"""Filesystem and environment primitives shared by every module.

Ported from the agentinstaller reference project (``shared.py``), reduced
to what a skills-only installer needs. The rules encoded here are the
ones that prevent silent damage:

* resolve paths at call time, never at import time (tests move ``HOME``;
  agents move their config directories through environment variables);
* write atomically, and write *through* a symlink instead of replacing it;
* compare content hashes, not mtimes, so a re-run can honestly report
  ``unchanged``;
* never create a directory through a symlink we do not understand.

This module deliberately imports nothing from the rest of the package.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence


class InstallerError(Exception):
    """User-facing failure. The CLI prints the message and exits 1."""


class ScopeKind(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"


@dataclass(frozen=True)
class Scope:
    """Where an install lives: the user's home or one project.

    ``project_root`` is absolute and resolved once, at construction time,
    because a project install must keep pointing at the project even if
    the process changes directory later.
    """

    kind: ScopeKind
    project_root: Path | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.PROJECT and self.project_root is None:
            raise ValueError("project scope needs a project root")
        if self.kind is ScopeKind.GLOBAL and self.project_root is not None:
            raise ValueError("global scope takes no project root")

    @property
    def label(self) -> str:
        return self.kind.value

    @classmethod
    def global_(cls) -> "Scope":
        return cls(ScopeKind.GLOBAL)

    @classmethod
    def project(cls, path: str | os.PathLike[str]) -> "Scope":
        root = Path(path).expanduser()
        if not root.is_dir():
            raise InstallerError(f"project directory does not exist: {root}")
        return cls(ScopeKind.PROJECT, root.resolve())


# ---------------------------------------------------------------------------
# Paths / environment
# ---------------------------------------------------------------------------


def home() -> Path:
    """The user's home, read at call time (tests move ``HOME``)."""
    return Path(os.path.expanduser("~")).resolve()


def _abspath(path: Path) -> Path:
    """Normalise ``.`` / ``..`` without resolving symlinks or hitting disk."""
    return Path(os.path.abspath(str(path)))


def _env_path(value: str, base: Path) -> Path:
    raw = value.strip()
    if raw == "~":
        return home()
    if raw.startswith("~/"):
        return _abspath(home() / raw[2:])
    if raw.startswith("/"):
        return _abspath(Path(raw))
    return _abspath(base / raw)


def hub_dir(scope: Scope) -> Path:
    """The shared copy every mirror points at.

    ``AWESOME_SKILLS_HOME`` only moves the *global* hub, matching the
    shell installer. A project install is always anchored to the project.
    """
    if scope.kind is ScopeKind.PROJECT:
        assert scope.project_root is not None
        return _abspath(scope.project_root / ".agents" / "skills")
    override = os.environ.get("AWESOME_SKILLS_HOME", "").strip()
    if override:
        return _env_path(override, home())
    return _abspath(home() / ".agents" / "skills")


def state_dir(scope: Scope) -> Path:
    """Per-scope bookkeeping; deliberately not inside the hub.

    Keeping state beside (not inside) the skills directory means pointing
    ``AWESOME_SKILLS_HOME`` at a shared volume does not mix one machine's
    ownership records into another machine's skill tree.
    """
    root = scope.project_root if scope.kind is ScopeKind.PROJECT else home()
    assert root is not None
    return _abspath(root / ".agents" / ".awesome-skills")


def tildify(path: Path) -> str:
    """``~``-shorten a path for display without following symlinks."""
    absolute = Path(os.path.abspath(str(path)))
    try:
        return "~/" + str(absolute.relative_to(home()))
    except (ValueError, OSError):
        return str(absolute)


_TEMPLATE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_template(template: str) -> str:
    """Expand ``${NAME}`` and ``${NAME:-default}`` in an agent path.

    A single pass, like the shell installer. An unset *or empty* variable
    falls back to the default; an unknown variable with no default
    expands to the empty string (and is then caught by path resolution if
    it leaves a relative path).
    """
    out: list[str] = []
    pos = 0
    while True:
        match = _TEMPLATE.search(template, pos)
        if match is None:
            out.append(template[pos:])
            break
        out.append(template[pos : match.start()])
        value = os.environ.get(match.group(1), "")
        if not value:
            value = match.group(2) or ""
        out.append(value)
        pos = match.end()
    text = "".join(out)
    if "${" in text:
        raise InstallerError(
            f"invalid path template {template!r}: expected ${{NAME}} or ${{NAME:-default}}"
        )
    return text


def resolve_path(template: str, scope: Scope) -> Path | None:
    """Resolve one ``agents.tsv`` path cell; ``None`` for ``N/A``.

    ``~/`` is expanded, absolute paths are kept, and relative paths are
    anchored to the project for a project install and to the home
    directory otherwise.
    """
    if template.strip() in ("", "N/A"):
        return None
    raw = expand_template(template).strip()
    if raw in ("", "N/A"):
        return None
    path = Path(raw)
    if raw == "~" or raw.startswith("~/"):
        return _abspath(home() / raw[2:]) if raw != "~" else home()
    if path.is_absolute():
        return _abspath(path)
    base = scope.project_root if scope.kind is ScopeKind.PROJECT else home()
    assert base is not None
    return _abspath(base / path)


