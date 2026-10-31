"""Interactive guide rendered like codegraph's clack installer.

Codegraph asks through ``@clack/prompts``: a guide rail, diamond steps,
green submit frames, a ``note`` box, and an ``outro``. This module keeps
that flow for skills:

    intro -> scope -> agent multiselect -> skill multiselect -> mode
          -> dry-run plan (note) -> confirm -> install -> outro

Only a real terminal reaches this code. ``--yes``, pipes and CI keep the
non-interactive CLI path, and the shell installer never prompts at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import orchestrator, shared, skills
from .clack import Clack, Option, UIAbort, complete_path
from .shared import InstallerError, Scope, tildify
from .state import State
from .types import MODE_COPY, MODE_LINK, AgentTarget, SkillSpec

WizardAbort = UIAbort

#: Shown first in the agent multiselect; the other ~70 stay one scroll down.
POPULAR_ALIASES = (
    "claude-code",
    "codex",
    "cursor",
    "gemini-cli",
    "github-copilot",
    "opencode",
    "dsh",
    "cline",
    "windsurf",
    "zed",
)


@dataclass(frozen=True)
class Plan:
    targets: tuple[AgentTarget, ...]
    skills: tuple[SkillSpec, ...]
    mode: str
    force: bool = False


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def ask_scope(ui: Clack) -> str | None:
    """Ask global vs project; returns a project path or ``None`` for global."""
    choice = ui.select(
        "Apply skills to all your projects, or just this one?",
        [
            Option("global", "All projects", hint="~/.agents/skills"),
            Option("project", "Just this project", hint="./.agents/skills"),
        ],
        initial=0,
    )
    if choice == "global":
        return None
    current = os.getcwd()
    return ui.text(
        "Project directory",
        default=current,
        initial=current,
        validate=_project_error,
        complete=complete_path,
    )


def collect(
    root: Path,
    targets: Sequence[AgentTarget],
    scope: Scope,
    *,
    ui: Clack,
    version: str = "dev",
    mode: str | None = None,
    force: bool = False,
) -> Plan | None:
    """Run the guide and return a confirmed plan, or ``None`` when cancelled."""
    selected = _ask_targets(ui, targets, scope)
    specs = _ask_skills(ui, root)
    mode = mode or _ask_mode(ui)

    while True:
        try:
            dry = orchestrator.install(
                scope,
                selected,
                specs,
                State.load(scope, targets),
                mode=mode,
                force=force,
                dry_run=True,
                version=version,
            )
        except InstallerError as err:
            ui.warn(str(err))
            if force:
                return None
            if not ui.confirm("Back up those paths and replace them?", initial=False):
                return None
            force = True
            continue

        ui.note(_plan_text(dry, scope, selected, specs, mode, force), "Plan")
        if ui.confirm("Proceed with the install?", initial=True):
            return Plan(tuple(selected), tuple(specs), mode, force)
        return None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _ask_targets(
    ui: Clack, targets: Sequence[AgentTarget], scope: Scope
) -> list[AgentTarget]:
    detected = {target.alias: target.detect().installed for target in targets}
    by_alias = {target.alias: target for target in targets}
    popular = [by_alias[alias] for alias in POPULAR_ALIASES if alias in by_alias]
    popular_set = {target.alias for target in popular}
    ordered = (
        [target for target in popular if detected[target.alias]]
        + [target for target in popular if not detected[target.alias]]
        + [
            target
            for target in targets
            if detected[target.alias] and target.alias not in popular_set
        ]
        + [
            target
            for target in targets
            if not detected[target.alias] and target.alias not in popular_set
        ]
    )

    options: list[Option] = []
    initial: list[str] = []
    for target in ordered:
        directory = target.directory(scope)
        flag = "(detected)" if detected[target.alias] else "(not found)"
        if directory is None:
            options.append(
                Option(
                    target.alias,
                    f"{target.alias} (no {scope.label} directory)",
                    disabled=True,
                )
            )
            continue
        hint = "reads the shared hub directly" if target.reads_hub(scope) else None
        options.append(Option(target.alias, f"{target.alias} {flag}", hint=hint))
        if detected[target.alias]:
            initial.append(target.alias)

    if not initial and any(target.alias == "claude-code" for target in targets):
        # Match codegraph's least-surprise fallback: a machine with no
        # probe hit still gets the most common agent pre-selected.
        initial = ["claude-code"]

    chosen = ui.multiselect(
        "Which agents should awesome-skills configure?",
        options,
        initial=initial,
        required=False,
    )
    wanted = set(chosen)
    return [target for target in targets if target.alias in wanted]


def _ask_skills(ui: Clack, root: Path) -> list[SkillSpec]:
    available = skills.discover(root)
    options = [
        Option(
            spec.name,
            spec.name,
            hint=_short_text(spec.description, 64) or None,
        )
        for spec in available
    ]
    chosen = ui.multiselect(
        "Which skills should be installed?",
        options,
        initial=[spec.name for spec in available],
        required=True,
    )
    wanted = set(chosen)
    return [spec for spec in available if spec.name in wanted]


def _ask_mode(ui: Clack) -> str:
    choice = ui.select(
        "How should the per-agent mirrors be written?",
        [
            Option("link", "link", hint="symlink to the hub, one copy on disk"),
            Option("copy", "copy", hint="independent copy; for filesystems without symlinks"),
        ],
        initial=1 if os.name == "nt" else 0,
    )
    return MODE_LINK if choice == "link" else MODE_COPY


def _plan_text(
    dry,
    scope: Scope,
    selected: Sequence[AgentTarget],
    specs: Sequence[SkillSpec],
    mode: str,
    force: bool,
) -> str:
    lines = [
        f"scope:  {scope.label}",
        f"hub:    {tildify(shared.hub_dir(scope))}",
    ]
    if selected:
        lines.append(
            f"tools:  {len(selected)}  " + _short_list([t.alias for t in selected], 8)
        )
    else:
        lines.append("tools:  hub only (no mirrors)")
    lines.append(f"skills: {len(specs)}  " + _short_list([spec.name for spec in specs], 8))
    lines.append(f"mode:   {mode}")
    if force:
        lines.append("force:  yes — foreign paths are backed up first")
    lines.append(
        f"paths:  {dry.count('created')} create, {dry.count('updated')} update, "
        f"{dry.count('unchanged')} unchanged"
    )
    for note in dry.notes:
        lines.append(f"note:   {note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_error(value: str) -> str | None:
    if not value.strip():
        return "a directory is required"
    try:
        shared.Scope.project(value)
    except InstallerError as err:
        return f"{err} — create it first, or press Enter for the current directory"
    return None


def _short_list(items: Sequence[str], limit: int) -> str:
    text = ", ".join(items[:limit])
    if len(items) > limit:
        text += f", +{len(items) - limit}"
    return text


def _short_text(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def guided(args) -> bool:
    """True when a human is at an interactive terminal and gave no choices."""
    if getattr(args, "yes", False) or getattr(args, "dry_run", False):
        return False
    if getattr(args, "tool", None) or getattr(args, "skill", None):
        return False
    return interactive()


def interactive() -> bool:
    """True when both ends are a terminal (bare invocation uses this)."""
    return _is_tty(_stdin()) and _is_tty(_stdout())


def _stdin():
    import sys

    return sys.stdin


def _stdout():
    import sys

    return sys.stdout


def _is_tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False
