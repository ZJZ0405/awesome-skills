#!/usr/bin/env python3
"""Sync reviewed upstream repositories into skills/.

The whitelist lives in upstreams.json. Every run resolves the configured track
to a commit, shallow-clones the repository with a sparse checkout, filters the
discovered skills through the license and portability gates, and copies the
result into skills/<name>/. A lock file records the commit and a SHA-256 for
every vendored file, so local edits are detected instead of silently replaced.

Standard library + git only. No GitHub API, no Node, no network beyond git.

Usage:
    python3 scripts/sync_upstreams.py --dry-run
    python3 scripts/sync_upstreams.py --upstream anthropic
    python3 scripts/sync_upstreams.py --prune
    python3 scripts/sync_upstreams.py --check

Exit codes: 0 okay (including skips), 1 fatal error or stale --check.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from skilllib import (
    MAX_DESCRIPTION_LEN,
    MAX_NAME_LEN,
    NAME_RE,
    load_skill,
    normalize_ws,
    repo_root,
)
from validate import check_links

SCHEMA_VERSION = 1

ALLOWED_LICENSES = frozenset(
    ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0")
)
ALLOWED_FIELDS = frozenset(
    ("name", "description", "license", "allowed-tools", "metadata", "version")
)
SKIP_DIRS = frozenset((".git", "node_modules", "__pycache__", ".venv", "dist"))
IGNORE_COPY = (
    ".git",
    "node_modules",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    ".venv",
    "dist",
    ".awesome-skills-skill",
)
HASH_IGNORE_FILES = frozenset((".DS_Store",))
HASH_IGNORE_DIRS = frozenset(("__pycache__", ".git"))

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SKILL_BYTES = 25 * 1024 * 1024
MAX_SKILL_MD_BYTES = 5 * 1024 * 1024
MAX_SKILL_MD_LINES = 500

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
SKILL_FIELDS = frozenset(
    (
        "id",
        "repo",
        "track",
        "license",
        "licenses",
        "licenseFile",
        "paths",
        "include",
        "exclude",
        "excludePaths",
        "rename",
        "enabled",
    )
)

SECURITY_PATTERNS = (
    ("curl/wget piped to a shell", re.compile(r"\b(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b")),
    ("eval with expansion", re.compile(r"\beval\s+[\"'$`]")),
    ("recursive rm", re.compile(r"\brm\s+-[A-Za-z]*[rR][A-Za-z]*")),
    ("world-writable chmod", re.compile(r"\bchmod\s+(?:-R\s+)?777\b")),
    ("base64 decoded into a shell", re.compile(r"\bbase64\s+(?:-d|--decode)\b[^\n|]*\|\s*(?:sh|bash)")),
    ("private key material", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)


class SyncError(Exception):
    """Fatal problem: nothing is written."""


class SkipSkill(Exception):
    """The skill is not portable or not licensed; keep going."""


@dataclass
class Upstream:
    id: str
    repo: str
    track: str
    license: str
    licenses: Dict[str, str]
    license_file: str
    paths: List[str]
    include: List[str]
    exclude: List[str]
    exclude_paths: List[str]
    enabled: bool


@dataclass
class Candidate:
    upstream: Upstream
    name: str
    path: Path
    rel: str


@dataclass
class Plan:
    action: str  # added, updated, unchanged, skipped, pruned
    name: str
    upstream: str
    commit: str
    license: str = ""
    rel: str = ""
    files: Optional[Dict[str, str]] = None
    staged: Optional[Path] = None
    reason: str = ""


class Report:
    def __init__(self):
        self.added = []
        self.updated = []
        self.unchanged = []
        self.skipped = []
        self.orphaned = []
        self.pruned = []
        self.available = []
        self.notes = []
        self.security = []
        self.lock_changes = []
        self.errors = []

    def note(self, message):
        self.notes.append(message)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def read_json(path, label):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SyncError("cannot read %s: %s" % (label, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SyncError("%s is not valid JSON: %s" % (label, exc))


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_text_lf(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_if_changed(path, text):
    path = Path(path)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    write_text_lf(path, text)
    return True


def has_magic(value):
    return bool(glob.has_magic(value))


def md_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(directory):
    directory = Path(directory)
    files = {}
    for dirpath, dirnames, filenames in os.walk(str(directory)):
        dirnames[:] = sorted(
            name for name in dirnames if name not in HASH_IGNORE_DIRS
        )
        for name in sorted(filenames):
            if name in HASH_IGNORE_FILES or name.endswith(".pyc"):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            files[path.relative_to(directory).as_posix()] = sha256_file(path)
    return files


def remove_path(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(str(path))


def is_present(path):
    path = Path(path)
    return path.exists() or path.is_symlink()


def has_shebang(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def safe_rel_path(value, where, field, allow_dot=False):
    if not isinstance(value, str) or not value.strip():
        raise SyncError("%s.%s must be a non-empty string" % (where, field))
    value = value.strip()
    if "\\" in value:
        raise SyncError("%s.%s must use forward slashes" % (where, field))
    if value.startswith("/"):
        raise SyncError("%s.%s must be relative" % (where, field))
    if not SAFE_PATH_RE.match(value):
        raise SyncError(
            "%s.%s contains unsupported characters: %r" % (where, field, value)
        )
    if value != ".":
        value = "/".join(PurePosixPath(value).parts)
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise SyncError("%s.%s must not contain '..'" % (where, field))
    if value == ".":
        if allow_dot:
            return "."
        raise SyncError("%s.%s must point to a directory below the repo root" % (where, field))
    if parts and parts[0] == ".":
        value = "/".join(parts[1:])
    if not value or value == "..":
        raise SyncError("%s.%s is not a usable path" % (where, field))
    return value


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def parse_track(track):
    if not isinstance(track, str) or not track.strip():
        raise SyncError("track must be a branch, tag, or 40-character commit")
    track = track.strip()
    if SHA_RE.match(track):
        return "commit", track
    for kind in ("branch", "tag"):
        prefix = kind + ":"
        if not track.startswith(prefix):
            continue
        value = track[len(prefix):]
        if (
            not value
            or not REF_RE.match(value)
            or ".." in value
            or value.endswith(".")
            or value.endswith("/")
        ):
            raise SyncError("invalid %s name in track: %r" % (kind, track))
        return kind, value
    raise SyncError(
        "track must be branch:<name>, tag:<name>, or a 40-character commit: %r"
        % track
    )


def _string(item, key, where):
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyncError("%s.%s must be a non-empty string" % (where, key))
    return value.strip()


def _pattern_list(item, key, where, required):
    raw = item.get(key)
    if raw is None:
        if required:
            raise SyncError("%s.%s is required" % (where, key))
        return []
    if not isinstance(raw, list):
        raise SyncError("%s.%s must be a list" % (where, key))
    patterns = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise SyncError("%s.%s[%d] must be a non-empty string" % (where, key, index))
        value = value.strip()
        if "/" in value or "\\" in value or value in (".", "..") or " " in value:
            raise SyncError("%s.%s[%d] is not a skill name or glob: %r" % (where, key, index, value))
        if value not in patterns:
            patterns.append(value)
    return patterns


def _path_glob_list(item, key, where):
    raw = item.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SyncError("%s.%s must be a list" % (where, key))
    patterns = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise SyncError("%s.%s[%d] must be a non-empty string" % (where, key, index))
        value = value.strip()
        if "\\" in value:
            raise SyncError("%s.%s[%d] must use forward slashes" % (where, key, index))
        if value.startswith("/"):
            raise SyncError("%s.%s[%d] must be relative" % (where, key, index))
        parts = PurePosixPath(value).parts
        if ".." in parts:
            raise SyncError("%s.%s[%d] must not contain '..'" % (where, key, index))
        value = PurePosixPath(value).as_posix()
        if not parts or value in (".", ""):
            raise SyncError("%s.%s[%d] is not a usable path glob" % (where, key, index))
        if value not in patterns:
            patterns.append(value)
    return patterns


def load_config(root):
    path = root / "upstreams.json"
    data = read_json(path, "upstreams.json")
    if data is None:
        raise SyncError(
            "upstreams.json not found at %s; create it with an empty upstreams list"
            % path
        )
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise SyncError("upstreams.json must have schemaVersion: %d" % SCHEMA_VERSION)
    raw = data.get("upstreams")
    if not isinstance(raw, list):
        raise SyncError("upstreams.json: upstreams must be a list")

    seen_ids = set()
    explicit_names = {}
    upstreams = []
    for index, item in enumerate(raw):
        where = "upstreams[%d]" % index
        if not isinstance(item, dict):
            raise SyncError("%s must be an object" % where)
        unknown = sorted(set(item) - SKILL_FIELDS)
        if unknown:
            raise SyncError("%s has unknown field(s): %s" % (where, ", ".join(unknown)))

        upstream_id = _string(item, "id", where)
        if not NAME_RE.match(upstream_id) or len(upstream_id) > MAX_NAME_LEN:
            raise SyncError("%s.id is not a valid slug: %r" % (where, upstream_id))
        if upstream_id in seen_ids:
            raise SyncError("duplicate upstream id: %r" % upstream_id)
        seen_ids.add(upstream_id)

        repo = _string(item, "repo", where)
        if not REPO_RE.match(repo) or ".." in repo or repo.startswith("/"):
            raise SyncError("%s.repo must look like owner/name: %r" % (where, repo))

        track = _string(item, "track", where)
        parse_track(track)

        license_value = _string(item, "license", where)
        licenses = {}
        if license_value == "per-skill":
            raw_licenses = item.get("licenses")
            if not isinstance(raw_licenses, dict) or not raw_licenses:
                raise SyncError("%s.licenses is required when license is per-skill" % where)
            for name, spdx in raw_licenses.items():
                if not isinstance(name, str) or not NAME_RE.match(name):
                    raise SyncError("%s.licenses has an invalid skill name: %r" % (where, name))
                if not isinstance(spdx, str) or spdx not in ALLOWED_LICENSES:
                    raise SyncError(
                        "%s.licenses[%r] is not an accepted SPDX license (%s)"
                        % (where, name, ", ".join(sorted(ALLOWED_LICENSES)))
                    )
                licenses[name] = spdx
        else:
            if license_value not in ALLOWED_LICENSES:
                raise SyncError(
                    "%s.license %r is not accepted; use one of: %s"
                    % (where, license_value, ", ".join(sorted(ALLOWED_LICENSES)))
                )
            if item.get("licenses") not in (None, {}):
                raise SyncError("%s.licenses is only allowed with license: per-skill" % where)

        license_file = safe_rel_path(
            item.get("licenseFile"), where, "licenseFile"
        )

        raw_paths = item.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise SyncError("%s.paths must be a non-empty list" % where)
        paths = []
        for index2, value in enumerate(raw_paths):
            path = safe_rel_path(value, where, "paths[%d]" % index2, allow_dot=True)
            if path not in paths:
                paths.append(path)
        if "." in paths and len(paths) > 1:
            raise SyncError("%s.paths cannot combine '.' with other paths" % where)

        include = _pattern_list(item, "include", where, required=True)
        exclude = _pattern_list(item, "exclude", where, required=False)

        rename = item.get("rename")
        if rename is not None:
            if not isinstance(rename, dict):
                raise SyncError("%s.rename must be an object" % where)
            if rename:
                raise SyncError(
                    "%s.rename is not implemented yet; add the skill under its "
                    "upstream name or use scripts/import.py" % where
                )

        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise SyncError("%s.enabled must be true or false" % where)

        exclude_paths = _path_glob_list(item, "excludePaths", where)

        if license_value == "per-skill":
            for name in include:
                if has_magic(name):
                    continue
                if name in exclude:
                    continue
                if name not in licenses:
                    raise SyncError(
                        "%s.include names %r but licenses has no entry for it"
                        % (where, name)
                    )

        for name in include:
            if has_magic(name):
                continue
            if name in explicit_names:
                raise SyncError(
                    "skill %r is included by both %r and %r"
                    % (name, explicit_names[name], upstream_id)
                )
            explicit_names[name] = upstream_id

        upstreams.append(
            Upstream(
                id=upstream_id,
                repo=repo,
                track=track,
                license=license_value,
                licenses=licenses,
                license_file=license_file,
                paths=paths,
                include=include,
                exclude=exclude,
                exclude_paths=exclude_paths,
                enabled=enabled,
            )
        )

    return upstreams


# ---------------------------------------------------------------------------
# Lock and sources
# ---------------------------------------------------------------------------


def empty_lock():
    return {"schemaVersion": SCHEMA_VERSION, "upstreams": {}}


def load_lock(root):
    data = read_json(root / "upstreams.lock.json", "upstreams.lock.json")
    if data is None:
        return empty_lock()
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise SyncError("upstreams.lock.json must have schemaVersion: %d" % SCHEMA_VERSION)
    if not isinstance(data.get("upstreams"), dict):
        raise SyncError("upstreams.lock.json: upstreams must be an object")

    owners = {}
    for upstream_id, entry in data["upstreams"].items():
        if not isinstance(entry, dict):
            raise SyncError("upstreams.lock.json: %r must be an object" % upstream_id)
        if not isinstance(entry.get("repo"), str) or not isinstance(entry.get("track"), str):
            raise SyncError("upstreams.lock.json: %r is missing repo/track" % upstream_id)
        if not isinstance(entry.get("commit"), str) or not SHA_RE.match(entry["commit"]):
            raise SyncError(
                "upstreams.lock.json: %r has an invalid commit" % upstream_id
            )
        skills = entry.get("skills")
        if not isinstance(skills, dict):
            raise SyncError("upstreams.lock.json: %r.skills must be an object" % upstream_id)
        for name, skill in skills.items():
            if name in owners:
                raise SyncError(
                    "upstreams.lock.json: skill %r is listed under both %r and %r"
                    % (name, owners[name], upstream_id)
                )
            owners[name] = upstream_id
            if not isinstance(name, str) or not NAME_RE.match(name):
                raise SyncError(
                    "upstreams.lock.json: %r has an invalid skill name %r"
                    % (upstream_id, name)
                )
            if not isinstance(skill, dict):
                raise SyncError("upstreams.lock.json: %r/%r must be an object" % (upstream_id, name))
            if not isinstance(skill.get("path"), str) or not isinstance(skill.get("license"), str):
                raise SyncError(
                    "upstreams.lock.json: %r/%r is missing path/license"
                    % (upstream_id, name)
                )
            files = skill.get("files")
            if not isinstance(files, dict):
                raise SyncError(
                    "upstreams.lock.json: %r/%r.files must be an object"
                    % (upstream_id, name)
                )
            for rel, digest in files.items():
                if not isinstance(rel, str) or not isinstance(digest, str) or not DIGEST_RE.match(digest):
                    raise SyncError(
                        "upstreams.lock.json: %r/%r has an invalid file hash for %r"
                        % (upstream_id, name, rel)
                    )
    return data


def load_sources(root):
    data = read_json(root / "sources.json", "sources.json")
    if data is None:
        return {"schemaVersion": SCHEMA_VERSION, "skills": {}}
    if not isinstance(data, dict):
        raise SyncError("sources.json must be an object")
    if not isinstance(data.get("skills"), dict):
        raise SyncError("sources.json: skills must be an object")
    return data


def canonical_lock(data):
    out = {"schemaVersion": SCHEMA_VERSION, "upstreams": {}}
    for upstream_id in sorted(data["upstreams"]):
        entry = data["upstreams"][upstream_id]
        skills = {}
        for name in sorted(entry["skills"]):
            skill = entry["skills"][name]
            files = {}
            for rel in sorted(skill["files"]):
                files[rel] = skill["files"][rel]
            skills[name] = {
                "path": skill["path"],
                "license": skill["license"],
                "files": files,
            }
        out["upstreams"][upstream_id] = {
            "repo": entry["repo"],
            "track": entry["track"],
            "commit": entry["commit"],
            "skills": skills,
        }
    return out


def canonical_sources(data):
    out = deepcopy(data)
    skills = {}
    for name in sorted(data.get("skills", {})):
        skills[name] = data["skills"][name]
    out["skills"] = skills
    return out


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def git_env():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env["LC_ALL"] = "C"
    return env


def run_git(args, cwd=None):
    command = ["git"] + list(args)
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=git_env(),
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else "exit %d" % proc.returncode
        raise SyncError("git %s failed: %s" % (args[0], message))
    return proc.stdout


def repo_url(repo, base):
    return "%s/%s.git" % (base.rstrip("/"), repo)


def resolve_commit(url, track):
    kind, value = parse_track(track)
    if kind == "commit":
        return value
    ref = "refs/heads/%s" % value if kind == "branch" else "refs/tags/%s" % value
    output = run_git(["ls-remote", url, ref, ref + "^{}"])
    exact = None
    peeled = None
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        commit, name = parts
        if name == ref + "^{}":
            peeled = commit
        elif name == ref:
            exact = commit
    commit = peeled or exact
    if not commit or not SHA_RE.match(commit):
        raise SyncError(
            "%s: %s not found in %s" % (track, ref, url)
        )
    return commit


def clone_sparse(url, track, paths, extra_files, dest):
    kind, value = parse_track(track)
    if kind == "commit":
        ref = value
    elif kind == "branch":
        ref = "refs/heads/%s" % value
    else:
        ref = "refs/tags/%s" % value

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-q", str(dest)])
    run_git(["-C", str(dest), "remote", "add", "origin", url])
    run_git(
        ["-C", str(dest), "fetch", "--depth", "1", "--no-tags", "origin", ref]
    )

    if "." not in paths:
        patterns = []
        for path in paths:
            patterns.append("/" + path.strip("/") + "/")
        for path in extra_files:
            patterns.append("/" + path.strip("/"))
        info_dir = dest / ".git" / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        write_text_lf(info_dir / "sparse-checkout", "\n".join(patterns) + "\n")
        run_git(["-C", str(dest), "config", "core.sparseCheckout", "true"])

    run_git(["-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"])
    commit = run_git(["-C", str(dest), "rev-parse", "HEAD"]).strip()
    if not SHA_RE.match(commit):
        raise SyncError("could not read HEAD after cloning %s" % url)
    return commit


# ---------------------------------------------------------------------------
# Discovery and selection
# ---------------------------------------------------------------------------


def discover_skills(repo, upstream, report):
    repo = Path(repo)
    found = {}
    existing = 0
    for rel_dir in upstream.paths:
        base = repo if rel_dir == "." else repo / rel_dir
        if not base.is_dir():
            report.note(
                "%s: path %r does not exist in the upstream checkout"
                % (upstream.id, rel_dir)
            )
            continue
        existing += 1
        for dirpath, dirnames, filenames in os.walk(str(base), followlinks=False):
            current = Path(dirpath)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in SKIP_DIRS and not (current / name).is_symlink()
            )
            if "SKILL.md" not in filenames:
                continue
            rel = current.relative_to(repo).as_posix()
            if rel == ".":
                continue
            name = current.name
            if not NAME_RE.match(name) or len(name) > MAX_NAME_LEN:
                report.note(
                    "%s: ignoring %s (invalid folder name for a skill)"
                    % (upstream.id, rel)
                )
                dirnames[:] = []
                continue
            if name in found:
                if found[name].path == current:
                    continue
                raise SyncError(
                    "%s: duplicate skill folder name %r (%s and %s)"
                    % (upstream.id, name, found[name].rel, rel)
                )
            found[name] = Candidate(upstream, name, current, rel)
            # A SKILL.md nested inside a skill is an example, not another skill.
            dirnames[:] = []

    if existing == 0:
        raise SyncError(
            "%s: none of the configured paths exist in the checkout (%s)"
            % (upstream.id, ", ".join(upstream.paths))
        )
    return found


def select_skills(upstream, discovered, report):
    selected = {}
    for name in sorted(discovered):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in upstream.include):
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in upstream.exclude):
                continue
            selected[name] = discovered[name]

    for pattern in upstream.include:
        if has_magic(pattern) or pattern in upstream.exclude:
            continue
        if pattern not in discovered:
            report.note(
                "%s: included skill %r was not found upstream" % (upstream.id, pattern)
            )

    available = sorted(set(discovered) - set(selected))
    if available:
        report.available.append((upstream.id, available))
    return selected


# ---------------------------------------------------------------------------
# Staging one skill
# ---------------------------------------------------------------------------


def with_license(text, spdx):
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkipSkill("SKILL.md has no YAML frontmatter")
    close = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            close = index
            break
    if close is None:
        raise SkipSkill("SKILL.md frontmatter is not terminated")
    replaced = False
    for index in range(1, close):
        if re.match(r"^license\s*:", lines[index]):
            lines[index] = "license: %s" % spdx
            replaced = True
    if not replaced:
        lines.insert(close, "license: %s" % spdx)
    return "\n".join(lines) + "\n"


def is_excluded_path(rel, patterns):
    """Match a skill-relative POSIX path or any of its parents against globs."""
    if not patterns:
        return False
    parts = PurePosixPath(rel).parts
    for end in range(1, len(parts) + 1):
        candidate = "/".join(parts[:end])
        for pattern in patterns:
            if fnmatch.fnmatchcase(candidate, pattern):
                return True
    return False


def copy_source(source, dest, exclude_paths=()):
    source = Path(source)
    dest = Path(dest)
    total = 0
    for dirpath, dirnames, filenames in os.walk(str(source), followlinks=False):
        current = Path(dirpath)
        kept = []
        for name in sorted(dirnames):
            if name in SKIP_DIRS:
                continue
            path = current / name
            rel = path.relative_to(source).as_posix()
            if is_excluded_path(rel, exclude_paths):
                continue
            if path.is_symlink():
                raise SkipSkill("contains a symlink: %s" % rel)
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = current / name
            rel = path.relative_to(source).as_posix()
            if is_excluded_path(rel, exclude_paths):
                continue
            if path.is_symlink():
                raise SkipSkill("contains a symlink: %s" % rel)
            if name in HASH_IGNORE_FILES or name.endswith(".pyc"):
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise SkipSkill("cannot stat %s: %s" % (rel, exc))
            if size > MAX_FILE_BYTES:
                raise SkipSkill(
                    "%s is larger than %d MB" % (rel, MAX_FILE_BYTES // 1048576)
                )
            total += size
            if total > MAX_SKILL_BYTES:
                raise SkipSkill(
                    "skill folder is larger than %d MB" % (MAX_SKILL_BYTES // 1048576)
                )

    base_ignore = shutil.ignore_patterns(*IGNORE_COPY)

    def ignore(dirpath, names):
        ignored = set(base_ignore(dirpath, names))
        for name in names:
            rel = (Path(dirpath) / name).relative_to(source).as_posix()
            if is_excluded_path(rel, exclude_paths):
                ignored.add(name)
        return sorted(ignored)

    shutil.copytree(str(source), str(dest), ignore=ignore)


def is_inside(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def copy_license(source, dest):
    source = Path(source)
    dest = Path(dest)
    if not source.is_file() or source.is_symlink():
        raise SkipSkill("license file is missing: %s" % source.name)
    if source.stat().st_size == 0:
        raise SkipSkill("license file is empty: %s" % source.name)
    if source.resolve() == dest.resolve():
        return
    if dest.is_dir() and not dest.is_symlink():
        raise SkipSkill(
            "license target conflicts with a directory: %s" % dest.name
        )
    if dest.exists() or dest.is_symlink():
        remove_path(dest)
    shutil.copy2(str(source), str(dest))


def normalize_scripts(staged):
    scripts = Path(staged) / "scripts"
    if not scripts.is_dir():
        return
    for path in scripts.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        needs_exec = path.suffix in (".sh", ".bash") or has_shebang(path)
        if not needs_exec:
            continue
        mode = path.stat().st_mode
        if not mode & 0o111:
            path.chmod(mode | 0o111)


def validate_staged(staged, candidate, expected_license):
    skill = load_skill(staged)
    if skill.error:
        raise SkipSkill(skill.error)
    frontmatter = skill.frontmatter
    extra = sorted(set(frontmatter) - ALLOWED_FIELDS)
    if extra:
        raise SkipSkill(
            "frontmatter field(s) are not portable: %s" % ", ".join(extra)
        )
    if not isinstance(frontmatter.get("name"), str):
        raise SkipSkill("frontmatter name is missing")
    if not isinstance(frontmatter.get("description"), str):
        raise SkipSkill("frontmatter description is missing")
    if len(skill.name) > MAX_NAME_LEN or not NAME_RE.match(skill.name):
        raise SkipSkill("invalid skill name: %r" % skill.name)
    if skill.name != candidate.name:
        raise SkipSkill(
            "frontmatter name %r does not match folder %r"
            % (skill.name, candidate.name)
        )
    description = normalize_ws(skill.description)
    if not description:
        raise SkipSkill("description is empty")
    if len(description) > MAX_DESCRIPTION_LEN:
        raise SkipSkill(
            "description is %d characters (max %d)"
            % (len(description), MAX_DESCRIPTION_LEN)
        )
    if "metadata" in frontmatter and not isinstance(frontmatter["metadata"], dict):
        raise SkipSkill("metadata must be a mapping")
    if skill.license != expected_license:
        raise SkipSkill(
            "license frontmatter is %r, expected %r"
            % (skill.license, expected_license)
        )
    lines = skill.text.count("\n") + 1
    if lines > MAX_SKILL_MD_LINES:
        raise SkipSkill(
            "SKILL.md is %d lines; the limit is %d" % (lines, MAX_SKILL_MD_LINES)
        )
    if skill.skill_md.stat().st_size > MAX_SKILL_MD_BYTES:
        raise SkipSkill("SKILL.md is larger than 5 MB")
    links = check_links(skill)
    if links:
        messages = [message for _level, message in links]
        raise SkipSkill("unportable link: %s" % "; ".join(messages[:3]))
    return skill


def scan_security(staged, report, upstream_id, name):
    found = 0
    for path in sorted(Path(staged).rglob("*")):
        if found >= 5:
            break
        if path.is_symlink() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            if size > 2 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        rel = path.relative_to(staged).as_posix()
        if b"\0" in data[:8192]:
            report.security.append(
                {
                    "upstream": upstream_id,
                    "name": name,
                    "file": rel,
                    "pattern": "binary file (review before trusting)",
                }
            )
            found += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECURITY_PATTERNS:
            if pattern.search(text):
                report.security.append(
                    {
                        "upstream": upstream_id,
                        "name": name,
                        "file": rel,
                        "pattern": label,
                    }
                )
                found += 1
                if found >= 5:
                    break


def normalize_text_files(staged):
    """Convert CRLF/CR text files to LF, matching the repo's eol=lf checkout."""
    for path in sorted(Path(staged).rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name in HASH_IGNORE_FILES or path.suffix == ".pyc":
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192] or b"\r" not in data:
            continue
        path.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def prepare_skill(candidate, expected_license, license_source, notice_sources, old, commit, stage_root, report):
    staged = Path(stage_root) / candidate.upstream.id / candidate.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        copy_source(candidate.path, staged, candidate.upstream.exclude_paths)
        license_name = PurePosixPath(candidate.upstream.license_file).name
        if not license_source.is_file() or license_source.is_symlink():
            raise SkipSkill("license file is missing: %s" % license_source.name)
        if license_source.stat().st_size == 0:
            raise SkipSkill("license file is empty: %s" % license_source.name)
        if not is_inside(license_source, candidate.path):
            copy_license(license_source, staged / license_name)
        for notice in notice_sources:
            if notice.is_file() and not notice.is_symlink():
                if not is_inside(notice, candidate.path):
                    copy_license(notice, staged / notice.name)
        normalize_text_files(staged)
        md = staged / "SKILL.md"
        try:
            text = md.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkipSkill("SKILL.md is not valid UTF-8: %s" % exc)
        text = with_license(text, expected_license)
        write_text_lf(md, text)
        normalize_scripts(staged)
        validate_staged(staged, candidate, expected_license)
        files = hash_tree(staged)
        scan_security(staged, report, candidate.upstream.id, candidate.name)
    except SkipSkill as exc:
        return Plan(
            action="skipped",
            name=candidate.name,
            upstream=candidate.upstream.id,
            commit=commit,
            reason=str(exc),
        )

    if old is None:
        action = "added"
    elif files != old["files"]:
        action = "updated"
    else:
        action = "unchanged"
    return Plan(
        action=action,
        name=candidate.name,
        upstream=candidate.upstream.id,
        commit=commit,
        license=expected_license,
        rel=candidate.rel,
        files=files,
        staged=staged,
    )


