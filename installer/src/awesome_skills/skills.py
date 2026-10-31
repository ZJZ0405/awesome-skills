"""Skills: discovery, the shared hub, and per-agent mirrors.

A skill is a folder with ``SKILL.md`` at its root; every other file in
the folder is part of it. Two install shapes exist, matching the shell
installer:

* **hub**: the real copy under ``~/.agents/skills`` (or the project's
  ``.agents/skills``), stamped with ``.awesome-skills-skill``;
* **mirror**: a symlink to the hub copy (default) or a copy of it, in
  each agent's skills directory.

Safety rules encoded here:

* content hashes decide ``unchanged``, so no timestamp ever makes a
  re-run rewrite a tree;
* a destination that is not ours is never overwritten without ``force``,
  and a forced replacement is moved to the backup directory first;
* removal verifies ownership again — a same-named folder the user made
  is left alone even if the state manifest claims the name;
* the marker is deterministic (no timestamp) because the shell
  installer's timestamped marker made every re-install look like a
  change.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

from . import shared
from .shared import is_within
from .types import MODE_COPY, MODE_LINK, FileResult, SkillError, SkillSpec

SKILL_FILE = "SKILL.md"
MARKER = ".awesome-skills-skill"
SOURCE = "awesome-skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DEPTH = 3  # <name>, <cat>/<name>, <cat>/<cat>/<name>
_TMP_PREFIX = ".awesome-skills-tmp-"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover(root: Path) -> list[SkillSpec]:
    """Every skill under ``<root>/skills``, sorted by name.

    Matches the Agent Skills discovery depth: a ``SKILL.md`` nested
    inside another skill folder is an asset, not a second skill.
    """
    skills_root = root / "skills"
    if not skills_root.is_dir():
        raise SkillError(f"skills directory missing: {skills_root}")

    found: dict[str, Path] = {}
    duplicates: dict[str, list[Path]] = {}
    for skill_md in sorted(skills_root.rglob(SKILL_FILE)):
        folder = skill_md.parent
        try:
            rel = folder.relative_to(skills_root)
        except ValueError:
            continue
        if len(rel.parts) > MAX_DEPTH:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if _nested(folder, skills_root):
            continue
        name = folder.name
        validate_name(name, where=str(folder))
        if name in found:
            duplicates.setdefault(name, [found[name]]).append(folder)
            continue
        found[name] = folder

    if duplicates:
        detail = "; ".join(
            f"{name}: " + ", ".join(str(path) for path in paths)
            for name, paths in sorted(duplicates.items())
        )
        raise SkillError(f"duplicate skill names in repository: {detail}")

    return [
        SkillSpec(name=name, source=folder, description=description_of(folder))
        for name, folder in sorted(found.items())
    ]


def _nested(folder: Path, skills_root: Path) -> bool:
    parent = folder.parent
    while parent != skills_root and parent != parent.parent:
        if (parent / SKILL_FILE).is_file():
            return True
        parent = parent.parent
    return False


def description_of(folder: Path) -> str:
    """First ``description:`` value from the frontmatter, best effort.

    Only used by ``list``; a malformed frontmatter is ``scripts/
    validate.py``'s job, not the installer's. Handles the quoted and
    block-scalar forms in use.
    """
    try:
        text = shared.read_text(folder / SKILL_FILE)
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            break
        match = re.match(r"^description:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            block: list[str] = []
            for follow in lines[index + 1 :]:
                if follow.startswith((" ", "\t")):
                    block.append(follow.strip())
                elif not follow.strip():
                    block.append("")
                else:
                    break
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(block).strip()
        return _unquote(value)
    return ""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def validate_name(name: str, where: str = "") -> None:
    if not name or len(name) > MAX_NAME_LEN or not NAME_RE.match(name):
        suffix = f" ({where})" if where else ""
        raise SkillError(
            f"invalid skill name {name!r}{suffix}: lowercase words joined by single hyphens"
        )


def validate_spec(spec: SkillSpec) -> None:
    validate_name(spec.name, where=str(spec.source))
    if not spec.source.is_dir():
        raise SkillError(f"{spec.source} is not a directory")
    if not (spec.source / SKILL_FILE).is_file():
        raise SkillError(f"{spec.source} has no {SKILL_FILE}; not a skill directory")


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def marker_text(name: str, version: str) -> str:
    return f"name={name}\nsource={SOURCE}\nversion={version}\n"


def marker_fields(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_dir():
        return {}
    try:
        text = shared.read_text(path / MARKER)
    except OSError:
        return {}
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def marker_name(path: Path) -> str:
    return marker_fields(path).get("name", "")


def marker_version(path: Path) -> str:
    return marker_fields(path).get("version", "")


def link_target(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    target = Path(raw) if os.path.isabs(raw) else path.parent / raw
    return Path(os.path.abspath(str(target)))


def is_owned(path: Path, hub: Path, name: str) -> bool:
    """True when ``path`` is a mirror/copy this tool would have written."""
    if path.is_symlink():
        target = link_target(path)
        return (
            target is not None
            and target.name == name
            and is_within(target, hub)
            and target.resolve() == (hub / name).resolve()
        )
    return marker_name(path) == name


def mirror_ok(dest: Path, hub: Path, name: str) -> bool:
    return (dest.exists() or dest.is_symlink()) and is_owned(dest, hub, name)


def mirror_missing(directory: Path, names: Sequence[str], hub: Path) -> list[str]:
    return [name for name in names if not mirror_ok(directory / name, hub, name)]


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def hub_install(
    spec: SkillSpec,
    hub: Path,
    version: str,
    *,
    force: bool = False,
    backup_root: Path | None = None,
    dry_run: bool = False,
) -> FileResult:
    validate_spec(spec)
    dest = _dest(hub, spec.name)
    existed, owned = _claim(dest, hub, spec.name, force, backup_root, dry_run)

    if dry_run:
        return FileResult(dest, "updated" if existed else "created", "dry-run")

    hub.mkdir(parents=True, exist_ok=True)
    if existed and owned and shared.fingerprint(dest) == shared.fingerprint(spec.source):
        if marker_name(dest) == spec.name and marker_version(dest) == version:
            return FileResult(dest, "unchanged")
        shared.atomic_write(dest / MARKER, marker_text(spec.name, version))
        return FileResult(dest, "updated", "marker refreshed")

    stage = _stage_copy(spec.source, dest)
    try:
        shared.atomic_write(stage / MARKER, marker_text(spec.name, version))
        _swap_in(stage, dest)
    finally:
        _cleanup_stage(stage)
    return FileResult(dest, "updated" if existed else "created", "copy")


def mirror_install(
    spec: SkillSpec,
    dest: Path,
    hub_skill: Path,
    mode: str,
    *,
    force: bool = False,
    backup_root: Path | None = None,
    dry_run: bool = False,
) -> FileResult:
    validate_name(spec.name)
    if not dry_run and (not hub_skill.is_dir() or hub_skill.is_symlink()):
        raise SkillError(f"{hub_skill} is missing; the hub copy was not installed")

    hub = hub_skill.parent
    existed, owned = _claim(dest, hub, spec.name, force, backup_root, dry_run)
    action = "updated" if existed else "created"

    if mode == MODE_LINK:
        if link_target(dest) is not None and is_owned(dest, hub, spec.name):
            return FileResult(dest, "unchanged", "link")
        if dry_run:
            return FileResult(dest, action, "dry-run link")
        _replace_with_symlink(dest, hub_skill)
        return FileResult(dest, action, "link")

    if mode != MODE_COPY:
        raise SkillError(f"unknown mirror mode: {mode!r}")

    if owned and dest.is_dir() and not dest.is_symlink():
        same_content = shared.fingerprint(dest) == shared.fingerprint(hub_skill)
        if same_content and marker_version(dest) == marker_version(hub_skill):
            return FileResult(dest, "unchanged", "copy")
    if dry_run:
        return FileResult(dest, action, "dry-run copy")
    stage = _stage_copy(hub_skill, dest)
    try:
        _swap_in(stage, dest)
    finally:
        _cleanup_stage(stage)
    return FileResult(dest, action, "copy")


def _dest(root: Path, name: str) -> Path:
    dest = root / name
    if dest == root or not is_within(dest, root):
        raise SkillError(f"skill {name!r} resolves outside {root}")
    return dest


def _claim(
    dest: Path,
    hub: Path,
    name: str,
    force: bool,
    backup_root: Path | None,
    dry_run: bool,
) -> tuple[bool, bool]:
    """Validate a destination; back up a foreign one when forced.

    Returns ``(existed, owned)``. Preflight already reported conflicts,
    but a second check here keeps a concurrent change from silently
    overwriting a path we did not create.
    """
    existed = dest.exists() or dest.is_symlink()
    if not existed:
        return False, False
    if is_owned(dest, hub, name):
        return True, True
    if not force:
        raise SkillError(
            f"{dest} already exists and was not installed by awesome-skills; "
            "re-run with --force to back it up and replace it"
        )
    if dry_run:
        return True, False
    if backup_root is None:
        raise SkillError(f"cannot back up {dest}: no backup directory")
    shared.backup_path(dest, backup_root, name)
    return True, False


def _stage_copy(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX, dir=str(dest.parent)))
    stage = holder / dest.name
    try:
        shared.copy_skill_tree(source, stage)
    except BaseException:
        shared.remove_path(holder)
        raise
    return stage


def _stage_symlink(target: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX, dir=str(dest.parent)))
    link = holder / "link"
    try:
        os.symlink(str(target), str(link))
    except BaseException:
        shared.remove_path(holder)
        raise
    return link


def _cleanup_stage(stage: Path) -> None:
    shared.remove_path(stage.parent)


def _swap_in(stage: Path, dest: Path) -> None:
    """Move ``stage`` into ``dest``, keeping the old path until it lands.

    Renaming the old entry aside (instead of deleting it first) means a
    failed second rename can restore it; the window where ``dest`` is
    absent is one rename long.
    """
    old: Path | None = None
    if dest.exists() or dest.is_symlink():
        old = dest.parent / f".{dest.name}.old-{os.getpid()}"
        counter = 1
        while old.exists() or old.is_symlink():
            old = dest.parent / f".{dest.name}.old-{os.getpid()}-{counter}"
            counter += 1
        os.replace(str(dest), str(old))

    try:
        os.replace(str(stage), str(dest))
    except BaseException:
        # Put the original back if the new entry did not land. If even
        # that fails, leave it at the hidden name: a recoverable leftover
        # beats deleting the user's only copy in a ``finally`` block.
        if old is not None and not (dest.exists() or dest.is_symlink()):
            try:
                os.replace(str(old), str(dest))
                old = None
            except OSError:
                pass
        raise

    if old is not None:
        try:
            shared.remove_path(old)
        except OSError:
            pass


def _replace_with_symlink(dest: Path, target: Path) -> None:
    stage = _stage_symlink(target, dest)
    try:
        _swap_in(stage, dest)
    finally:
        _cleanup_stage(stage)


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def remove_skill(path: Path, hub: Path, name: str, *, dry_run: bool = False) -> FileResult:
    """Delete an owned skill, refusing anything we cannot prove is ours."""
    if not (path.exists() or path.is_symlink()):
        return FileResult(path, "not-found")
    if not is_owned(path, hub, name):
        raise SkillError(f"{path} was not installed by awesome-skills; leaving it alone")
    if dry_run:
        return FileResult(path, "removed", "dry-run")
    shared.remove_path(path)
    return FileResult(path, "removed")


def hub_remove(path: Path, hub: Path, name: str, *, dry_run: bool = False) -> FileResult:
    return remove_skill(path, hub, name, dry_run=dry_run)


def mirror_remove(dest: Path, hub: Path, name: str, *, dry_run: bool = False) -> FileResult:
    return remove_skill(dest, hub, name, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Small helpers used by the orchestrator and doctor
# ---------------------------------------------------------------------------


def resolve_sources(root: Path, names: Iterable[str] | None = None) -> list[SkillSpec]:
    """Discovery filtered to ``names`` (all when empty/None)."""
    available = {spec.name: spec for spec in discover(root)}
    if not names:
        return list(available.values())
    selected: list[SkillSpec] = []
    unknown: list[str] = []
    for name in shared.unique(names):
        spec = available.get(name)
        if spec is None:
            unknown.append(name)
        else:
            selected.append(spec)
    if unknown:
        raise SkillError(
            "unknown skill: " + ", ".join(unknown) + "; run `awesome-skills-py list --available`"
        )
    return selected


# Re-exported for callers that only import skills.
__all__ = [
    "MARKER",
    "SKILL_FILE",
    "SkillError",
    "describe",
    "discover",
    "hub_install",
    "hub_remove",
    "is_owned",
    "link_target",
    "marker_name",
    "mirror_install",
    "mirror_missing",
    "mirror_ok",
    "mirror_remove",
    "remove_skill",
    "resolve_sources",
    "validate_name",
]


def describe(spec: SkillSpec) -> str:
    return spec.description or "(no description)"
