"""The installer's own manifest: what *we* wrote, where.

A skills collection installs user-chosen names into directories shared
by many agents, so "is this ours?" cannot be answered by looking for one
fixed key. The manifest is that answer.

One manifest per scope (``~/.agents/.awesome-skills/state.json`` or
``<project>/.agents/.awesome-skills/state.json``), so uninstalling a
project can never touch a user-wide install.

Records keep three things the reference's name-only manifest does not:

* ``mode`` — link or copy, because removal differs;
* ``path`` — the path observed at install time, so a later
  ``CLAUDE_CONFIG_DIR`` change can still clean up the old mirror;
* ``version`` — what release wrote it, for ``check`` and doctor.

Paths are still *resolved* from the registry at call time for writing:
the recorded path is a fallback for cleanup, not the source of truth.

The shell installer's ``manifest.tsv`` is imported read-only when no
``state.json`` exists yet, so the two CLIs can coexist on one machine.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import shared
from .shared import InstallerError, Scope
from .types import AgentTarget

STATE_VERSION = 1
STATE_FILE = "state.json"
LEGACY_FILE = "manifest.tsv"
HUB_TARGET = "hub"
LEGACY_TARGET = "legacy"


class StateError(InstallerError):
    """The state manifest exists but cannot be written safely."""


@dataclass(frozen=True)
class Record:
    mode: str
    version: str
    path: Path


@dataclass
class State:
    path: Path
    scope_label: str
    hub: Path
    targets: dict[str, dict[str, Record]] = field(default_factory=dict)
    corrupt: bool = False
    adopted_legacy: bool = False

    # -- io ---------------------------------------------------------------

    @classmethod
    def load(cls, scope: Scope, targets: Sequence[AgentTarget]) -> "State":
        path = shared.state_dir(scope) / STATE_FILE
        hub = shared.hub_dir(scope)
        state = cls(path=path, scope_label=scope.label, hub=hub)

        if path.exists():
            try:
                raw = json.loads(shared.read_text(path).lstrip("\ufeff"))
            except (OSError, ValueError):
                state.corrupt = True
                return state
            if not isinstance(raw, dict):
                state.corrupt = True
                return state
            raw_targets = raw.get("targets")
            if isinstance(raw_targets, dict):
                for target_id, skills in raw_targets.items():
                    if not isinstance(skills, dict):
                        continue
                    bucket: dict[str, Record] = {}
                    for name, value in skills.items():
                        record = _parse_record(value, hub=hub, name=name)
                        if record is not None:
                            bucket[str(name)] = record
                    if bucket:
                        state.targets[str(target_id)] = bucket
            return state

        state.targets = _import_legacy(scope, targets)
        state.adopted_legacy = bool(state.targets)
        return state

    def save(self, dry_run: bool = False) -> None:
        if dry_run:
            return
        payload = {
            "version": STATE_VERSION,
            "tool": "awesome-skills",
            "scope": self.scope_label,
            "hub": str(self.hub),
            "targets": {
                target_id: {
                    name: {
                        "mode": record.mode,
                        "version": record.version,
                        "path": str(record.path),
                    }
                    for name, record in sorted(skills.items())
                }
                for target_id, skills in sorted(self.targets.items())
                if skills
            },
        }
        if self.corrupt and self.path.exists():
            # Keep the unreadable file for forensics instead of silently
            # replacing the only ownership record we have.
            try:
                shutil.copyfile(self.path, self.path.with_name(self.path.name + ".corrupt"))
            except OSError:
                pass

        if not payload["targets"]:
            if self.adopted_legacy or (self.path.parent / LEGACY_FILE).exists():
                # A tombstone: deleting state.json would make the next load
                # re-import the shell's stale manifest.tsv and resurrect
                # records for files this uninstall already removed.
                self.path.parent.mkdir(parents=True, exist_ok=True)
                shared.atomic_write(
                    self.path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                )
                self.adopted_legacy = False
                return
            if self.corrupt:
                # Keep the corrupt manifest for forensics instead of
                # deleting the only ownership record we have.
                self.path.parent.mkdir(parents=True, exist_ok=True)
                shared.atomic_write(
                    self.path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                )
                self.corrupt = False
                return
            # Full reversal: remove the manifest, and the folder when it
            # is empty (backups, if any, keep it alive).
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as err:
                raise StateError(f"cannot remove {self.path}: {err}") from err
            try:
                self.path.parent.rmdir()
            except OSError:
                pass
            return

        shared.atomic_write(self.path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        self.corrupt = False

    # -- queries ----------------------------------------------------------

    def is_empty(self) -> bool:
        return not any(self.targets.values())

    def configured(self, target: str) -> bool:
        return bool(self.targets.get(target))

    def any_configured(self) -> bool:
        return any(self.configured(target) for target in self.targets)

    def skills(self, target: str) -> list[str]:
        return list(self.targets.get(target, {}))

    def get(self, target: str, name: str) -> Record | None:
        return self.targets.get(target, {}).get(name)

    def targets_for(self, name: str) -> list[str]:
        return [target for target, skills in self.targets.items() if name in skills]

    def all_records(self) -> list[tuple[str, str, Record]]:
        out: list[tuple[str, str, Record]] = []
        for target, skills in self.targets.items():
            for name, record in skills.items():
                out.append((target, name, record))
        return out

    def record_count(self) -> int:
        return sum(len(skills) for skills in self.targets.values())

    # -- mutations --------------------------------------------------------

    def record(self, target: str, name: str, mode: str, version: str, path: Path) -> None:
        self.targets.setdefault(target, {})[name] = Record(mode, version, Path(path))

    def forget(self, target: str, name: str | None = None) -> None:
        if target not in self.targets:
            return
        if name is None:
            del self.targets[target]
            return
        self.targets[target].pop(name, None)
        if not self.targets[target]:
            del self.targets[target]

    def forget_all(self, target: str, names: Iterable[str]) -> None:
        for name in names:
            self.forget(target, name)


def _parse_record(value: object, hub: Path, name: str) -> Record | None:
    if not isinstance(value, dict):
        return None
    raw_path = value.get("path")
    path = Path(raw_path).expanduser() if isinstance(raw_path, str) and raw_path else hub / name
    mode = str(value.get("mode") or "copy")
    version = str(value.get("version") or "")
    return Record(mode=mode, version=version, path=path)


def _import_legacy(scope: Scope, targets: Sequence[AgentTarget]) -> dict[str, dict[str, Record]]:
    """Adopt the shell installer's TSV manifest, best effort.

    Rows never disappear because of this: an unreadable row is skipped,
    not deleted. Aliases are recovered by matching each mirror's parent
    directory against the reviewed table; when several aliases share a
    directory only the first is recorded (the file is shared anyway).
    """
    path = shared.state_dir(scope) / LEGACY_FILE
    try:
        text = shared.read_text(path)
    except OSError:
        return {}

    alias_by_dir: dict[Path, str] = {}
    for target in targets:
        directory = target.directory(scope)
        if directory is not None:
            alias_by_dir.setdefault(directory, target.alias)

    imported: dict[str, dict[str, Record]] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        name, kind, mode, raw_path = fields[0], fields[1], fields[2], fields[3]
        version = fields[4] if len(fields) > 4 else ""
        if not name or not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if kind == "hub":
            target_id = HUB_TARGET
        elif kind == "mirror":
            target_id = alias_by_dir.get(path.parent, LEGACY_TARGET)
        else:
            continue
        bucket = imported.setdefault(target_id, {})
        if name not in bucket:
            bucket[name] = Record(mode=mode or "copy", version=version, path=path)
    return imported