# ---------------------------------------------------------------------------
# Drift and orphans
# ---------------------------------------------------------------------------


def drift_reason(dest, files):
    dest = Path(dest)
    if dest.is_symlink():
        return "path is a symlink"
    if not dest.exists():
        return "path is missing"
    if not dest.is_dir():
        return "path is not a directory"
    actual = hash_tree(dest)
    if actual == files:
        return None
    missing = sorted(set(files) - set(actual))
    extra = sorted(set(actual) - set(files))
    details = []
    if missing:
        details.append("missing: %s" % ", ".join(missing[:3]))
    if extra:
        details.append("extra: %s" % ", ".join(extra[:3]))
    if not details:
        details.append("contents changed")
    return "; ".join(details)


def handle_orphans(upstream_id, old_skills, selected_names, skills_dir, plans, report, prune, reason_prefix):
    retained = {}
    for name in sorted(old_skills):
        if name in selected_names:
            continue
        old = old_skills[name]
        dest = skills_dir / name
        if prune:
            reason = drift_reason(dest, old["files"])
            if reason is None:
                plans.append(
                    Plan(
                        action="pruned",
                        name=name,
                        upstream=upstream_id,
                        commit="",
                        license=old.get("license", ""),
                        rel=old.get("path", ""),
                        files=old.get("files", {}),
                    )
                )
                report.pruned.append(
                    {
                        "name": name,
                        "upstream": upstream_id,
                        "license": old.get("license", ""),
                        "reason": "removed with --prune",
                    }
                )
                continue
            if not dest.exists() and not dest.is_symlink():
                # The lock entry is stale but there is nothing to delete.
                plans.append(
                    Plan(
                        action="pruned",
                        name=name,
                        upstream=upstream_id,
                        commit="",
                        license=old.get("license", ""),
                        rel=old.get("path", ""),
                        files=old.get("files", {}),
                    )
                )
                report.pruned.append(
                    {
                        "name": name,
                        "upstream": upstream_id,
                        "license": old.get("license", ""),
                        "reason": "lock entry removed (path was already missing)",
                    }
                )
                continue
            report.orphaned.append(
                {
                    "name": name,
                    "upstream": upstream_id,
                    "license": old.get("license", ""),
                    "reason": "not pruned: %s" % reason,
                }
            )
        else:
            report.orphaned.append(
                {
                    "name": name,
                    "upstream": upstream_id,
                    "license": old.get("license", ""),
                    "reason": "%s (use --prune to remove)" % reason_prefix,
                }
            )
        retained[name] = old
    return retained


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def install_tree(staged, dest):
    staged = Path(staged)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = dest.with_name(
        ".awesome-sync-backup-%s-%d" % (dest.name, os.getpid())
    )
    remove_path(backup)
    moved = False
    if is_present(dest):
        os.replace(str(dest), str(backup))
        moved = True
    try:
        shutil.copytree(str(staged), str(dest))
    except BaseException:
        remove_path(dest)
        if moved and is_present(backup):
            os.replace(str(backup), str(dest))
        raise
    remove_path(backup)


