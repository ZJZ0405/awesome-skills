"""Contract and integration tests for the optional Python installer.

Stdlib only: ``PYTHONPATH=installer/src python3 -m unittest discover -s
installer/tests``. Every test gets a throwaway HOME, project, PATH and
repository fixture, so nothing touches the real machine.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from awesome_skills import cli, orchestrator, registry, shared, skills, wizard
from awesome_skills.clack import Clack, Option, complete_path
from awesome_skills.state import HUB_TARGET, State
from awesome_skills.types import MODE_COPY, MODE_LINK, SkillError, SkillSpec

ENV_KEYS = (
    "HOME",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "AWESOME_SKILLS_HOME",
    "ALPHA_HOME",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PATH",
)


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="awesome-py-test-"))
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.bin = self.tmp / "bin"
        self.repo = self.tmp / "repo"
        for folder in (self.home, self.project, self.bin, self.repo / "lib", self.repo / "skills"):
            folder.mkdir(parents=True)
        (self.repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")

        self._env = {key: os.environ.get(key) for key in ENV_KEYS}
        os.environ["HOME"] = str(self.home)
        os.environ["USERPROFILE"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")
        for key in ("AWESOME_SKILLS_HOME", "ALPHA_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            os.environ.pop(key, None)
        self._cwd = Path.cwd()
        os.chdir(self.project)

        self.write_table()
        self.write_skill("alpha", "Alpha skill does alpha things.")
        self.write_skill("beta", "Beta skill.")
        self.write_skill("cat/gamma", "Gamma skill.")
        self.write_skill("alpha/inner", "Nested; not a separate skill.")
        self.write_skill("too/deep/x/epsilon", "Too deep to be discovered.")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------

    def write_table(self, rows: list[str] | None = None) -> None:
        rows = rows or [
            "# alias\tproject\tglobal\tdetect",
            "alpha\t.alpha/skills\t${ALPHA_HOME:-~/.alpha}/skills\tcmd:alpha-cli;${ALPHA_HOME:-~/.alpha}",
            "beta\t.agents/skills\t~/.agents/skills\tcmd:beta-cli",
            "gamma\t.gamma/skills\t~/.gamma/skills\t~/.gamma",
            "shared-a\t.shared/skills\t${XDG_CONFIG_HOME:-~/.config}/shared/skills\tcmd:shared-a",
            "shared-b\t.shared/skills\t${XDG_CONFIG_HOME:-~/.config}/shared/skills\tcmd:shared-b",
            "project-only\t.ponly/skills\tN/A\tN/A",
        ]
        (self.repo / "lib" / "agents.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def write_skill(self, rel: str, description: str) -> Path:
        folder = self.repo / "skills" / rel
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / "SKILL.md").exists():
            (folder / "SKILL.md").write_text(
                f"---\nname: {folder.name}\ndescription: {description}\n---\n\n# {folder.name}\n",
                encoding="utf-8",
            )
        return folder

    def fake_binary(self, name: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        shared.clear_command_cache()

    def detected(self, *aliases: str) -> None:
        for alias in aliases:
            if alias == "alpha":
                (self.home / ".alpha").mkdir(exist_ok=True)
            elif alias == "gamma":
                (self.home / ".gamma").mkdir(exist_ok=True)
            else:
                self.fake_binary(f"{alias}-cli")

    # -- helpers ----------------------------------------------------------

    def targets(self):
        return registry.load_targets(self.repo)

    def scope(self):
        return shared.Scope.global_()

    def state(self, scope=None):
        return State.load(scope or self.scope(), self.targets())

    def specs(self, *names):
        return skills.resolve_sources(self.repo, list(names))

    def install(self, tools, *names, mode="link", force=False, dry_run=False, scope=None):
        scope = scope or self.scope()
        selected = registry.resolve_selection(self.targets(), [tools], scope)
        return orchestrator.install(
            scope,
            selected,
            self.specs(*names),
            self.state(scope),
            mode=mode,
            force=force,
            dry_run=dry_run,
            version="9.9.9",
        )

    def run_cli(self, *args, expect=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--root", str(self.repo), *args])
        if expect is not None:
            self.assertEqual(code, expect, f"stdout:\n{out.getvalue()}\nstderr:\n{err.getvalue()}")
        return code, out.getvalue(), err.getvalue()

    def hub(self):
        return shared.hub_dir(self.scope())


class TestRealTable(unittest.TestCase):
    """The reviewed 80-row table is data, so test the data itself."""

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.tmp = Path(tempfile.mkdtemp(prefix="awesome-py-real-"))
        self._env = {key: os.environ.get(key) for key in ENV_KEYS}
        os.environ["HOME"] = str(self.tmp / "home")
        os.environ["USERPROFILE"] = str(self.tmp / "home")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "home" / ".config")
        for key in ("AWESOME_SKILLS_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "ALPHA_HOME"):
            os.environ.pop(key, None)
        Path(os.environ["HOME"]).mkdir(parents=True)

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_row_has_a_path_and_a_read_only_detect(self) -> None:
        targets = registry.load_targets(self.root)
        self.assertGreaterEqual(len(targets), 80)
        scope = shared.Scope.global_()
        project = shared.Scope.project(self.tmp)
        for target in targets:
            with self.subTest(alias=target.alias):
                self.assertTrue(
                    target.directory(scope) is not None or target.directory(project) is not None
                )
                target.detect().installed  # must not raise or write
                describe = target.describe_paths(scope, ["handoff"])
                self.assertTrue(all(path.is_absolute() for path in describe))

    def test_every_row_installs_and_uninstalls(self) -> None:
        targets = registry.load_targets(self.root)
        scope = shared.Scope.global_()
        spec = SkillSpec("handoff", self.root / "skills" / "handoff")
        state = State.load(scope, targets)
        outcome = orchestrator.install(
            scope, targets, [spec], state, mode="link", version="test"
        )
        self.assertFalse(outcome.failed, outcome.notes)
        for target in targets:
            directory = target.directory(scope)
            if directory is None or directory == shared.hub_dir(scope):
                continue
            with self.subTest(alias=target.alias):
                self.assertTrue(
                    skills.mirror_ok(directory / "handoff", shared.hub_dir(scope), "handoff")
                )

        state = State.load(scope, targets)
        orchestrator.uninstall(
            scope,
            targets,
            state,
            all_targets=targets,
            remove_all=True,
        )
        leftovers = [
            path
            for path in Path(os.environ["HOME"]).rglob("*")
            if path.is_file() or path.is_symlink()
        ]
        self.assertEqual(leftovers, [])
        self.assertEqual(list((shared.hub_dir(scope)).iterdir()), [])


class TestPaths(Sandbox):
    def test_environment_override_and_default(self) -> None:
        alpha = registry.find(self.targets(), "alpha")
        self.assertEqual(alpha.directory(self.scope()), self.home / ".alpha" / "skills")
        os.environ["ALPHA_HOME"] = str(self.home / "custom")
        self.assertEqual(alpha.directory(self.scope()), self.home / "custom" / "skills")

    def test_project_paths_are_relative_to_the_project(self) -> None:
        gamma = registry.find(self.targets(), "gamma")
        project = shared.Scope.project(self.project)
        self.assertEqual(gamma.directory(project), self.project / ".gamma" / "skills")
        self.assertIsNone(registry.find(self.targets(), "project-only").directory(self.scope()))
        self.assertEqual(
            registry.find(self.targets(), "project-only").directory(project),
            self.project / ".ponly" / "skills",
        )

    def test_hub_override_moves_only_the_hub(self) -> None:
        os.environ["AWESOME_SKILLS_HOME"] = str(self.home / "elsewhere")
        self.assertEqual(shared.hub_dir(self.scope()), self.home / "elsewhere")
        self.assertEqual(
            shared.state_dir(self.scope()), self.home / ".agents" / ".awesome-skills"
        )

    def test_detection_reads_paths_and_commands_without_writing(self) -> None:
        self.assertFalse(registry.find(self.targets(), "alpha").detect().installed)
        (self.home / ".alpha").mkdir()
        self.fake_binary("beta-cli")
        before = sorted(self.home.rglob("*"))
        self.assertTrue(registry.find(self.targets(), "alpha").detect().installed)
        self.assertTrue(registry.find(self.targets(), "beta").detect().installed)
        self.assertFalse(registry.find(self.targets(), "gamma").detect().installed)
        self.assertEqual(before, sorted(self.home.rglob("*")))

    def test_tildify_keeps_the_link_name(self) -> None:
        target = self.home / "real"
        target.mkdir()
        link = self.home / "mirror"
        os.symlink(target, link)
        self.assertEqual(shared.tildify(link), "~/mirror")


class TestShared(Sandbox):
    def test_atomic_write_goes_through_a_symlink(self) -> None:
        real = self.home / "dotfiles" / "state.json"
        real.parent.mkdir()
        real.write_text("old\n", encoding="utf-8")
        link = self.home / "state.json"
        os.symlink(real, link)

        shared.atomic_write(link, "new\n")
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(encoding="utf-8"), "new\n")

    def test_invalid_template_names_the_problem(self) -> None:
        with self.assertRaises(shared.InstallerError):
            shared.expand_template("${bad name}/skills")
        self.assertEqual(shared.expand_template("${MISSING:-~/x}/y"), "~/x/y")

    def test_fingerprint_ignores_the_marker_and_pycache(self) -> None:
        folder = self.home / "skill"
        folder.mkdir()
        (folder / "SKILL.md").write_text("body\n", encoding="utf-8")
        baseline = shared.fingerprint(folder)
        (folder / ".awesome-skills-skill").write_text("name=skill\n", encoding="utf-8")
        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "x.pyc").write_bytes(b"\0")
        self.assertEqual(shared.fingerprint(folder), baseline)


class TestRegistry(Sandbox):
    def test_table_order_and_aliases(self) -> None:
        self.assertEqual(
            [target.alias for target in self.targets()],
            ["alpha", "beta", "gamma", "shared-a", "shared-b", "project-only"],
        )

    def test_duplicate_alias_is_a_repository_error(self) -> None:
        self.write_table(
            [
                "alpha\t.a/skills\t~/.a/skills\tN/A",
                "alpha\t.b/skills\t~/.b/skills\tN/A",
            ]
        )
        with self.assertRaises(registry.RegistryError):
            self.targets()

    def test_wrong_field_count_is_refused(self) -> None:
        self.write_table(["alpha\t.a/skills\t~/.a/skills"])
        with self.assertRaises(registry.RegistryError):
            self.targets()

    def test_selection_auto_all_none_and_unknown(self) -> None:
        targets = self.targets()
        self.detected("alpha", "gamma")
        self.assertEqual(
            [target.alias for target in registry.resolve_selection(targets, [], self.scope())],
            ["alpha", "gamma"],
        )
        self.assertEqual(len(registry.resolve_selection(targets, ["all"], self.scope())), 6)
        self.assertEqual(registry.resolve_selection(targets, ["none"], self.scope()), [])
        with self.assertRaises(registry.RegistryError) as caught:
            registry.resolve_selection(targets, ["gama"], self.scope())
        self.assertIn("did you mean", str(caught.exception))

    def test_shared_directories_group(self) -> None:
        targets = self.targets()
        groups = registry.groups_by_directory(targets, self.scope())
        shared_group = [aliases for directory, aliases in groups if directory == self.home / ".config" / "shared" / "skills"]
        self.assertEqual(len(shared_group), 1)
        self.assertEqual([target.alias for target in shared_group[0]], ["shared-a", "shared-b"])


class TestDiscovery(Sandbox):
    def test_discovery_depth_nesting_and_description(self) -> None:
        names = [spec.name for spec in skills.discover(self.repo)]
        self.assertEqual(names, ["alpha", "beta", "gamma"])
        self.assertIn("Alpha skill", skills.discover(self.repo)[0].description)

    def test_duplicate_names_are_refused(self) -> None:
        self.write_skill("other/alpha", "Another alpha.")
        with self.assertRaises(SkillError) as caught:
            skills.discover(self.repo)
        self.assertIn("duplicate", str(caught.exception))

    def test_invalid_folder_name_is_refused(self) -> None:
        self.write_skill("Bad_Name", "bad")
        with self.assertRaises(SkillError):
            skills.discover(self.repo)

    def test_quoted_and_block_descriptions(self) -> None:
        folder = self.write_skill("delta", "placeholder")
        (folder / "SKILL.md").write_text(
            '---\nname: delta\ndescription: "Quoted: yes"\n---\n', encoding="utf-8"
        )
        self.assertEqual(
            skills.description_of(folder), "Quoted: yes"
        )
        (folder / "SKILL.md").write_text(
            "---\nname: delta\ndescription: >\n  Folded\n  text\n---\n", encoding="utf-8"
        )
        self.assertEqual(skills.description_of(folder), "Folded text")


class TestHub(Sandbox):
    def test_install_is_idempotent_and_excludes_repository_noise(self) -> None:
        spec = self.specs("alpha")[0]
        (spec.source / ".git").mkdir()
        (spec.source / ".git" / "config").write_text("no\n", encoding="utf-8")
        hub = self.hub()

        first = skills.hub_install(spec, hub, "9.9.9")
        self.assertEqual(first.action, "created")
        self.assertTrue((hub / "alpha" / "SKILL.md").is_file())
        self.assertFalse((hub / "alpha" / ".git").exists())
        marker = (hub / "alpha" / ".awesome-skills-skill").read_text(encoding="utf-8")
        self.assertIn("name=alpha", marker)
        self.assertNotIn("installed=", marker)

        stat = (hub / "alpha" / "SKILL.md").stat().st_mtime_ns
        second = skills.hub_install(spec, hub, "9.9.9")
        self.assertEqual(second.action, "unchanged")
        self.assertEqual((hub / "alpha" / "SKILL.md").stat().st_mtime_ns, stat)

    def test_source_change_updates_and_force_backs_up_foreign_paths(self) -> None:
        spec = self.specs("alpha")[0]
        hub = self.hub()
        skills.hub_install(spec, hub, "9.9.9")
        (spec.source / "SKILL.md").write_text("---\nname: alpha\n---\n\nnew body\n", encoding="utf-8")
        result = skills.hub_install(spec, hub, "9.9.9")
        self.assertEqual(result.action, "updated")
        self.assertIn("new body", (hub / "alpha" / "SKILL.md").read_text(encoding="utf-8"))

        foreign = self.hub() / "alpha"
        shutil.rmtree(foreign)
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("user file\n", encoding="utf-8")
        with self.assertRaises(SkillError):
            skills.hub_install(spec, hub, "9.9.9", backup_root=self.tmp / "backups")
        result = skills.hub_install(
            spec, hub, "9.9.9", force=True, backup_root=self.tmp / "backups"
        )
        self.assertEqual(result.action, "updated")
        backups = list((self.tmp / "backups").rglob("SKILL.md"))
        self.assertEqual(len(backups), 1)


class TestMirrors(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.spec = self.specs("alpha")[0]
        self.hub = self.hub()
        skills.hub_install(self.spec, self.hub, "9.9.9")
        self.dest = self.home / ".gamma" / "skills" / "alpha"

    def test_link_is_created_and_idempotent(self) -> None:
        first = skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "link")
        self.assertEqual(first.action, "created")
        self.assertTrue(self.dest.is_symlink())
        second = skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "link")
        self.assertEqual(second.action, "unchanged")

    def test_copy_is_idempotent_and_can_switch_modes(self) -> None:
        first = skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "copy")
        self.assertEqual(first.action, "created")
        self.assertFalse(self.dest.is_symlink())
        stat = (self.dest / "SKILL.md").stat().st_mtime_ns
        second = skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "copy")
        self.assertEqual(second.action, "unchanged")
        self.assertEqual((self.dest / "SKILL.md").stat().st_mtime_ns, stat)

        switched = skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "link")
        self.assertEqual(switched.action, "updated")
        self.assertTrue(self.dest.is_symlink())
        switched = skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "copy")
        self.assertEqual(switched.action, "updated")
        self.assertFalse(self.dest.is_symlink())

    def test_foreign_destination_is_refused_without_force(self) -> None:
        self.dest.mkdir(parents=True)
        (self.dest / "SKILL.md").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(SkillError):
            skills.mirror_install(self.spec, self.dest, self.hub / "alpha", "link")
        self.assertIn("mine", (self.dest / "SKILL.md").read_text(encoding="utf-8"))

    def test_remove_refuses_a_folder_we_did_not_install(self) -> None:
        self.dest.mkdir(parents=True)
        (self.dest / "SKILL.md").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(SkillError):
            skills.mirror_remove(self.dest, self.hub, "alpha")
        self.assertTrue(self.dest.is_dir())


class ScriptedUI:
    """Stand-in for ``Clack`` that returns queued answers and records prompts."""

    def __init__(self, selects=(), multis=(), confirms=(), texts=()):
        self._selects = iter(selects)
        self._multis = iter(multis)
        self._confirms = iter(confirms)
        self._texts = iter(texts)
        self.calls = []
        self.lines = []

    def intro(self, title):
        self.calls.append(("intro", title))

    def outro(self, message):
        self.calls.append(("outro", message))

    def cancel(self, message):
        self.calls.append(("cancel", message))

    def info(self, message):
        self.calls.append(("info", message))

    def success(self, message):
        self.calls.append(("success", message))

    def step(self, message):
        self.calls.append(("step", message))

    def warn(self, message):
        self.calls.append(("warn", message))

    def error(self, message):
        self.calls.append(("error", message))

    def note(self, message, title=""):
        self.calls.append(("note", title, message))

    def select(self, message, options, **kwargs):
        self.calls.append(("select", message))
        return next(self._selects)

    def multiselect(self, message, options, **kwargs):
        self.calls.append(("multiselect", message, kwargs.get("initial"), list(options)))
        return next(self._multis)

    def confirm(self, message, **kwargs):
        self.calls.append(("confirm", message))
        return next(self._confirms)

    def text(self, message, **kwargs):
        self.calls.append(("text", message, kwargs))
        validate = kwargs.get("validate")
        while True:
            value = next(self._texts)
            problem = validate(value) if validate else None
            if problem is None:
                return value


class TestWizard(Sandbox):
    def ui(self, **kwargs):
        return ScriptedUI(**kwargs)

    def all_skills(self):
        return [spec.name for spec in skills.discover(self.repo)]

    def test_hub_only_plan(self) -> None:
        ui = self.ui(multis=[[], self.all_skills()], selects=["link"], confirms=[True])
        plan = wizard.collect(
            self.repo, self.targets(), self.scope(), ui=ui, version="9.9.9"
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.targets, ())
        self.assertEqual([spec.name for spec in plan.skills], self.all_skills())
        self.assertEqual(plan.mode, MODE_LINK)
        self.assertFalse(plan.force)
        self.assertTrue(any(call[0] == "note" for call in ui.calls))
        self.assertTrue(any(call[0] == "confirm" for call in ui.calls))

    def test_popular_agents_come_first(self) -> None:
        ui = self.ui(
            multis=[["alpha"], ["alpha"]],
            selects=["link"],
            confirms=[True],
        )
        wizard.collect(self.repo, self.targets(), self.scope(), ui=ui, version="9.9.9")
        call = next(call for call in ui.calls if call[0] == "multiselect")
        aliases = [option.value for option in call[3]]
        self.assertEqual(aliases[:3], ["alpha", "beta", "gamma"])  # no popular aliases in the fixture

    def test_popular_agents_are_the_first_page_of_the_real_table(self) -> None:
        root = Path(__file__).resolve().parents[2]
        targets = registry.load_targets(root)
        ui = self.ui(multis=[[]])
        wizard._ask_targets(ui, targets, shared.Scope.global_())
        options = ui.calls[0][3]
        self.assertEqual(
            {option.value for option in options[:10]},
            set(wizard.POPULAR_ALIASES),
        )

    def test_manual_targets_skills_and_copy_mode(self) -> None:
        ui = self.ui(
            multis=[["alpha", "gamma"], ["alpha", "gamma"]],
            selects=["copy"],
            confirms=[True],
        )
        plan = wizard.collect(
            self.repo, self.targets(), self.scope(), ui=ui, version="9.9.9"
        )
        assert plan is not None
        self.assertEqual([target.alias for target in plan.targets], ["alpha", "gamma"])
        self.assertEqual([spec.name for spec in plan.skills], ["alpha", "gamma"])
        self.assertEqual(plan.mode, MODE_COPY)

    def test_conflict_offers_force_then_proceeds(self) -> None:
        foreign = self.home / ".gamma" / "skills" / "alpha"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("mine\n", encoding="utf-8")
        ui = self.ui(
            multis=[["gamma"], ["alpha"]],
            selects=["link"],
            confirms=[True, True],
        )
        plan = wizard.collect(
            self.repo, self.targets(), self.scope(), ui=ui, version="9.9.9"
        )
        assert plan is not None
        self.assertTrue(plan.force)
        self.assertTrue(any(call[0] == "warn" for call in ui.calls))

    def test_declining_the_plan_returns_none(self) -> None:
        ui = self.ui(
            multis=[[], self.all_skills()],
            selects=["link"],
            confirms=[False],
        )
        plan = wizard.collect(
            self.repo, self.targets(), self.scope(), ui=ui, version="9.9.9"
        )
        self.assertIsNone(plan)

    def test_explicit_mode_and_force_skip_those_questions(self) -> None:
        ui = self.ui(multis=[[], self.all_skills()], confirms=[True])
        plan = wizard.collect(
            self.repo,
            self.targets(),
            self.scope(),
            ui=ui,
            version="9.9.9",
            mode=MODE_COPY,
            force=True,
        )
        assert plan is not None
        self.assertEqual(plan.mode, MODE_COPY)
        self.assertTrue(plan.force)
        self.assertFalse(any(call[0] == "select" for call in ui.calls))

    def test_scope_prompt_validates_the_directory(self) -> None:
        ui = self.ui(
            selects=["project"],
            texts=[str(self.tmp / "missing"), str(self.project)],
        )
        selected = wizard.ask_scope(ui)
        self.assertEqual(selected, str(self.project))

    def test_scope_prompt_can_choose_global(self) -> None:
        ui = self.ui(selects=["global"])
        self.assertIsNone(wizard.ask_scope(ui))

    def test_project_prompt_prefills_the_current_directory(self) -> None:
        ui = self.ui(selects=["project"], texts=[str(self.project)])
        wizard.ask_scope(ui)
        call = next(call for call in ui.calls if call[0] == "text")
        self.assertEqual(call[2].get("initial"), str(Path.cwd()))
        self.assertEqual(call[2].get("default"), str(Path.cwd()))

    def test_skill_hint_is_text_not_characters(self) -> None:
        spec = skills.discover(self.repo)[0]
        hint = wizard._short_text(spec.description, 10)
        self.assertTrue(hint.startswith("Alpha"), hint)
        self.assertLessEqual(len(hint), 10)

    def test_guided_detection_respects_yes_and_explicit_choices(self) -> None:
        base = SimpleNamespace(yes=False, dry_run=False, tool=None, skill=None)
        with mock.patch.object(wizard, "_is_tty", return_value=True):
            self.assertTrue(wizard.guided(base))
            self.assertFalse(wizard.guided(SimpleNamespace(**{**vars(base), "yes": True})))
            self.assertFalse(wizard.guided(SimpleNamespace(**{**vars(base), "tool": ["gamma"]})))
            self.assertFalse(wizard.guided(SimpleNamespace(**{**vars(base), "dry_run": True})))

    def test_cli_uses_the_confirmed_plan(self) -> None:
        gamma = registry.find(self.targets(), "gamma")
        spec = self.specs("alpha")[0]
        plan = wizard.Plan(targets=(gamma,), skills=(spec,), mode=MODE_LINK)
        with mock.patch.object(wizard, "guided", return_value=True), \
             mock.patch.object(wizard, "ask_scope", return_value=None), \
             mock.patch.object(wizard, "collect", return_value=plan):
            code, out, _ = self.run_cli("install", expect=0)
        self.assertIn("Installed", out)
        self.assertTrue((self.home / ".gamma" / "skills" / "alpha").is_symlink())
        self.assertEqual(self.state().skills("gamma"), ["alpha"])


class TestClackFrame(unittest.TestCase):
    def ui(self, **kwargs):
        return Clack(stream=io.StringIO(), color=False, unicode=True, **kwargs)

    def test_select_frames(self) -> None:
        ui = self.ui()
        options = [Option("a", "Alpha"), Option("b", "Beta", hint="the second")]
        self.assertEqual(
            ui.select_lines("Pick one", options, 0),
            ["│", "◆  Pick one", "│  ● Alpha", "│  ○ Beta", "└"],
        )
        self.assertEqual(
            ui.select_submit_lines("Pick one", options, 0),
            ["│", "◇  Pick one", "│  Alpha"],
        )
        self.assertIn("the second", "\n".join(ui.select_lines("Pick one", options, 1)))

    def test_multiselect_frames(self) -> None:
        ui = self.ui()
        options = [Option("a", "Alpha"), Option("b", "Beta")]
        self.assertEqual(
            ui.multiselect_lines("Pick", options, 0, {"a"}),
            ["│", "◆  Pick", "│  ◼ Alpha", "│  ◻ Beta", "└"],
        )
        self.assertEqual(
            ui.multiselect_submit_lines("Pick", options, ["a"]),
            ["│", "◇  Pick", "│  Alpha"],
        )

    def test_confirm_frame(self) -> None:
        ui = self.ui()
        self.assertEqual(
            ui.confirm_lines("Proceed?", True),
            ["│", "◆  Proceed?", "│  ● Yes / ○ No", "└"],
        )
        self.assertEqual(
            ui.confirm_submit_lines("Proceed?", False),
            ["│", "◇  Proceed?", "│  No"],
        )

    def test_note_box(self) -> None:
        ui = self.ui()
        self.assertEqual(
            ui.note_lines("one\ntwo", "Plan"),
            [
                "│",
                "◇  Plan ─╮",
                "│  one   │",
                "│  two   │",
                "├────────╯",
            ],
        )

    def test_ascii_fallback(self) -> None:
        ui = Clack(stream=io.StringIO(), color=False, unicode=False)
        lines = ui.select_lines("Pick", [Option("a", "Alpha")], 0)
        self.assertEqual(lines[1], "*  Pick")
        self.assertEqual(lines[2], "|  > Alpha")
        self.assertEqual(lines[3], "-")

    def test_logs_write_clack_lines(self) -> None:
        stream = io.StringIO()
        ui = Clack(stream=stream, color=False, unicode=True)
        ui.success("ok")
        self.assertEqual(stream.getvalue(), "│\n◆  ok\n")


class TestCompletion(Sandbox):
    def test_common_prefix_unique_match_and_miss(self) -> None:
        base = self.tmp / "paths"
        for name in ("alpha", "alpine", "beta"):
            (base / name).mkdir(parents=True)
        (base / "afile").write_text("x", encoding="utf-8")

        new, hint = complete_path(str(base / "al"))
        self.assertEqual(new, str(base / "alp"))
        self.assertEqual(hint, "2 matches")

        new, hint = complete_path(str(base / "alpha"))
        self.assertEqual(new, str(base / "alpha") + "/")
        self.assertIsNone(hint)

        new, hint = complete_path(str(base / "be"))
        self.assertEqual(new, str(base / "beta") + "/")

        new, hint = complete_path(str(base / "zz"))
        self.assertIsNone(new)
        self.assertIn("no matching", hint or "")

        # Files are not offered for a directory prompt.
        new, hint = complete_path(str(base / "af"))
        self.assertIsNone(new)

    def test_tilde_is_preserved(self) -> None:
        (self.home / "project").mkdir()
        new, hint = complete_path("~/pro")
        self.assertEqual(new, "~/project/")
        self.assertIsNone(hint)

    def test_text_hint_is_rendered(self) -> None:
        ui = Clack(stream=io.StringIO(), color=False, unicode=True)
        lines = ui.text_lines("Project directory", "/tmp", hint="2 matches")
        self.assertIn("2 matches", "\n".join(lines))


class TestSwap(unittest.TestCase):
    def test_failed_swap_restores_the_original(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="awesome-swap-"))
        try:
            dest = tmp / "dest"
            dest.mkdir()
            (dest / "file").write_text("old\n", encoding="utf-8")
            stage = tmp / "stage"
            stage.mkdir()
            (stage / "file").write_text("new\n", encoding="utf-8")

            real_replace = skills.os.replace

            def flaky(src, dst):
                if Path(src) == stage and Path(dst) == dest:
                    raise OSError("simulated failure")
                return real_replace(src, dst)

            with mock.patch.object(skills.os, "replace", side_effect=flaky):
                with self.assertRaises(OSError):
                    skills._swap_in(stage, dest)
            self.assertEqual((dest / "file").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((stage / "file").read_text(encoding="utf-8"), "new\n")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestOrchestratorInstall(Sandbox):
    def test_auto_installs_detected_agents_and_skips_hub_readers(self) -> None:
        self.detected("alpha", "beta")
        outcome = self.install("auto", "alpha")
        statuses = {report.alias: report.status for report in outcome.reports}
        self.assertEqual(statuses["alpha"], "installed")
        self.assertEqual(statuses["beta"], "unchanged")
        self.assertFalse((self.home / ".agents" / "skills" / "alpha").is_symlink())
        self.assertTrue((self.home / ".alpha" / "skills" / "alpha").is_symlink())
        state = self.state()
        self.assertTrue(state.configured("alpha"))
        self.assertTrue(state.configured(HUB_TARGET))
        self.assertEqual(state.get("alpha", "alpha").mode, "link")

    def test_no_detected_agent_means_hub_only(self) -> None:
        outcome = self.install("auto", "alpha")
        self.assertEqual(outcome.reports, [])
        self.assertTrue((self.hub() / "alpha" / "SKILL.md").is_file())
        self.assertEqual(self.state().skills(HUB_TARGET), ["alpha"])

    def test_shared_directory_is_written_once(self) -> None:
        outcome = self.install("shared-a,shared-b", "alpha")
        directory = self.home / ".config" / "shared" / "skills"
        self.assertEqual(sorted(os.listdir(directory)), ["alpha"])
        self.assertEqual(len(outcome.reports), 2)
        state = self.state()
        self.assertEqual(state.skills("shared-a"), ["alpha"])
        self.assertEqual(state.skills("shared-b"), ["alpha"])

    def test_conflict_aborts_before_anything_is_written(self) -> None:
        foreign = self.home / ".gamma" / "skills" / "alpha"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(shared.InstallerError):
            self.install("gamma", "alpha")
        self.assertFalse((self.hub() / "alpha").exists())
        self.assertFalse(shared.state_dir(self.scope()).exists())

    def test_dry_run_writes_nothing(self) -> None:
        self.detected("gamma")
        outcome = self.install("gamma", "alpha", dry_run=True)
        self.assertTrue(any(file.action == "created" for file in outcome.actions()))
        self.assertFalse((self.hub()).exists())
        self.assertFalse((self.home / ".gamma" / "skills").exists())
        self.assertFalse(shared.state_dir(self.scope()).exists())

    def test_copy_mode_is_recorded(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha", mode="copy")
        mirror = self.home / ".gamma" / "skills" / "alpha"
        self.assertFalse(mirror.is_symlink())
        self.assertEqual(self.state().get("gamma", "alpha").mode, "copy")

    def test_force_replaces_a_foreign_path_after_backing_it_up(self) -> None:
        self.detected("gamma")
        foreign = self.home / ".gamma" / "skills" / "alpha"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("mine\n", encoding="utf-8")

        outcome = self.install("gamma", "alpha", force=True)
        self.assertFalse(outcome.failed)
        self.assertTrue((foreign / "SKILL.md").read_text(encoding="utf-8").startswith("---"))
        backups = list((shared.state_dir(self.scope()) / "backups").rglob("SKILL.md"))
        self.assertEqual(len(backups), 1)
        self.assertIn("mine", backups[0].read_text(encoding="utf-8"))

    def test_second_run_is_unchanged_and_keeps_mtimes(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha")
        mirror = self.home / ".gamma" / "skills" / "alpha"
        before = os.lstat(mirror).st_mtime_ns
        outcome = self.install("gamma", "alpha")
        self.assertEqual(outcome.count("created", "updated"), 0)
        self.assertEqual(os.lstat(mirror).st_mtime_ns, before)


class TestUninstall(Sandbox):
    def test_all_removes_mirrors_hub_and_state(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha")
        state = self.state()
        outcome = orchestrator.uninstall(
            self.scope(),
            registry.resolve_selection(self.targets(), ["all"], self.scope()),
            state,
            all_targets=self.targets(),
            remove_all=True,
        )
        self.assertTrue(outcome.count("removed") >= 2)
        self.assertFalse((self.hub() / "alpha").exists())
        self.assertFalse((self.home / ".gamma" / "skills" / "alpha").exists())
        self.assertFalse(shared.state_dir(self.scope()).exists())

    def test_all_removes_a_hub_only_install(self) -> None:
        outcome = self.install("auto", "alpha")
        self.assertEqual(outcome.reports, [])
        state = self.state()
        orchestrator.uninstall(
            self.scope(), [], state, all_targets=self.targets(), remove_all=True
        )
        self.assertFalse((self.hub() / "alpha").exists())
        self.assertFalse(shared.state_dir(self.scope()).exists())

    def test_tool_removal_keeps_the_hub_and_other_tools(self) -> None:
        self.detected("gamma", "alpha")
        self.install("gamma,alpha", "alpha")
        state = self.state()
        selected = registry.resolve_selection(self.targets(), ["gamma"], self.scope())
        orchestrator.uninstall(
            self.scope(),
            selected,
            state,
            all_targets=self.targets(),
            remove_all=False,
        )
        self.assertFalse((self.home / ".gamma" / "skills" / "alpha").exists())
        self.assertTrue((self.home / ".alpha" / "skills" / "alpha").is_symlink())
        self.assertTrue((self.hub() / "alpha").is_dir())
        reloaded = self.state()
        self.assertFalse(reloaded.configured("gamma"))
        self.assertTrue(reloaded.configured("alpha"))

    def test_shared_mirror_is_released_not_deleted(self) -> None:
        self.install("shared-a,shared-b", "alpha")
        directory = self.home / ".config" / "shared" / "skills" / "alpha"
        state = self.state()
        selected = registry.resolve_selection(self.targets(), ["shared-a"], self.scope())
        orchestrator.uninstall(
            self.scope(), selected, state, all_targets=self.targets(), remove_all=False
        )
        self.assertTrue(directory.exists())
        reloaded = self.state()
        self.assertFalse(reloaded.configured("shared-a"))
        self.assertTrue(reloaded.configured("shared-b"))

    def test_unmanaged_mirror_is_kept_and_still_recorded(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha")
        mirror = self.home / ".gamma" / "skills" / "alpha"
        mirror.unlink()
        mirror.mkdir()
        (mirror / "SKILL.md").write_text("user replaced this\n", encoding="utf-8")

        code, out, _ = self.run_cli("uninstall", "--all", expect=0)
        self.assertTrue(mirror.exists())
        self.assertIn("user replaced this", (mirror / "SKILL.md").read_text(encoding="utf-8"))
        reloaded = self.state()
        self.assertTrue(reloaded.configured("gamma"))
        self.assertFalse((self.hub() / "alpha").exists())

    def test_read_hub_tool_needs_all(self) -> None:
        self.detected("beta")
        self.install("beta", "alpha")
        code, _, err = self.run_cli("uninstall", "--tool", "beta")
        self.assertEqual(code, 1)
        self.assertIn("use --all", err)

    def test_uninstall_cleans_the_path_recorded_before_the_env_changed(self) -> None:
        os.environ["ALPHA_HOME"] = str(self.home / "old")
        self.install("alpha", "alpha")
        old = self.home / "old" / "skills" / "alpha"
        self.assertTrue(old.is_symlink())
        os.environ["ALPHA_HOME"] = str(self.home / "new")

        state = self.state()
        selected = registry.resolve_selection(self.targets(), ["alpha"], self.scope())
        orchestrator.uninstall(
            self.scope(), selected, state, all_targets=self.targets(), remove_all=False
        )
        self.assertFalse(old.exists() or old.is_symlink())
        self.assertFalse(self.state().configured("alpha"))

    def test_dry_run_removes_nothing(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha")
        before = sorted(self.home.rglob("*"))
        self.run_cli("uninstall", "--all", "--dry-run", expect=0)
        self.assertEqual(before, sorted(self.home.rglob("*")))
        self.assertTrue(self.state().configured("gamma"))


class TestCheck(Sandbox):
    def test_healthy_install_and_missing_mirror(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha")
        self.assertEqual(orchestrator.check(self.scope(), self.state(), self.targets()), [])

        (self.home / ".gamma" / "skills" / "alpha").unlink()
        drift = orchestrator.check(self.scope(), self.state(), self.targets())
        self.assertEqual([(item.target, item.skill) for item in drift], [("gamma", "alpha")])

    def test_moved_agent_directory_is_drift(self) -> None:
        self.detected("alpha")
        self.install("alpha", "alpha")
        os.environ["ALPHA_HOME"] = str(self.home / "elsewhere")
        drift = orchestrator.check(self.scope(), self.state(), self.targets())
        self.assertEqual(len(drift), 1)
        self.assertIn("moved", drift[0].reason)

    def test_stale_copy_is_drift(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha", mode="copy")
        with (self.hub() / "alpha" / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nnew upstream text\n")
        drift = orchestrator.check(self.scope(), self.state(), self.targets())
        self.assertIn("stale", drift[0].reason)

    def test_missing_hub_copy_is_drift(self) -> None:
        self.detected("gamma")
        self.install("gamma", "alpha")
        shutil.rmtree(self.hub() / "alpha")
        drift = orchestrator.check(self.scope(), self.state(), self.targets())
        self.assertTrue(any(item.target == HUB_TARGET for item in drift))


class TestState(Sandbox):
    def test_empty_state_removes_file_and_directory(self) -> None:
        state = self.state()
        state.record("gamma", "alpha", "link", "9.9.9", self.home / ".gamma" / "skills" / "alpha")
        state.save()
        self.assertTrue(state.path.is_file())
        state.forget("gamma")
        state.save()
        self.assertFalse(state.path.exists())
        self.assertFalse(state.path.parent.exists())

    def test_legacy_manifest_is_adopted(self) -> None:
        folder = shared.state_dir(self.scope())
        folder.mkdir(parents=True)
        hub = self.hub()
        mirror = self.home / ".gamma" / "skills" / "alpha"
        (folder / "manifest.tsv").write_text(
            "# awesome-skills manifest v1\n"
            f"# hub={hub}\n"
            f"alpha\thub\tcopy\t{hub / 'alpha'}\t0.1.1\n"
            f"alpha\tmirror\tlink\t{mirror}\t0.1.1\n",
            encoding="utf-8",
        )
        state = self.state()
        self.assertTrue(state.adopted_legacy)
        self.assertEqual(state.skills(HUB_TARGET), ["alpha"])
        self.assertEqual(state.skills("gamma"), ["alpha"])
        self.assertEqual(state.get("gamma", "alpha").mode, "link")

    def test_check_refuses_a_corrupt_manifest(self) -> None:
        folder = shared.state_dir(self.scope())
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{ not json", encoding="utf-8")
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("unreadable", err)

    def test_legacy_import_can_be_fully_removed_without_resurrection(self) -> None:
        spec = self.specs("alpha")[0]
        hub = self.hub()
        skills.hub_install(spec, hub, "1.0")
        mirror = self.home / ".gamma" / "skills" / "alpha"
        skills.mirror_install(spec, mirror, hub / "alpha", "link")

        folder = shared.state_dir(self.scope())
        folder.mkdir(parents=True)
        (folder / "manifest.tsv").write_text(
            f"alpha\thub\tcopy\t{hub / 'alpha'}\t1.0\n"
            f"alpha\tmirror\tlink\t{mirror}\t1.0\n",
            encoding="utf-8",
        )
        state = self.state()
        self.assertTrue(state.adopted_legacy)
        targets = self.targets()
        gamma = registry.find(targets, "gamma")
        orchestrator.uninstall(
            self.scope(), [gamma], state, all_targets=targets, remove_all=True
        )
        self.assertFalse((hub / "alpha").exists())
        self.assertFalse(mirror.is_symlink())
        # A tombstone stays behind so manifest.tsv cannot resurrect rows.
        self.assertTrue(state.path.is_file())
        reloaded = self.state()
        self.assertTrue(reloaded.is_empty())
        self.assertFalse(reloaded.adopted_legacy)

    def test_saving_a_corrupt_manifest_preserves_a_copy(self) -> None:
        folder = shared.state_dir(self.scope())
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{ not json", encoding="utf-8")
        state = self.state()
        self.assertTrue(state.corrupt)
        state.save()
        self.assertTrue(state.path.is_file())
        self.assertTrue(state.path.with_name("state.json.corrupt").is_file())
        self.assertFalse(state.corrupt)

    def test_corrupt_state_is_empty_and_never_raises(self) -> None:
        folder = shared.state_dir(self.scope())
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{ not json", encoding="utf-8")
        state = self.state()
        self.assertTrue(state.corrupt)
        self.assertTrue(state.is_empty())


class TestCli(Sandbox):
    def test_install_list_check_uninstall_round_trip(self) -> None:
        self.detected("gamma")
        code, out, _ = self.run_cli("install", "--tool", "gamma", "--skill", "alpha", expect=0)
        self.assertIn("Installed", out)

        code, out, _ = self.run_cli("list", "--installed", expect=0)
        self.assertIn("alpha", out)
        self.assertIn("gamma", out)

        self.run_cli("check", expect=0)
        (self.home / ".gamma" / "skills" / "alpha").unlink()
        self.run_cli("check", expect=1)

        self.run_cli("uninstall", "--tool", "gamma", expect=0)
        self.assertFalse((self.home / ".gamma" / "skills" / "alpha").exists())
        self.assertTrue((self.hub() / "alpha").exists())

    def test_yes_flag_skips_the_guide(self) -> None:
        with mock.patch.object(wizard, "collect", side_effect=AssertionError("prompted")):
            code, _, _ = self.run_cli(
                "install", "--yes", "--tool", "gamma", "--skill", "alpha", expect=0
            )
        self.assertEqual(code, 0)
        self.assertTrue((self.home / ".gamma" / "skills" / "alpha").is_symlink())

    def test_project_scope(self) -> None:
        self.run_cli(
            "install", "--project", str(self.project), "--tool", "gamma", "--skill", "alpha",
            expect=0,
        )
        self.assertTrue((self.project / ".agents" / "skills" / "alpha" / "SKILL.md").is_file())
        mirror = self.project / ".gamma" / "skills" / "alpha"
        self.assertTrue(mirror.is_symlink())
        self.assertFalse((self.home / ".agents" / "skills").exists())
        self.assertEqual(
            State.load(shared.Scope.project(self.project), self.targets()).skills("gamma"),
            ["alpha"],
        )

    def test_unknown_skill_and_bad_flag_combinations(self) -> None:
        code, _, err = self.run_cli("install", "--tool", "gamma", "--skill", "nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown skill", err)

        code, _, err = self.run_cli("uninstall", "--all", "--tool", "gamma")
        self.assertEqual(code, 1)
        self.assertIn("mutually exclusive", err)

        code, _, err = self.run_cli("uninstall")
        self.assertEqual(code, 1)
        self.assertIn("choose what to remove", err)

    def test_tools_and_doctor(self) -> None:
        self.detected("gamma")
        code, out, _ = self.run_cli("tools", expect=0)
        self.assertIn("shared-a", out)
        self.assertIn("gamma", out)

        code, out, _ = self.run_cli("doctor", expect=0)
        self.assertIn("repository", out)
        self.assertIn("agent table", out)


if __name__ == "__main__":
    unittest.main()
