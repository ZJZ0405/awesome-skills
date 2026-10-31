"""CLI: ``awesome-skills-py install | uninstall | list | check | doctor | tools``.

Deliberately small — argparse, no rendering library, no colour. The
commands mirror the shell installer's vocabulary, plus ``check`` (drift)
from the reference project. Exit codes: 0 success, 1 failure or drift,
2 usage (argparse).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, orchestrator, registry, shared, skills, wizard
from .clack import Clack
from .shared import InstallerError, Scope, split_csv, tildify
from .state import HUB_TARGET, State
from .types import AgentTarget, FileResult

PROG = "awesome-skills-py"
_HIDDEN_ACTIONS = ("unchanged", "not-found")


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _load(args: argparse.Namespace) -> tuple[Path, list[AgentTarget], Scope]:
    root = registry.repo_root(getattr(args, "root", None))
    targets = registry.load_targets(root)
    scope = registry.scope_from_args(getattr(args, "project", None))
    return root, targets, scope


def _header(scope: Scope, root: Path, mode: str | None = None) -> None:
    print(f"{PROG} {__version__} — {shared.tildify(root)}")
    print(f"scope:  {scope.label}")
    print(f"hub:    {shared.tildify(shared.hub_dir(scope))}")
    if mode:
        print(f"mode:   {mode}")
    print("")


def _print_files(files: list[FileResult], *, verbose: bool) -> None:
    for file in files:
        if not verbose and file.action in _HIDDEN_ACTIONS:
            continue
        detail = f" {file.detail}" if file.detail else ""
        print(f"  {file.action:<9}{detail:<10} {tildify(file.path)}")


def _print_notes(notes: list[str], *, verbose: bool) -> None:
    for note in notes:
        if verbose:
            print(f"  note: {note}")


def _warn_state(state: State) -> None:
    if state.corrupt:
        print(
            f"warning: {tildify(state.path)} is unreadable; treating it as empty "
            "(nothing will be deleted because of this).",
            file=sys.stderr,
        )
    elif state.adopted_legacy:
        print(
            "note: adopted the shell installer's manifest.tsv; it will be "
            "rewritten as state.json on the next change.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def cmd_install(args: argparse.Namespace) -> int:
    if wizard.guided(args):
        return _guided_install(args)

    root, targets, scope = _load(args)
    specs = skills.resolve_sources(root, split_csv(args.skill))
    selected = registry.resolve_selection(targets, split_csv(args.tool), scope)
    mode = "copy" if args.copy else "link"

    ui = None
    if not args.quiet and not args.dry_run and _stdout_is_tty():
        ui = Clack()
        ui.intro(f"awesome-skills v{__version__}")
    code = _perform_install(args, root, targets, scope, specs, selected, mode, ui=ui)
    if ui is not None:
        if code == 0:
            if selected:
                ui.note(
                    "Start a new agent session.\nMost agents scan skills only at startup.",
                    "Next",
                )
            ui.outro(
                "Done! Restart your agent to use the new skills."
                if selected
                else "Done! The skills are in the shared hub."
            )
        else:
            ui.outro("Finished with errors — see the messages above.")
    return code


def _stdout_is_tty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _guided_install(args: argparse.Namespace) -> int:
    ui = Clack()
    ui.intro(f"awesome-skills v{__version__}")
    try:
        if not args.project:
            args.project = wizard.ask_scope(ui)
        root, targets, scope = _load(args)
        plan = wizard.collect(
            root,
            targets,
            scope,
            ui=ui,
            version=shared.repo_version(root),
            mode="copy" if args.copy else ("link" if args.link else None),
            force=args.force,
        )
    except wizard.WizardAbort:
        ui.cancel("Installation cancelled.")
        return 0

    if plan is None:
        ui.cancel("Installation cancelled.")
        return 0

    args.force = args.force or plan.force
    code = _perform_install(
        args,
        root,
        targets,
        scope,
        list(plan.skills),
        list(plan.targets),
        plan.mode,
        ui=ui,
    )
    if code == 0:
        if plan.targets:
            ui.note(
                "Start a new agent session.\nMost agents scan skills only at startup.",
                "Next",
            )
            ui.outro("Done! Restart your agent to use the new skills.")
        else:
            ui.outro("Done! The skills are in the shared hub.")
    else:
        ui.outro("Finished with errors — see the messages above.")
    return code


def _perform_install(
    args: argparse.Namespace,
    root: Path,
    targets: list[AgentTarget],
    scope: Scope,
    specs: list,
    selected: list,
    mode: str,
    *,
    ui: Clack | None = None,
) -> int:
    state = State.load(scope, targets)

    if ui is not None:
        ui.step(
            f"Installing {len(specs)} skill(s) into {tildify(shared.hub_dir(scope))}"
        )
    elif not args.quiet:
        _header(scope, root, mode)
        _warn_state(state)
        print(f"skills: {len(specs)} selected")
        print("")

    if not selected:
        if ui is not None:
            ui.info("No mirrors selected — installing into the shared hub only.")
        else:
            requested = ",".join(args.tool or ()).strip().lower()
            if requested in ("", "auto"):
                print(
                    "No agent detected; installing to the shared hub only. "
                    "Add mirrors later with --tool.",
                    file=sys.stderr,
                )
            else:
                print("No mirrors requested; installing to the shared hub only.", file=sys.stderr)

    outcome = orchestrator.install(
        scope,
        selected,
        specs,
        state,
        mode=mode,
        force=args.force,
        dry_run=args.dry_run,
        version=shared.repo_version(root),
    )

    if ui is not None:
        for report in outcome.reports:
            if report.error:
                ui.error(f"{report.alias}: {report.error}")
            for note in report.notes:
                ui.info(f"{report.alias}: {note}")
        for note in outcome.notes:
            ui.info(note)
    elif not args.quiet:
        print(f"hub  {tildify(shared.hub_dir(scope))}")
        _print_files(outcome.hub_files, verbose=args.verbose)
        print("")
        for report in outcome.reports:
            changed = [
                file
                for file in report.files
                if args.verbose or file.action not in _HIDDEN_ACTIONS
            ]
            if report.error or changed or (args.verbose and report.notes):
                label = f"{report.alias}  {report.status}"
                if report.error:
                    label += f" — {report.error}"
                print(label)
                _print_files(changed, verbose=args.verbose)
                _print_notes(report.notes, verbose=args.verbose)
        if outcome.notes:
            print("")
            for note in outcome.notes:
                print(f"note: {note}")

    created = outcome.count("created")
    updated = outcome.count("updated")
    unchanged = outcome.count("unchanged")
    if ui is not None:
        if outcome.failed:
            ui.error("Some entries failed; see the messages above.")
        elif created or updated:
            ui.success(
                f"Installed {len(specs)} skill(s) into {tildify(shared.hub_dir(scope))}"
            )
        else:
            ui.info("Everything was already up to date.")
        ui.info(f"{created} created, {updated} updated, {unchanged} unchanged")
        return 1 if outcome.failed else 0

    print("")
    if args.dry_run:
        print(f"Dry run: would create {created}, update {updated}, keep {unchanged} path(s).")
        return 0
    print(
        f"Installed into {tildify(shared.hub_dir(scope))}: "
        f"{created} created, {updated} updated, {unchanged} unchanged; "
        f"{len(outcome.reports)} tool entr(ies) processed."
    )
    if outcome.failed:
        print("Some entries failed; see the errors above.", file=sys.stderr)
        return 1
    if not selected:
        print("No agent mirrors were created (hub only).")
    else:
        print("Start a new agent session to pick the skills up.")
    return 0


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def cmd_uninstall(args: argparse.Namespace) -> int:
    root, targets, scope = _load(args)
    state = State.load(scope, targets)
    tool_values = split_csv(args.tool)
    skill_values = split_csv(args.skill)

    if args.all and tool_values:
        raise InstallerError("--all and --tool are mutually exclusive")
    if not args.all and not tool_values:
        raise InstallerError("choose what to remove: --all, or --tool LIST [--skill LIST]")
    if skill_values and not args.all and not tool_values:
        raise InstallerError("--skill needs --all (or a --tool to limit the removal)")

    if args.all:
        selected = [target for target in targets if state.configured(target.alias)]
    else:
        selected = registry.resolve_selection(targets, tool_values, scope)
        if not selected:
            raise InstallerError("no tool matched, so there is nothing to remove")

    ui = None
    if not args.quiet and not args.dry_run and _stdout_is_tty():
        ui = Clack()
        ui.intro(f"awesome-skills v{__version__} — uninstall")
        if state.corrupt:
            ui.warn(f"{tildify(state.path)} is unreadable; treating it as empty.")
        elif state.adopted_legacy:
            ui.info("Adopted the shell installer's manifest.tsv; rewriting as state.json.")
    elif not args.quiet:
        _header(scope, root)
        _warn_state(state)

    outcome = orchestrator.uninstall(
        scope,
        selected,
        state,
        all_targets=targets,
        remove_all=args.all,
        skill_filter=skill_values,
        dry_run=args.dry_run,
    )

    removed = outcome.count("removed")
    kept = outcome.count("kept")
    missing = outcome.count("not-found")

    if ui is not None:
        for report in outcome.reports:
            if report.error:
                ui.error(f"{report.alias}: {report.error}")
        for file in outcome.files:
            if file.action == "removed":
                ui.success(f"removed {tildify(file.path)}")
            elif file.action == "kept":
                ui.info(f"kept {tildify(file.path)}")
            elif args.verbose and file.action == "not-found":
                ui.info(f"already gone {tildify(file.path)}")
        for note in outcome.notes:
            ui.info(note)
        if removed:
            ui.success(f"Removed {removed} path(s); kept {kept}; {missing} already gone.")
        elif kept or missing:
            ui.info(f"Removed 0 path(s); kept {kept}; {missing} already gone.")
        else:
            ui.info("Nothing recorded for this selection — nothing to remove.")
        if outcome.failed:
            ui.outro("Finished with errors — see the messages above.")
            return 1
        ui.outro("Done!")
        return 0

    if not args.quiet:
        for report in outcome.reports:
            if report.error:
                print(f"{report.alias}  failed — {report.error}")
            if report.notes and (args.verbose or report.status == "kept"):
                for note in report.notes:
                    print(f"note: {note}")
        _print_files(outcome.files, verbose=args.verbose)
        if outcome.notes:
            print("")
            for note in outcome.notes:
                print(f"note: {note}")

    print("")
    if args.dry_run:
        print(f"Dry run: would remove {removed} path(s), keep {kept}.")
        return 0
    if removed == 0 and kept == 0 and missing == 0:
        print("Nothing recorded for this selection — nothing to remove.")
    else:
        print(f"Removed {removed} path(s); kept {kept}; {missing} already gone.")
    if outcome.failed:
        print("Some removals failed; see the errors above.", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    root, targets, scope = _load(args)
    state = State.load(scope, targets)
    show_available = not args.installed
    show_installed = not args.available

    if show_available:
        print(f"Available skills ({tildify(root)})")
        for spec in skills.discover(root):
            description = spec.description or "(no description)"
            if len(description) > 110:
                description = description[:107] + "..."
            print(f"  {spec.name:<24} {description}")
        print("")

    if show_installed:
        print(f"Installed ({scope.label}: {tildify(shared.hub_dir(scope))})")
        _warn_state(state)
        records = state.all_records()
        if not records:
            print("  (nothing recorded)")
        else:
            hub = shared.hub_dir(scope)
            rows = []
            for target_id, name, record in records:
                rows.append((name, target_id, record, _record_state(target_id, name, record, scope, targets, hub)))
            for name, target_id, record, status in sorted(rows, key=lambda row: (row[0], row[1])):
                print(
                    f"  {name:<24} {target_id:<12} {record.mode:<5} {status:<9} "
                    f"{tildify(record.path)}"
                )
    return 0


def _record_state(
    target_id: str,
    name: str,
    record,
    scope: Scope,
    targets: list[AgentTarget],
    hub: Path,
) -> str:
    if not (record.path.exists() or record.path.is_symlink()):
        return "MISSING"
    if not skills.is_owned(record.path, hub, name):
        return "UNMANAGED"
    target = registry.find(targets, target_id)
    if target is not None and target_id != HUB_TARGET:
        directory = target.directory(scope)
        if directory is not None and directory / name != record.path:
            return "MOVED"
    if record.mode == "copy" and target_id != HUB_TARGET:
        hub_copy = hub / name
        if hub_copy.is_dir() and shared.fingerprint(record.path) != shared.fingerprint(hub_copy):
            return "STALE"
    return "ok"


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    root, targets, scope = _load(args)
    state = State.load(scope, targets)
    _warn_state(state)
    if state.corrupt:
        print(f"cannot check {tildify(state.path)}: the manifest is unreadable", file=sys.stderr)
        return 1
    drift = orchestrator.check(scope, state, targets)
    if not drift:
        print(f"All recorded entries are present ({scope.label}).")
        return 0
    print(f"Drift in {scope.label} install:")
    for item in drift:
        print(f"  drift {item.target:<12} {item.skill:<24} {item.reason}")
        print(f"        {tildify(item.path)}")
    print("Re-run `awesome-skills-py install` for the affected tools.")
    return 1


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    root, targets, scope = _load(args)
    state = State.load(scope, targets)
    report = orchestrator.doctor(scope, root, targets, state)
    print(f"{PROG} doctor")
    print("")
    marks = {"ok": "✓", "warn": "!", "bad": "✗", "info": "·"}
    for level, text in report.lines:
        print(f"  {marks.get(level, '·')} {text}")
    print("")
    if report.errors:
        print(f"{report.errors} error(s), {report.warnings} warning(s)")
        return 1
    print(f"no errors, {report.warnings} warning(s)")
    return 0


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def cmd_tools(args: argparse.Namespace) -> int:
    root, targets, scope = _load(args)
    del scope
    print(f"Supported tools ({len(targets)}; source: {tildify(root / 'lib' / 'agents.tsv')})")
    print("")
    print(f"{'ALIAS':<18} {'PROJECT':<30} {'GLOBAL':<46} DETECTED")
    print(f"{'-' * 5:<18} {'-' * 7:<30} {'-' * 6:<46} {'-' * 8}")
    for target in targets:
        detected = target.detect().installed
        if args.detected and not detected:
            continue
        project = _display_template(target.project_template)
        global_dir = _display_template(target.global_template)
        print(f"{target.alias:<18} {project:<30} {global_dir:<46} {'yes' if detected else '-'}")
    return 0


def _display_template(template: str) -> str:
    try:
        value = shared.expand_template(template)
    except InstallerError:
        return template
    return value.replace(str(shared.home()), "~", 1) if value.startswith(str(shared.home())) else value


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project",
        metavar="DIR",
        help="install into a project instead of globally",
    )
    common.add_argument("-q", "--quiet", action="store_true", help="only warnings, errors and summaries")
    common.add_argument("-v", "--verbose", action="store_true", help="also show unchanged paths and notes")

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Install agent skills from this repository into a shared hub and "
            "mirror them into each agent's skills directory."
        ),
    )
    parser.add_argument(
        "--root",
        metavar="DIR",
        help="repository root (default: this checkout, AWESOME_SKILLS_ROOT, or cwd)",
    )
    parser.add_argument("-V", "--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def target_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--tool", action="append", metavar="LIST",
                       help="comma-separated aliases, or auto/all/none (default: auto)")
        p.add_argument("--skill", action="append", metavar="LIST",
                       help="comma-separated skill names (default: every skill)")

    p = sub.add_parser("install", parents=[common], help="install skills and mirrors")
    target_flags(p)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--copy", action="store_true", help="copy instead of symlinking mirrors")
    mode.add_argument("--link", action="store_true", help="symlink mirrors (default)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="never prompt; use the auto-detected tools (for scripts/CI)")
    p.add_argument("--force", action="store_true",
                   help="back up and replace paths that were not installed by us")
    p.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", parents=[common], help="remove what this tool recorded")
    p.add_argument("--all", action="store_true", help="remove mirrors and the shared hub copies")
    p.add_argument("--tool", action="append", metavar="LIST", help="comma-separated aliases")
    p.add_argument("--skill", action="append", metavar="LIST", help="limit to these skills")
    p.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("list", parents=[common], help="show available and installed skills")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--available", action="store_true", help="only skills in this repository")
    group.add_argument("--installed", action="store_true", help="only recorded installs")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("check", parents=[common], help="exit 1 when a recorded entry went missing")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("doctor", parents=[common], help="check the repository, state and symlink support")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("tools", help="list supported tool aliases and their directories")
    p.add_argument("--detected", action="store_true", help="only tools detected on this machine")
    p.set_defaults(func=cmd_tools)

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if not argv and wizard.interactive():
        # Bare invocation on a terminal: guide instead of printing usage.
        argv = ["install"]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except InstallerError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # `awesome-skills-py list | head` is a normal thing to do.
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