def prune_tree(dest, skills_dir):
    dest = Path(dest)
    skills_dir = Path(skills_dir)
    if dest.parent.resolve() != skills_dir.resolve():
        raise SyncError("refusing to remove a path outside skills/: %s" % dest)
    if dest.is_symlink():
        raise SyncError("refusing to remove a symlink: %s" % dest)
    if dest.is_dir():
        shutil.rmtree(str(dest))


# ---------------------------------------------------------------------------
# Summary and output
# ---------------------------------------------------------------------------


def print_item(action, item):
    line = "  %-10s %-32s %-12s" % (action, item.get("name", ""), item.get("upstream", ""))
    if item.get("license"):
        line += " %s" % item["license"]
    if item.get("reason"):
        line += "  (%s)" % item["reason"]
    print(line.rstrip())


def print_report(report, dry_run):
    prefix = "would-" if dry_run else ""
    for item in report.added:
        print_item(prefix + "add", item)
    for item in report.updated:
        print_item(prefix + "update", item)
    for item in report.unchanged:
        print_item("unchanged", item)
    for item in report.skipped:
        print_item("skipped", item)
    for item in report.orphaned:
        print_item("orphaned", item)
    for item in report.pruned:
        print_item(prefix + "prune", item)
    for change in report.lock_changes:
        print("  %s" % change)
    for message in report.notes[:20]:
        print("  note: %s" % message)
    if len(report.notes) > 20:
        print("  note: ... %d more" % (len(report.notes) - 20))
    for item in report.security:
        print(
            "  security: %s/%s (%s)"
            % (item["name"], item["file"], item["pattern"])
        )
    if report.errors:
        for message in report.errors:
            print("error: %s" % message, file=sys.stderr)
    print(
        "%d added, %d updated, %d unchanged, %d skipped, %d orphaned, %d pruned"
        % (
            len(report.added),
            len(report.updated),
            len(report.unchanged),
            len(report.skipped),
            len(report.orphaned),
            len(report.pruned),
        )
    )


