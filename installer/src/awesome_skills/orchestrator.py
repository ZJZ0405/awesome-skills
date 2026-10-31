"""Orchestration: state in, target calls out, reports back.

The orchestrator knows no agent's paths. It resolves the shared hub once,
runs each target's five verbs, and folds outcomes into the state
manifest. One target's failure never aborts the sweep.

The hub is installed once, before any target, because every mirror needs
it and because writing it per target would hash the same tree 80 times.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import registry, shared, skills
from .shared import InstallerError, Scope
from .state import HUB_TARGET, LEGACY_TARGET, State
from .types import (
    AgentTarget,
    Drift,
    FileResult,
    InstallRequest,
    MODE_COPY,
    SkillSpec,
    SkillError,
    TargetReport,
)

CONFLICT_HINT = "re-run with --force to back up the existing paths and replace them"


@dataclass
class InstallOutcome:
    hub_files: list[FileResult] = field(default_factory=list)
    reports: list[TargetReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hub_failed: int = 0

    @property
    def failed(self) -> bool:
        return self.hub_failed > 0 or any(report.status == "failed" for report in self.reports)

    def actions(self) -> list[FileResult]:
        files = list(self.hub_files)
        for report in self.reports:
            files.extend(report.files)
        return files

    def count(self, *actions: str) -> int:
        wanted = set(actions)
        return sum(1 for file in self.actions() if file.action in wanted)


@dataclass
class UninstallOutcome:
    files: list[FileResult] = field(default_factory=list)
    reports: list[TargetReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(report.status == "failed" for report in self.reports)

    def count(self, *actions: str) -> int:
        wanted = set(actions)
        return sum(1 for file in self.files if file.action in wanted)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def install(
    scope: Scope,
    targets: Sequence[AgentTarget],
    specs: Sequence[SkillSpec],
    state: State,
    *,
    mode: str = "link",
    force: bool = False,
    dry_run: bool = False,
    version: str = "dev",
) -> InstallOutcome:
    hub = shared.hub_dir(scope)
    backup_root = shared.state_dir(scope) / "backups"
    outcome = InstallOutcome()

    conflicts, foreign = _preflight(scope, targets, specs, hub, force)
    if conflicts:
        raise InstallerError(
            "refusing to install; these paths exist and are not ours:\n  - "
            + "\n  - ".join(conflicts)
            + f"\n{CONFLICT_HINT}"
        )
    if foreign and force:
        verb = "would be moved" if dry_run else "moved"
        outcome.notes.append(
            f"{len(foreign)} existing path(s) {verb} to {shared.tildify(backup_root)}"
        )

    ready: list[SkillSpec] = []
    for spec in specs:
        try:
            result = skills.hub_install(
                spec,
                hub,
                version,
                force=force,
                backup_root=backup_root,
                dry_run=dry_run,
            )
        except (SkillError, OSError) as err:
            outcome.notes.append(f"{spec.name}: {err}")
            outcome.hub_failed += 1
            continue
        outcome.hub_files.append(result)
        ready.append(spec)
        if not dry_run:
            state.record(HUB_TARGET, spec.name, MODE_COPY, version, result.path)

    request = InstallRequest(
        skills=tuple(ready),
        hub=hub,
        mode=mode,
        force=force,
        dry_run=dry_run,
        backup_root=backup_root,
    )
    seen: dict[Path, tuple[str, list[str]]] = {}
    for target in targets:
        directory = target.directory(scope)
        if directory is None:
            outcome.reports.append(
                TargetReport(target.alias, "unsupported", notes=[f"no {scope.label} skills directory"])
            )
            continue
        if directory == hub:
            outcome.reports.append(
                TargetReport(
                    target.alias,
                    "unchanged",
                    notes=["reads the shared hub directly; no mirror needed"],
                )
            )
            continue
        if directory in seen:
            first_alias, written = seen[directory]
            _record_names(state, target.alias, written, mode, version, directory, dry_run)
            outcome.reports.append(
                TargetReport(
                    target.alias,
                    "unchanged",
                    notes=[f"shares {shared.tildify(directory)} with {first_alias}"],
                )
            )
            continue
        try:
            result = target.install(scope, request)
        except Exception as err:  # one bad agent must not stop the sweep
            outcome.reports.append(
                TargetReport(target.alias, "failed", error=f"{type(err).__name__}: {err}")
            )
            continue
        seen[directory] = (target.alias, result.names("skills"))
        _record_names(
            state, target.alias, result.names("skills"), mode, version, directory, dry_run
        )
        failed = any(file.action == "failed" for file in result.files)
        status = "failed" if failed else (
            "installed"
            if any(file.action in ("created", "updated") for file in result.files)
            else "unchanged"
        )
        outcome.reports.append(
            TargetReport(target.alias, status, files=list(result.files), notes=list(result.notes))
        )

    if not dry_run:
        state.save()
    return outcome


def _record_names(
    state: State,
    alias: str,
    names: Sequence[str],
    mode: str,
    version: str,
    directory: Path,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    for name in names:
        state.record(alias, name, mode, version, directory / name)


def _preflight(
    scope: Scope,
    targets: Sequence[AgentTarget],
    specs: Sequence[SkillSpec],
    hub: Path,
    force: bool,
) -> tuple[list[str], list[Path]]:
    """Collect conflicts and unwritable destinations before any write.

    The shell installer learned this the hard way: discovering a conflict
    halfway through leaves a half-installed hub. Nothing is written until
    every destination is known.
    """
    hub_ok = shared.nearest_writable(hub)
    if not hub_ok:
        return [f"{hub} (not writable)"], []

    destinations: dict[Path, tuple[str, str]] = {}
    for spec in specs:
        destinations[hub / spec.name] = (spec.name, "hub copy")
    for target in targets:
        directory = target.directory(scope)
        if directory is None or directory == hub:
            continue
        for spec in specs:
            destinations.setdefault(
                directory / spec.name,
                (spec.name, f"mirror for {target.alias}"),
            )

    conflicts: list[str] = []
    foreign: list[Path] = []
    for dest, (name, label) in destinations.items():
        if not shared.nearest_writable(dest.parent):
            conflicts.append(f"{dest} (parent not writable, {label})")
            continue
        if not (dest.exists() or dest.is_symlink()):
            continue
        if skills.is_owned(dest, hub, name):
            continue
        if force:
            foreign.append(dest)
        else:
            conflicts.append(f"{dest} ({label})")
    return conflicts, foreign


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def uninstall(
    scope: Scope,
    targets: Sequence[AgentTarget],
    state: State,
    *,
    all_targets: Sequence[AgentTarget],
    remove_all: bool = False,
    skill_filter: Sequence[str] | None = None,
    dry_run: bool = False,
) -> UninstallOutcome:
    """Remove what the manifest says we wrote, nothing else.

    Building the request from state (instead of passing "everything") is
    what makes a generic uninstall safe: it can only name entries this
    tool itself installed.
    """
    hub = shared.hub_dir(scope)
    wanted = set(skill_filter or ())
    selected_aliases = {target.alias for target in targets}
    outcome = UninstallOutcome()
    removed_paths: set[Path] = set()

    if not remove_all:
        mirrors = [target for target in targets if not target.reads_hub(scope)]
        if not mirrors:
            raise InstallerError(
                "the selected tools read the shared hub directly; "
                "use --all to remove the hub copies"
            )
        targets = mirrors

    for target in targets:
        directory = target.directory(scope)
        if directory is None:
            outcome.reports.append(
                TargetReport(target.alias, "unsupported", notes=[f"no {scope.label} skills directory"])
            )
            continue
        names = state.skills(target.alias)
        if wanted:
            names = [name for name in names if name in wanted]
        if not names:
            outcome.reports.append(TargetReport(target.alias, "not-configured"))
            continue

        protected: set[Path] = set()
        for name in names:
            protected.update(
                _protected_paths(
                    scope, state, target.alias, name, selected_aliases, remove_all, all_targets
                )
            )
        recorded = {name: state.get(target.alias, name) for name in names}
        request = InstallRequest(
            skills=tuple(SkillSpec(name=name, source=Path(".")) for name in names),
            hub=hub,
            protected=frozenset(protected),
            dry_run=dry_run,
        )
        try:
            result = target.uninstall(scope, request)
        except Exception as err:  # one bad agent must not stop the sweep
            outcome.reports.append(
                TargetReport(target.alias, "failed", error=f"{type(err).__name__}: {err}")
            )
            continue

        outcome.files.extend(result.files)
        released = result.names("skills")
        # An environment variable may have moved the tool directory since
        # the install. Clean the recorded path too, and keep the record if
        # that cleanup fails so a retry still knows where the file is.
        released = [
            name
            for name in released
            if _remove_stale_path(
                outcome,
                recorded.get(name),
                directory / name if directory is not None else None,
                hub,
                name,
                protected,
                dry_run,
            )
        ]
        removed_here = {file.path for file in result.files if file.action == "removed"}
        removed_paths.update(removed_here)
        if not dry_run:
            state.forget_all(target.alias, released)
        shared_gone = bool(result.files) and all(
            file.action == "not-found" and file.path in removed_paths for file in result.files
        )
        status = "removed" if removed_here or shared_gone else (
            "kept" if any(file.action == "kept" for file in result.files) else "not-configured"
        )
        outcome.reports.append(
            TargetReport(target.alias, status, files=list(result.files), notes=list(result.notes))
        )

    if remove_all:
        _uninstall_hub(state, hub, wanted, outcome, dry_run)
        _uninstall_orphan_records(scope, state, hub, selected_aliases, wanted, outcome, dry_run)

    # Aliases that share a directory report the same path; only the first
    # removal is interesting, the rest are bookkeeping.
    outcome.files = [
        file
        for file in outcome.files
        if not (file.action == "not-found" and file.path in removed_paths)
    ]
    if not dry_run:
        _cleanup_empty_dirs(outcome.files, hub)
        state.save()
    return outcome


def _protected_paths(
    scope: Scope,
    state: State,
    alias: str,
    name: str,
    selected_aliases: set[str],
    remove_all: bool,
    all_targets: Sequence[AgentTarget],
) -> set[Path]:
    """Paths that must survive this uninstall because someone else uses them."""
    protected: set[Path] = set()
    for other, other_name, record in state.all_records():
        if other_name != name or other == alias:
            continue
        if other == HUB_TARGET:
            if not remove_all:
                protected.add(record.path)
            continue
        if other in selected_aliases and remove_all:
            continue  # it is being removed in this same sweep
        if other == LEGACY_TARGET or registry.find(all_targets, other) is None:
            protected.add(record.path)
            continue
        protected.add(record.path)
        other_target = registry.find(all_targets, other)
        if other_target is not None:
            directory = other_target.directory(scope)
            if directory is not None:
                protected.add(directory / name)
    return protected


def _uninstall_hub(
    state: State,
    hub: Path,
    wanted: set[str],
    outcome: UninstallOutcome,
    dry_run: bool,
) -> None:
    for name in state.skills(HUB_TARGET):
        if wanted and name not in wanted:
            continue
        record = state.get(HUB_TARGET, name)
        assert record is not None
        try:
            result = skills.hub_remove(record.path, hub, name, dry_run=dry_run)
        except (SkillError, OSError) as err:
            outcome.notes.append(str(err))
            continue
        outcome.files.append(result)
        if not dry_run:
            state.forget(HUB_TARGET, name)


def _uninstall_orphan_records(
    scope: Scope,
    state: State,
    hub: Path,
    selected_aliases: set[str],
    wanted: set[str],
    outcome: UninstallOutcome,
    dry_run: bool,
) -> None:
    """Rows whose alias is no longer in the table (legacy manifest, retired tool)."""
    del scope
    for alias in list(state.targets):
        if alias == HUB_TARGET or alias in selected_aliases:
            continue
        for name in list(state.targets[alias]):
            if wanted and name not in wanted:
                continue
            record = state.get(alias, name)
            assert record is not None
            try:
                result = skills.remove_skill(record.path, hub, name, dry_run=dry_run)
            except (SkillError, OSError) as err:
                outcome.notes.append(f"{alias}/{name}: {err}")
                continue
            outcome.files.append(result)
            if not dry_run:
                state.forget(alias, name)


def _remove_stale_path(
    outcome: UninstallOutcome,
    record,
    current: Path | None,
    hub: Path,
    name: str,
    protected: set[Path],
    dry_run: bool,
) -> bool:
    """Remove a recorded path that the tool directory no longer resolves to.

    Returns False when the old path could not be removed, so the caller
    keeps the record for a later retry.
    """
    if record is None or current is None or record.path == current:
        return True
    if record.path in protected:
        return True
    if not (record.path.exists() or record.path.is_symlink()):
        return True
    try:
        result = skills.remove_skill(record.path, hub, name, dry_run=dry_run)
    except (SkillError, OSError) as err:
        outcome.notes.append(f"kept old path {record.path}: {err}")
        return False
    outcome.files.append(result)
    return True


def _cleanup_empty_dirs(files: Iterable[FileResult], hub: Path) -> None:
    parents = {file.path.parent for file in files if file.action == "removed"}
    for parent in sorted(parents, key=lambda path: len(path.parts), reverse=True):
        if parent == hub:
            continue
        try:
            parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def check(scope: Scope, state: State, targets: Sequence[AgentTarget]) -> list[Drift]:
    """Entries the manifest claims but the filesystem no longer matches."""
    hub = shared.hub_dir(scope)
    by_alias = {target.alias: target for target in targets}
    drift: list[Drift] = []

    for target_id, name, record in state.all_records():
        if target_id == HUB_TARGET:
            if not _present(record.path, hub, name):
                drift.append(Drift(HUB_TARGET, name, record.path, "hub copy is missing or replaced"))
            continue

        if target_id == LEGACY_TARGET or target_id not in by_alias:
            if not record.path.exists():
                drift.append(Drift(target_id, name, record.path, "recorded path is gone"))
            elif not skills.is_owned(record.path, hub, name):
                drift.append(Drift(target_id, name, record.path, "path is no longer ours"))
            continue

        target = by_alias[target_id]
        directory = target.directory(scope)
        current = directory / name if directory is not None else None
        if not _present(record.path, hub, name):
            drift.append(Drift(target_id, name, record.path, "mirror is missing or replaced"))
            continue
        if current is not None and current != record.path:
            drift.append(
                Drift(target_id, name, record.path, "tool directory moved; re-run install")
            )
            continue
        if record.mode == MODE_COPY and current is not None:
            hub_copy = hub / name
            if hub_copy.is_dir() and shared.fingerprint(current) != shared.fingerprint(hub_copy):
                drift.append(Drift(target_id, name, record.path, "copy is stale; re-run install"))

    return sorted(drift, key=lambda item: (item.target, item.skill, str(item.path)))


def _present(path: Path, hub: Path, name: str) -> bool:
    return (path.exists() or path.is_symlink()) and skills.is_owned(path, hub, name)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@dataclass
class DoctorReport:
    lines: list[tuple[str, str]] = field(default_factory=list)  # (ok|warn|bad|info, text)
    errors: int = 0
    warnings: int = 0

    def ok(self, text: str) -> None:
        self.lines.append(("ok", text))

    def warn(self, text: str) -> None:
        self.lines.append(("warn", text))
        self.warnings += 1

    def bad(self, text: str) -> None:
        self.lines.append(("bad", text))
        self.errors += 1

    def info(self, text: str) -> None:
        self.lines.append(("info", text))


def doctor(scope: Scope, root: Path, targets: Sequence[AgentTarget], state: State) -> DoctorReport:
    report = DoctorReport()

    try:
        available = skills.discover(root)
        report.ok(f"repository: {len(available)} skill(s) under {shared.tildify(root / 'skills')}")
    except SkillError as err:
        report.bad(f"repository: {err}")
        available = []

    if targets:
        report.ok(f"agent table: {len(targets)} alias(es)")
    else:
        report.bad("agent table: no aliases")

    hub = shared.hub_dir(scope)
    if hub.exists():
        report.ok(f"hub: {shared.tildify(hub)}")
    else:
        report.info(f"hub: {shared.tildify(hub)} (created on first install)")

    if state.path.exists():
        if state.corrupt:
            report.bad(f"state: unreadable ({shared.tildify(state.path)})")
        else:
            report.ok(
                f"state: {state.record_count()} record(s) in {shared.tildify(state.path)}"
            )
    elif state.adopted_legacy:
        report.info(f"state: adopted legacy manifest ({state.record_count()} record(s))")
    else:
        report.info("state: nothing recorded for this scope")

    detected = [target.alias for target in targets if target.detect().installed]
    if detected:
        report.ok(f"detected agents: {', '.join(detected)}")
    else:
        report.info("detected agents: none (hub-only install is still useful)")

    _probe_symlinks(report)

    for line in _probe_python():
        report.info(line)

    drift = check(scope, state, targets)
    if drift:
        report.warn(f"drift: {len(drift)} recorded entr(ies) need attention (run check)")
    return report


def _probe_symlinks(report: DoctorReport) -> None:
    folder: Path | None = None
    try:
        folder = Path(tempfile.mkdtemp(prefix="awesome-skills-doctor-"))
        target = folder / "target"
        target.mkdir()
        link = folder / "link"
        os.symlink(str(target), str(link))
        if link.is_symlink():
            report.ok("symlinks: supported (mode=link)")
    except OSError:
        report.warn("symlinks: not supported here; use --copy")
    finally:
        if folder is not None:
            shared.remove_path(folder)


def _probe_python() -> list[str]:
    import platform

    return [f"python: {platform.python_version()} ({platform.system()})"]
