"""The one place that knows which agents exist: ``lib/agents.tsv``.

The reference project hard-codes four targets in a tuple. Here the
reviewed table is the registry, so adding an agent is a one-line data
change and cannot drift from the documentation generated out of it.
Parsing is strict: a malformed row is a repository bug, not something to
paper over at install time.
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Iterable, Sequence

from .shared import InstallerError, Scope
from .types import AgentTarget

_ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class RegistryError(InstallerError):
    """The agent table is missing or malformed."""


def repo_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Find the repository that owns ``skills/`` and ``lib/agents.tsv``.

    Order: an explicit path (tests, ``--root``), ``AWESOME_SKILLS_ROOT``,
    the checkout this package lives in, then the current directory.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env = os.environ.get("AWESOME_SKILLS_ROOT", "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path(__file__).resolve().parents[3])
    candidates.append(Path.cwd())

    for candidate in candidates:
        candidate = candidate.resolve()
        if (candidate / "skills").is_dir() and (candidate / "lib" / "agents.tsv").is_file():
            return candidate
    raise RegistryError(
        "cannot locate the awesome-skills repository "
        "(need skills/ and lib/agents.tsv); pass --root or set AWESOME_SKILLS_ROOT"
    )


def table_path(root: Path) -> Path:
    return root / "lib" / "agents.tsv"


def load_targets(root: Path) -> list[AgentTarget]:
    path = table_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise RegistryError(f"agent table missing: {path} ({err})") from err

    targets: list[AgentTarget] = []
    seen: dict[str, int] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise RegistryError(
                f"{path}:{lineno}: expected 4 tab-separated fields, got {len(fields)}"
            )
        alias, project, global_dir, detect = (field.strip() for field in fields)
        if not _ALIAS.match(alias):
            raise RegistryError(f"{path}:{lineno}: invalid alias {alias!r}")
        if alias == "hub":
            raise RegistryError(f"{path}:{lineno}: alias 'hub' is reserved for the shared copy")
        if alias in seen:
            raise RegistryError(
                f"{path}:{lineno}: duplicate alias {alias!r} (first on line {seen[alias]})"
            )
        if not project or not global_dir or not detect:
            raise RegistryError(f"{path}:{lineno}: empty field in row {alias!r}")
        seen[alias] = lineno
        targets.append(AgentTarget(alias, project, global_dir, detect))

    if not targets:
        raise RegistryError(f"agent table is empty: {path}")
    return targets


def load_for(root: Path) -> list[AgentTarget]:
    return load_targets(root)


def find(targets: Iterable[AgentTarget], alias: str) -> AgentTarget | None:
    for target in targets:
        if target.alias == alias:
            return target
    return None


def resolve_selection(
    targets: Sequence[AgentTarget],
    values: Sequence[str] | None,
    scope: Scope,
) -> list[AgentTarget]:
    """``auto`` / ``all`` / ``none`` / ``a,b`` -> targets, in table order.

    ``auto`` returns only detected agents. Unlike the reference's Claude
    fallback, an empty answer is meaningful here: the shared hub still
    gets the skills, and the caller says so.
    """
    raw = ",".join(values or ()).strip().lower()
    del scope  # detection tokens are user-level; kept for interface symmetry

    if raw in ("", "auto"):
        return [target for target in targets if target.detect().installed]
    if raw == "none":
        return []
    if raw == "all":
        return list(targets)

    chosen: list[AgentTarget] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for alias in (part.strip() for part in raw.split(",")):
        if not alias or alias in seen:
            continue
        seen.add(alias)
        target = find(targets, alias)
        if target is None:
            unknown.append(alias)
        else:
            chosen.append(target)
    if unknown:
        known = [target.alias for target in targets]
        hints = []
        for alias in unknown:
            match = difflib.get_close_matches(alias, known, n=1)
            hints.append(f"{alias!r}" + (f" (did you mean {match[0]!r}?)" if match else ""))
        raise RegistryError(
            "unknown tool alias: "
            + ", ".join(hints)
            + f". {len(known)} known aliases; run `awesome-skills-py tools`."
        )
    return chosen


def groups_by_directory(
    targets: Sequence[AgentTarget], scope: Scope
) -> list[tuple[Path, list[AgentTarget]]]:
    """Targets that write the same directory end up in one group.

    Around twenty aliases share ``~/.agents/skills``; writing (and
    hashing) that tree once instead of twenty times is the difference
    between a fast install and a noisy one. Order follows the table.
    """
    groups: dict[Path, list[AgentTarget]] = {}
    for target in targets:
        directory = target.directory(scope)
        if directory is None:
            continue
        groups.setdefault(directory, []).append(target)
    return list(groups.items())



def scope_from_args(project: str | None) -> Scope:
    return Scope.project(project) if project else Scope.global_()


__all__ = [
    "RegistryError",
    "AgentTarget",
    "find",
    "groups_by_directory",
    "load_targets",
    "repo_root",
    "resolve_selection",
    "scope_from_args",
    "table_path",
]