def render_table(rows, columns):
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join([":---"] * len(columns)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_cell(value) for value in row) + " |")
    return lines


def render_summary(report, dry_run):
    lines = ["# Upstream skills sync", ""]
    if dry_run:
        lines.append("_Dry run: no files were written._")
        lines.append("")
    lines.append(
        "**%d added, %d updated, %d unchanged, %d skipped, %d orphaned, %d pruned.**"
        % (
            len(report.added),
            len(report.updated),
            len(report.unchanged),
            len(report.skipped),
            len(report.orphaned),
            len(report.pruned),
        )
    )
    lines.append("")

    if report.lock_changes:
        lines.append("## Lock")
        lines.append("")
        for change in report.lock_changes:
            lines.append("- %s" % change)
        lines.append("")

    for title, items in (
        ("Added", report.added),
        ("Updated", report.updated),
        ("Pruned", report.pruned),
    ):
        if not items:
            continue
        lines.append("## %s" % title)
        lines.append("")
        lines.extend(
            render_table(
                [
                    (
                        item.get("name", ""),
                        item.get("upstream", ""),
                        item.get("license", ""),
                        item.get("commit", "")[:12],
                    )
                    for item in items
                ],
                ["Skill", "Upstream", "License", "Commit"],
            )
        )
        lines.append("")

    if report.skipped:
        lines.append("## Skipped")
        lines.append("")
        lines.extend(
            render_table(
                [
                    (item.get("name", ""), item.get("upstream", ""), item.get("reason", ""))
                    for item in report.skipped
                ],
                ["Skill", "Upstream", "Reason"],
            )
        )
        lines.append("")

    if report.orphaned:
        lines.append("## Orphaned but kept")
        lines.append("")
        lines.extend(
            render_table(
                [
                    (item.get("name", ""), item.get("upstream", ""), item.get("reason", ""))
                    for item in report.orphaned
                ],
                ["Skill", "Upstream", "Reason"],
            )
        )
        lines.append("")

    if report.unchanged:
        lines.append("## Unchanged")
        lines.append("")
        lines.append(
            ", ".join("`%s`" % item["name"] for item in report.unchanged)
        )
        lines.append("")

    if report.available:
        lines.append("## Available but not imported")
        lines.append("")
        for upstream_id, names in report.available:
            lines.append("- `%s`: %s" % (upstream_id, ", ".join("`%s`" % name for name in names)))
        lines.append("")

    if report.security:
        lines.append("## Security notes (review before merging)")
        lines.append("")
        lines.extend(
            render_table(
                [
                    (item["name"], item["file"], item["pattern"])
                    for item in report.security
                ],
                ["Skill", "File", "Pattern"],
            )
        )
        lines.append("")

    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for message in report.notes:
            lines.append("- %s" % message)
        lines.append("")

    if report.errors:
        lines.append("## Errors")
        lines.append("")
        for message in report.errors:
            lines.append("- %s" % message)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Check mode