def detect_paths(template: str) -> list[Path]:
    """Paths from a detection cell that exist right now.

    Read-only and quiet: this runs for every agent on every install,
    including agents this tool was never installed into. It must not
    create, back up, or warn about anything.
    """
    found: list[Path] = []
    for token in template.split(";"):
        token = token.strip()
        if not token or token == "N/A" or token.startswith("cmd:"):
            continue
        path = resolve_path(token, Scope.global_())
        if path is not None and path.exists():
            found.append(path)
    return found


def is_detected(template: str) -> bool:
    for token in template.split(";"):
        token = token.strip()
        if not token or token == "N/A":
            continue
        if token.startswith("cmd:"):
            if command_on_path(token[4:].strip()):
                return True
            continue
        path = resolve_path(token, Scope.global_())
        if path is not None and path.exists():
            return True
    return False


@lru_cache(maxsize=8)
def _path_commands(path_value: str) -> frozenset[str]:
    """Every executable name on this ``PATH``, scanned once.

    ``shutil.which`` per command is O(dirs) and, on a WSL machine with
    ``/mnt/c`` on ``PATH``, costs ~85 ms per *missing* name — 60 agents
    froze the guide for seconds. One ``scandir`` pass turns the whole
    probe into a set lookup. The cache is keyed by ``PATH`` and cleared
    explicitly by tests that add a binary mid-run.
    """
    names: set[str] = set()
    for folder in path_value.split(os.pathsep):
        if not folder:
            continue
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_file():
                            names.add(entry.name)
                    except OSError:
                        continue
        except OSError:
            continue
    return frozenset(names)


def command_on_path(name: str) -> bool:
    if not name:
        return False
    if os.name == "nt":
        return shutil.which(name) is not None
    # The cheap pass only collects names; ``which`` verifies the exec bit
    # for the handful of candidates that actually exist.
    if name not in _path_commands(os.environ.get("PATH", "")):
        return False
    return shutil.which(name) is not None


def clear_command_cache() -> None:
    """Tests call this after creating a fake executable."""
    _path_commands.cache_clear()


# ---------------------------------------------------------------------------
# Reads / writes
# ---------------------------------------------------------------------------


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_then_rename(path: Path, content: str) -> None:
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(folder), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write(path: Path, content: str) -> None:
    """Write atomically, through a symlink rather than replacing it.

    A ``~/.agents/.../state.json -> ~/dotfiles/state.json`` kept in a
    dotfiles repo must stay a symlink. Renaming onto the link would turn
    it into a private copy and leave the dotfiles repo stale.
    """
    if not path.is_symlink():
        _write_then_rename(path, content)
        return
    target = path
    for _ in range(40):
        if not target.is_symlink():
            break
        target = Path(os.path.realpath(str(target.parent))) / os.readlink(target)
    else:
        raise InstallerError(f"{path} is a symbolic link chain that never ends")
    if not target.parent.exists():
        raise InstallerError(f"{path} points at {target}, whose folder does not exist")
    _write_then_rename(target, content)


def remove_path(path: Path) -> None:
    """Remove a file, symlink, or directory tree; idempotent."""
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    elif path.is_dir():
        shutil.rmtree(path)


# ---------------------------------------------------------------------------
# Content identity
# ---------------------------------------------------------------------------

IGNORED_NAMES = frozenset({".git", ".DS_Store", "__pycache__", ".awesome-skills-skill"})
IGNORED_SUFFIXES = (".pyc", ".pyo")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root: Path) -> tuple[tuple[str, str], ...] | None:
    """Relative path + content hash for every file, minus our marker.

    ``None`` when ``root`` is not a real directory (e.g. a symlink), so
    callers can tell "not comparable" from "empty".
    """
    if root.is_symlink() or not root.is_dir():
        return None
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in IGNORED_NAMES for part in rel.parts):
            continue
        if path.suffix in IGNORED_SUFFIXES:
            continue
        posix = rel.as_posix()
        if path.is_symlink():
            entries.append((posix, "link:" + os.readlink(path)))
        elif path.is_file():
            entries.append((posix, "sha256:" + sha256_file(path)))
    return tuple(entries)


def copy_skill_tree(source: Path, dest: Path) -> None:
    """Copy a skill directory verbatim, minus repository noise."""
    shutil.copytree(
        source,
        dest,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", ".DS_Store", "__pycache__", "*.pyc", "*.pyo"),
    )


# ---------------------------------------------------------------------------
# Safety / preflight helpers
# ---------------------------------------------------------------------------


def is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def nearest_writable(path: Path) -> bool:
    """True when the nearest existing ancestor is a writable directory."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.is_dir():
        return False
    return os.access(probe, os.W_OK | os.X_OK)


def backup_path(path: Path, backup_root: Path, name: str) -> Path:
    """Move a path we are about to replace into the backup directory."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tag = hashlib.sha256(str(path.parent).encode("utf-8")).hexdigest()[:8]
    dest = backup_root / stamp / tag / name
    counter = 1
    while dest.exists() or dest.is_symlink():
        dest = dest.with_name(f"{name}.{counter}")
        counter += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def split_csv(values: Sequence[str] | None) -> list[str]:
    """Flatten repeatable ``a,b`` flags, preserving order and dropping blanks."""
    out: list[str] = []
    for value in values or ():
        for part in value.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return unique(out)


def repo_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"
