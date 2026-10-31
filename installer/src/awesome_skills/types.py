"""The installer's contract.

Mirrors the reference project's ``types.py`` with two adaptations that
follow from this repository's shape:

1. There is no ``targets/<agent>.py`` module per agent. The reviewed
   ``lib/agents.tsv`` table already *is* the registry; 80 hand-written
   classes would re-encode the same four columns and drift from it. An
   :class:`AgentTarget` is therefore a thin object over one table row
   that still owns the five verbs (``detect`` / ``install`` /
   ``uninstall`` / ``verify`` / ``describe_paths``).
2. Skills are installed once into a shared hub and then linked or copied
   into each agent's directory. That is this product's existing
   behaviour ("one install, many agents"), so ``InstallRequest`` carries
   the hub path and the mirror mode.

Everything else is deliberately close to the reference: frozen result
types, an explicit location, and a ``WriteResult.recorded`` channel the
orchestrator folds into its own state manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .shared import InstallerError, Scope, ScopeKind, hub_dir, is_detected, resolve_path


class SkillError(InstallerError):
    """A skill cannot be installed or removed safely."""


FileAction = str  # created | updated | unchanged | removed | not-found | kept

MODE_LINK = "link"
MODE_COPY = "copy"
MODES = (MODE_LINK, MODE_COPY)


@dataclass(frozen=True)
class DetectionResult:
    installed: bool
    paths: tuple[Path, ...] = ()

    @property
    def config_path(self) -> Path | None:
        return self.paths[0] if self.paths else None


@dataclass(frozen=True)
class SkillSpec:
    """One skill directory: a folder with ``SKILL.md`` at its root."""

    name: str
    source: Path
    description: str = ""


@dataclass(frozen=True)
class InstallRequest:
    skills: tuple[SkillSpec, ...]
    hub: Path
    mode: str = MODE_LINK
    force: bool = False
    dry_run: bool = False
    backup_root: Path | None = None
    #: Uninstall only: paths another recorded target still needs. Targets
    #: keep them and release their own record instead of deleting.
    protected: frozenset[Path] = frozenset()

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}; expected link or copy")


@dataclass(frozen=True)
class FileResult:
    path: Path
    action: FileAction
    detail: str = ""

    def __str__(self) -> str:
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.action:<9} {self.path}{suffix}"


@dataclass(frozen=True)
class WriteResult:
    files: list[FileResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Install: skill names now owned by the caller. Uninstall: skill
    #: names the caller may forget (removed, already gone, or released
    #: because another target shares the path). Names left alone because
    #: the path is no longer ours are deliberately absent.
    recorded: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def names(self, kind: str = "skills") -> list[str]:
        return list(self.recorded.get(kind, ()))

    def extend(self, other: "WriteResult") -> "WriteResult":
        merged = {kind: list(names) for kind, names in self.recorded.items()}
        for kind, names in other.recorded.items():
            merged.setdefault(kind, []).extend(names)
        return WriteResult(
            files=[*self.files, *other.files],
            notes=[*self.notes, *other.notes],
            recorded=merged,
        )


@dataclass(frozen=True)
class Drift:
    target: str
    skill: str
    path: Path
    reason: str


@dataclass
class TargetReport:
    alias: str
    status: str  # installed | unchanged | removed | not-configured | unsupported | failed
    files: list[FileResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# The target interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTarget:
    """One row of ``lib/agents.tsv``.

    Path resolution happens in every call, never in ``__init__``: tests
    move ``HOME``, projects change, and agents relocate their config
    directory through ``CLAUDE_CONFIG_DIR`` / ``XDG_CONFIG_HOME`` /
    ``CODEX_HOME`` at any time.
    """

    alias: str
    project_template: str
    global_template: str
    detect_template: str

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"skills"})

    @property
    def display_name(self) -> str:
        return self.alias

    def template(self, scope: Scope) -> str:
        return self.global_template if scope.kind is ScopeKind.GLOBAL else self.project_template

    def directory(self, scope: Scope) -> Path | None:
        return resolve_path(self.template(scope), scope)

    def supports(self, scope: Scope) -> bool:
        return self.directory(scope) is not None

    def reads_hub(self, scope: Scope) -> bool:
        directory = self.directory(scope)
        return directory is not None and directory == hub_dir(scope)

    def detect(self, scope: Scope | None = None) -> DetectionResult:
        del scope  # detection tokens are user-level paths and binaries
        paths = tuple(
            path
            for token in self.detect_template.split(";")
            if (path := _detect_token(token)) is not None
        )
        return DetectionResult(installed=is_detected(self.detect_template), paths=paths)

    # -- the two write verbs ---------------------------------------------

    def install(self, scope: Scope, req: InstallRequest) -> WriteResult:
        from . import skills

        directory = self.directory(scope)
        if directory is None:
            raise SkillError(f"{self.alias} has no {scope.label} skills directory")
        if directory == req.hub:
            return WriteResult(
                notes=[f"{self.alias} reads the shared hub directly; no mirror needed"]
            )

        files: list[FileResult] = []
        written: list[str] = []
        for spec in req.skills:
            dest = directory / spec.name
            try:
                files.append(
                    skills.mirror_install(
                        spec,
                        dest,
                        req.hub / spec.name,
                        req.mode,
                        force=req.force,
                        backup_root=req.backup_root,
                        dry_run=req.dry_run,
                    )
                )
            except (SkillError, OSError) as err:
                # Keep going: one unwritable mirror must not cost the
                # user the other nineteen skills.
                files.append(FileResult(dest, "failed", str(err)))
                continue
            written.append(spec.name)
        return WriteResult(files=files, recorded={"skills": written})

    def uninstall(self, scope: Scope, req: InstallRequest) -> WriteResult:
        from . import skills

        directory = self.directory(scope)
        if directory is None:
            raise SkillError(f"{self.alias} has no {scope.label} skills directory")

        files: list[FileResult] = []
        notes: list[str] = []
        released: list[str] = []
        for spec in req.skills:
            dest = directory / spec.name
            if dest in req.protected:
                files.append(FileResult(dest, "kept", "shared with another tool"))
                notes.append(f"kept {dest}: another recorded tool still needs it")
                released.append(spec.name)
                continue
            try:
                files.append(skills.mirror_remove(dest, req.hub, spec.name, dry_run=req.dry_run))
            except SkillError as err:
                # Not ours any more: delete nothing and keep the record so
                # the user can inspect the path.
                files.append(FileResult(dest, "kept", "not installed by us"))
                notes.append(str(err))
                continue
            released.append(spec.name)
        return WriteResult(files=files, notes=notes, recorded={"skills": released})

    # -- introspection ----------------------------------------------------

    def verify(self, scope: Scope, names: Sequence[str]) -> list[str]:
        from . import skills

        directory = self.directory(scope)
        if directory is None:
            return list(names)
        return [name for name in names if not skills.mirror_ok(directory / name, hub_dir(scope), name)]

    def describe_paths(self, scope: Scope, names: Iterable[str] = ()) -> list[Path]:
        directory = self.directory(scope)
        if directory is None:
            return []
        return [directory / name for name in names] or [directory]


def _detect_token(token: str) -> Path | None:
    """The path an existing detection token names, if it exists.

    ``cmd:`` tokens have no path; they are handled by ``is_detected``.
    """
    from .shared import resolve_path as _resolve

    token = token.strip()
    if not token or token == "N/A" or token.startswith("cmd:"):
        return None
    path = _resolve(token, Scope.global_())
    return path if path is not None and path.exists() else None