# ---------------------------------------------------------------------------


def run_check(args, config, lock, report):
    targets = [up for up in config if args.upstream is None or up.id == args.upstream]
    if not targets:
        raise SyncError("no configured upstream matches --upstream %r" % args.upstream)
    base = args.repo_base
    stale = False
    for upstream in targets:
        if not upstream.enabled:
            report.note("%s is disabled; skipped" % upstream.id)
            continue
        if not upstream.include:
            report.note("%s has an empty include list; skipped" % upstream.id)
            continue
        entry = lock["upstreams"].get(upstream.id, {})
        old = entry.get("commit")
        commit = resolve_commit(repo_url(upstream.repo, base), upstream.track)
        report.lock_changes.append(
            "%s: %s -> %s" % (upstream.id, (old or "missing")[:12], commit[:12])
        )
        if (
            old != commit
            or entry.get("repo") != upstream.repo
            or entry.get("track") != upstream.track
        ):
            stale = True
    if stale:
        print("upstreams are stale; run scripts/sync_upstreams.py", file=sys.stderr)
        return 1
    print("upstreams are up to date")
    return 0


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------


def run_sync(args, root, config, lock, sources, report):
    skills_dir = root / "skills"
    base = args.repo_base
    plans = []
    fatal = []
    configured = {up.id for up in config}
    name_owner = {}
    for upstream_id, entry in lock["upstreams"].items():
        for name in entry["skills"]:
            name_owner[name] = upstream_id
    planned_names = {}

    targets = [up for up in config if args.upstream is None or up.id == args.upstream]
    if args.upstream and not targets:
        raise SyncError("no configured upstream matches --upstream %r" % args.upstream)
    work = Path(tempfile.mkdtemp(prefix="awesome-skills-sync-"))

    try:
        for upstream in targets:
            old_entry = lock["upstreams"].get(upstream.id)
            old_skills = old_entry["skills"] if old_entry else {}
            print("sync: %s (%s) %s" % (upstream.id, upstream.repo, upstream.track))

            if not upstream.enabled and not args.prune:
                report.note(
                    "%s is disabled; %d skill(s) left in place"
                    % (upstream.id, len(old_skills))
                )
                continue
            if not upstream.include and not args.prune:
                report.note(
                    "%s has an empty include list; %d skill(s) left in place"
                    % (upstream.id, len(old_skills))
                )
                continue

            if not upstream.enabled:
                retained = handle_orphans(
                    upstream.id,
                    old_skills,
                    set(),
                    skills_dir,
                    plans,
                    report,
                    True,
                    "upstream disabled",
                )
                if retained and old_entry is not None:
                    old_entry = deepcopy(old_entry)
                    old_entry["skills"] = retained
                    lock["upstreams"][upstream.id] = old_entry
                elif not retained:
                    lock["upstreams"].pop(upstream.id, None)
                continue

            if not upstream.include:
                retained = handle_orphans(
                    upstream.id,
                    old_skills,
                    set(),
                    skills_dir,
                    plans,
                    report,
                    args.prune,
                    "no longer selected",
                )
                if retained and old_entry is not None:
                    old_entry = deepcopy(old_entry)
                    old_entry["skills"] = retained
                    lock["upstreams"][upstream.id] = old_entry
                elif not retained:
                    lock["upstreams"].pop(upstream.id, None)
                continue

            url = repo_url(upstream.repo, base)
            resolved = resolve_commit(url, upstream.track)
            extra = []
            if upstream.license != "per-skill":
                extra.append(upstream.license_file)
                extra.extend(["NOTICE", "NOTICE.txt"])
            clone_dir = work / "clone" / upstream.id
            commit = clone_sparse(url, upstream.track, upstream.paths, extra, clone_dir)
            if commit != resolved:
                report.note(
                    "%s: track moved during sync (%s -> %s); using the checkout"
                    % (upstream.id, resolved[:12], commit[:12])
                )
            old_commit = old_entry["commit"] if old_entry else None
            if old_commit != commit:
                report.lock_changes.append(
                    "%s: %s -> %s"
                    % (upstream.id, (old_commit or "missing")[:12], commit[:12])
                )
            else:
                report.note("%s: up to date at %s" % (upstream.id, commit[:12]))

            discovered = discover_skills(clone_dir, upstream, report)
            selected = select_skills(upstream, discovered, report)
            selected_names = set(selected)

            # Drift is fatal for every selected skill we already manage.
            drift = {}
            for name in sorted(selected_names):
                old = old_skills.get(name)
                if not old:
                    continue
                reason = drift_reason(skills_dir / name, old["files"])
                if reason:
                    drift[name] = reason
                    if not args.force:
                        fatal.append(
                            "skills/%s: %s; commit or discard the local change, "
                            "or re-run with --force" % (name, reason)
                        )

            skill_entries = {}
            retained = {}
            for name in sorted(selected_names):
                candidate = selected[name]
                old = old_skills.get(name)
                owner = name_owner.get(name)
                if owner and owner != upstream.id:
                    fatal.append(
                        "skill %r is already locked to upstream %r; prune it first"
                        % (name, owner)
                    )
                    if old:
                        skill_entries[name] = old
                    continue
                if name in planned_names and planned_names[name] != upstream.id:
                    fatal.append(
                        "skill %r is selected by both %r and %r"
                        % (name, planned_names[name], upstream.id)
                    )
                    continue
                if old is None and is_present(skills_dir / name):
                    fatal.append(
                        "skills/%s exists but is not managed by an upstream; "
                        "move it or add it to sources.json manually" % name
                    )
                    continue
                if old is None:
                    source_entry = sources.get("skills", {}).get(name)
                    if isinstance(source_entry, dict) and source_entry.get("source") == "original":
                        fatal.append(
                            "skill %r is declared as original in sources.json; "
                            "rename the upstream skill or drop it from include" % name
                        )
                        continue

                if old and name in drift and not args.force:
                    skill_entries[name] = old
                    continue

                if upstream.license == "per-skill":
                    expected_license = upstream.licenses.get(name)
                    if not expected_license:
                        plan = Plan(
                            action="skipped",
                            name=name,
                            upstream=upstream.id,
                            commit=commit,
                            reason="no licenses entry for this skill",
                        )
                        report.skipped.append(
                            {
                                "name": name,
                                "upstream": upstream.id,
                                "license": "",
                                "reason": plan.reason,
                            }
                        )
                        if old:
                            skill_entries[name] = old
                        continue
                else:
                    expected_license = upstream.license

                license_source = (
                    candidate.path / upstream.license_file
                    if upstream.license == "per-skill"
                    else clone_dir / upstream.license_file
                )
                notice_dir = license_source.parent
                notice_sources = [notice_dir / "NOTICE", notice_dir / "NOTICE.txt"]

                plan = prepare_skill(
                    candidate,
                    expected_license,
                    license_source,
                    notice_sources,
                    old,
                    commit,
                    work / "stage",
                    report,
                )
                plans.append(plan)
                forced_drift = old is not None and name in drift and args.force
                if forced_drift and plan.action == "unchanged":
                    # --force must repair a modified or missing local copy even
                    # when the upstream content itself did not change.
                    plan.action = "updated"
                if forced_drift and plan.action == "skipped":
                    report.note(
                        "%s: local drift could not be repaired because the new "
                        "upstream version was skipped (%s)" % (name, plan.reason)
                    )
                if plan.action == "skipped":
                    report.skipped.append(
                        {
                            "name": name,
                            "upstream": upstream.id,
                            "license": expected_license,
                            "reason": plan.reason,
                        }
                    )
                    if old:
                        skill_entries[name] = old
                    continue
                planned_names[name] = upstream.id
                skill_entries[name] = {
                    "path": plan.rel,
                    "license": plan.license,
                    "files": plan.files,
                }
                if plan.action == "added":
                    report.added.append(
                        {
                            "name": name,
                            "upstream": upstream.id,
                            "license": plan.license,
                            "commit": commit,
                        }
                    )
                elif plan.action == "updated":
                    report.updated.append(
                        {
                            "name": name,
                            "upstream": upstream.id,
                            "license": plan.license,
                            "commit": commit,
                        }
                    )
                else:
                    report.unchanged.append(
                        {
                            "name": name,
                            "upstream": upstream.id,
                            "license": plan.license,
                            "commit": commit,
                        }
                    )

            # Anything locked but no longer selected is orphaned.
            for name in sorted(old_skills):
                if name in selected_names:
                    continue
                orphan_reason = drift_reason(
                    skills_dir / name, old_skills[name]["files"]
                )
                if args.prune:
                    if orphan_reason is None or not is_present(skills_dir / name):
                        plans.append(
                            Plan(
                                action="pruned",
                                name=name,
                                upstream=upstream.id,
                                commit="",
                                license=old_skills[name].get("license", ""),
                                rel=old_skills[name].get("path", ""),
                                files=old_skills[name].get("files", {}),
                            )
                        )
                        report.pruned.append(
                            {
                                "name": name,
                                "upstream": upstream.id,
                                "license": old_skills[name].get("license", ""),
                                "reason": "no longer selected",
                            }
                        )
                        continue
                    report.orphaned.append(
                        {
                            "name": name,
                            "upstream": upstream.id,
                            "license": old_skills[name].get("license", ""),
                            "reason": "not pruned: %s" % orphan_reason,
                        }
                    )
                else:
                    report.orphaned.append(
                        {
                            "name": name,
                            "upstream": upstream.id,
                            "license": old_skills[name].get("license", ""),
                            "reason": "no longer selected (use --prune to remove)",
                        }
                    )
                retained[name] = old_skills[name]

            skill_entries.update(retained)
            lock["upstreams"][upstream.id] = {
                "repo": upstream.repo,
                "track": upstream.track,
                "commit": commit,
                "skills": skill_entries,
            }

        # Lock entries for upstreams that were removed from the config.
        if args.upstream is None:
            for upstream_id in list(lock["upstreams"]):
                if upstream_id in configured:
                    continue
                entry = lock["upstreams"][upstream_id]
                retained = handle_orphans(
                    upstream_id,
                    entry["skills"],
                    set(),
                    skills_dir,
                    plans,
                    report,
                    args.prune,
                    "upstream removed from upstreams.json",
                )
                if retained:
                    entry["skills"] = retained
                else:
                    lock["upstreams"].pop(upstream_id, None)

        # Persist the plan (never in dry-run, never with fatal errors).
        if fatal:
            report.errors.extend(fatal)
            print_report(report, args.dry_run)
            return 1

        if not args.dry_run:
            skills_dir.mkdir(parents=True, exist_ok=True)
            for plan in plans:
                if plan.action in ("added", "updated"):
                    install_tree(plan.staged, skills_dir / plan.name)
                elif plan.action == "pruned":
                    prune_tree(skills_dir / plan.name, skills_dir)

            for plan in plans:
                if plan.action in ("added", "updated", "unchanged"):
                    sources["skills"][plan.name] = {
                        "source": "https://github.com/%s/tree/%s/%s"
                        % (
                            next(up.repo for up in config if up.id == plan.upstream),
                            plan.commit,
                            plan.rel,
                        ),
                        "license": plan.license,
                        "upstream": plan.upstream,
                        "commit": plan.commit,
                        "path": plan.rel,
                    }
                elif plan.action == "pruned":
                    sources["skills"].pop(plan.name, None)

            lock_changed = write_if_changed(
                root / "upstreams.lock.json", dump_json(canonical_lock(lock))
            )
            sources_changed = write_if_changed(
                root / "sources.json", dump_json(canonical_sources(sources))
            )
            if lock_changed:
                print("wrote upstreams.lock.json")
            if sources_changed:
                print("wrote sources.json")
        else:
            print("dry run: no files written")

        print_report(report, args.dry_run)
        return 0
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sync_upstreams.py",
        description="Sync reviewed upstream skill repositories into skills/",
    )
    parser.add_argument("--upstream", metavar="ID", help="only process one upstream id")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    parser.add_argument("--force", action="store_true", help="overwrite local drift (commit first)")
    parser.add_argument("--prune", action="store_true", help="remove skills no longer selected upstream")
    parser.add_argument("--summary", metavar="PATH", help="write a Markdown summary (for the PR body)")
    parser.add_argument("--check", action="store_true", help="only compare upstream commits with the lock")
    parser.add_argument("--root", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument(
        "--repo-base",
        metavar="URL",
        default=os.environ.get("AWESOME_SKILLS_REPO_BASE", "https://github.com"),
        help="git base URL (default: https://github.com; for tests and forks)",
    )
    args = parser.parse_args(argv)

    if args.check and (args.dry_run or args.force or args.prune):
        parser.error("--check cannot be combined with --dry-run, --force, or --prune")

    root = Path(args.root).expanduser().resolve() if args.root else repo_root()
    report = Report()
    code = 0
    try:
        config = load_config(root)
        lock = load_lock(root)
        if args.check:
            code = run_check(args, config, lock, report)
        else:
            sources = load_sources(root)
            code = run_sync(args, root, config, lock, sources, report)
    except SyncError as exc:
        report.errors.append(str(exc))
        print("error: %s" % exc, file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        code = 130

    if args.summary:
        try:
            write_text_lf(Path(args.summary), render_summary(report, args.dry_run))
        except OSError as exc:
            print("error: cannot write summary: %s" % exc, file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
