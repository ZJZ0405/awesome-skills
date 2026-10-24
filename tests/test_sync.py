#!/usr/bin/env python3
"""Tests for scripts/sync_upstreams.py.

Every upstream is a local bare repository served over file://, so the suite is
offline and does not need a GitHub token.

Run: python3 tests/test_sync.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "sync_upstreams.py"

SKILL_TEMPLATE = """---
name: {name}
description: Test skill {name} used by the sync test suite.
{extra}---

# {name}

Body for {name}.
"""

LICENSE_MIT = """MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""


def skill_md(name, extra=""):
    return SKILL_TEMPLATE.format(name=name, extra=extra)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awesome-sync-test-"))
        self.remotes = self.tmp / "remotes"
        self.remotes.mkdir()
        self.root = self.tmp / "collection"
        (self.root / "skills").mkdir(parents=True)
        self.write_json(
            self.root / "sources.json",
            {"schemaVersion": 1, "skills": {}},
        )
        self.set_config([])
        self.bare = {}

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def set_config(self, upstreams):
        self.write_json(
            self.root / "upstreams.json",
            {"schemaVersion": 1, "upstreams": upstreams},
        )

    def git(self, cwd, *args):
        proc = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode:
            self.fail(
                "git %s failed: %s" % (" ".join(args), proc.stderr.strip())
            )
        return proc.stdout

    def write_files(self, base, files):
        for rel, content in files.items():
            path = Path(base) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def make_repo(self, repo, files):
        work = self.tmp / "work" / repo.replace("/", "__")
        work.mkdir(parents=True)
        self.git(work, "init", "-q")
        self.git(work, "config", "user.email", "test@example.com")
        self.git(work, "config", "user.name", "Sync Test")
        self.write_files(work, files)
        self.git(work, "add", "-A")
        self.git(work, "commit", "-qm", "initial")
        self.git(work, "branch", "-M", "main")
        bare = self.remotes / (repo + ".git")
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.tmp, "clone", "-q", "--bare", str(work), str(bare))
        self.bare[repo] = bare
        return work

    def update_repo(self, repo, work, files, remove=()):
        for name in remove:
            path = Path(work) / "skills" / name
            if path.exists():
                shutil.rmtree(str(path))
        self.write_files(work, files)
        self.git(work, "add", "-A")
        self.git(work, "commit", "-qm", "update")
        self.git(work, "push", "-q", str(self.bare[repo]), "HEAD:main")

    def base_upstream(self, include, **overrides):
        data = {
            "id": "acme",
            "repo": "acme/skills",
            "track": "branch:main",
            "license": "MIT",
            "licenseFile": "LICENSE",
            "paths": ["skills"],
            "include": include,
        }
        data.update(overrides)
        return data

    def sync(self, *args):
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--repo-base",
                self.remotes.as_uri(),
            ]
            + list(args),
            cwd=str(REPO),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.last = proc
        return proc

    def assert_ok(self, proc):
        self.assertEqual(
            proc.returncode,
            0,
            "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )

    def assert_failed(self, proc):
        self.assertEqual(
            proc.returncode,
            1,
            "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )

    def skill_dir(self, name):
        return self.root / "skills" / name

    def lock(self):
        return self.read_json(self.root / "upstreams.lock.json")

    def sources(self):
        return self.read_json(self.root / "sources.json")

    # -- tests -----------------------------------------------------------

    def test_add_writes_skill_lock_and_sources(self):
        work = self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "NOTICE": "Upstream notice.\n",
                "skills/alpha/SKILL.md": skill_md("alpha"),
                "skills/alpha/references/notes.md": "Notes\n",
                "skills/beta/SKILL.md": skill_md("beta"),
            },
        )
        self.set_config([self.base_upstream(["alpha"])])
        proc = self.sync()
        self.assert_ok(proc)

        self.assertTrue((self.skill_dir("alpha") / "SKILL.md").is_file())
        self.assertFalse(self.skill_dir("beta").exists())
        text = (self.skill_dir("alpha") / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("license: MIT", text)
        self.assertTrue((self.skill_dir("alpha") / "LICENSE").is_file())
        self.assertTrue((self.skill_dir("alpha") / "NOTICE").is_file())
        self.assertTrue(
            (self.skill_dir("alpha") / "references" / "notes.md").is_file()
        )

        commit = self.git(work, "rev-parse", "HEAD").strip()
        lock = self.lock()
        self.assertEqual(lock["upstreams"]["acme"]["commit"], commit)
        files = lock["upstreams"]["acme"]["skills"]["alpha"]["files"]
        self.assertIn("SKILL.md", files)
        self.assertIn("LICENSE", files)
        self.assertIn("NOTICE", files)
        self.assertEqual(len(files["SKILL.md"]), 64)

        source = self.sources()["skills"]["alpha"]
        self.assertEqual(source["license"], "MIT")
        self.assertEqual(source["commit"], commit)
        self.assertEqual(
            source["source"],
            "https://github.com/acme/skills/tree/%s/skills/alpha" % commit,
        )

    def test_exclude_paths_are_not_vendored(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/demo/SKILL.md": skill_md("demo"),
                "skills/demo/examples/keep.json": "{}\n",
                "skills/demo/examples/demo-rendered.html": "<html></html>\n",
                "skills/demo/examples/nested/drop.html": "<html></html>\n",
            },
        )
        self.set_config(
            [self.base_upstream(["demo"], excludePaths=["examples/*.html"])]
        )
        proc = self.sync()
        self.assert_ok(proc)

        demo = self.skill_dir("demo")
        self.assertTrue((demo / "examples" / "keep.json").is_file())
        self.assertFalse((demo / "examples" / "demo-rendered.html").exists())
        self.assertFalse((demo / "examples" / "nested" / "drop.html").exists())
        files = self.lock()["upstreams"]["acme"]["skills"]["demo"]["files"]
        self.assertIn("examples/keep.json", files)
        self.assertNotIn("examples/demo-rendered.html", files)

        again = self.sync()
        self.assert_ok(again)
        self.assertIn("0 added, 0 updated, 1 unchanged", again.stdout)

    def test_exclude_paths_reject_traversal(self):
        self.set_config(
            [self.base_upstream(["alpha"], excludePaths=["../secret/*"])]
        )
        proc = self.sync()
        self.assert_failed(proc)
        self.assertIn("must not contain '..'", proc.stderr)

    def test_text_files_are_normalized_to_lf(self):
        work = self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/demo/SKILL.md": skill_md("demo"),
            },
        )
        self.git(work, "config", "core.autocrlf", "false")
        self.update_repo(
            "acme/skills",
            work,
            {
                "skills/demo/scripts/check.py": "#!/usr/bin/env python3\r\nprint('ok')\r\n",
                "skills/demo/data.json": "{\r\n  \"a\": 1\r\n}\r\n",
                "skills/demo/blob.bin": "\x00\r\n",
            },
        )
        self.set_config([self.base_upstream(["demo"])])
        self.assert_ok(self.sync())

        demo = self.skill_dir("demo")
        self.assertNotIn(b"\r", (demo / "scripts" / "check.py").read_bytes())
        self.assertNotIn(b"\r", (demo / "data.json").read_bytes())
        self.assertIn(b"\r", (demo / "blob.bin").read_bytes())
        files = self.lock()["upstreams"]["acme"]["skills"]["demo"]["files"]
        self.assertIn("scripts/check.py", files)

    def test_code_span_links_are_not_portability_errors(self):
        md = skill_md("demo").replace(
            "Body for demo.",
            "Example: `Created [timing](/abs/path/timing.html).`",
        )
        self.make_repo(
            "acme/skills",
            {"LICENSE": LICENSE_MIT, "skills/demo/SKILL.md": md},
        )
        self.set_config([self.base_upstream(["demo"])])
        proc = self.sync()
        self.assert_ok(proc)
        self.assertTrue((self.skill_dir("demo") / "SKILL.md").is_file())

    def test_broken_reference_links_still_skip(self):
        md = skill_md("demo").replace(
            "Body for demo.",
            "See [notes](references/missing.md).",
        )
        self.make_repo(
            "acme/skills",
            {"LICENSE": LICENSE_MIT, "skills/demo/SKILL.md": md},
        )
        self.set_config([self.base_upstream(["demo"])])
        proc = self.sync()
        self.assert_ok(proc)
        self.assertIn("1 skipped", proc.stdout)
        self.assertIn("broken internal link", proc.stdout)
        self.assertFalse(self.skill_dir("demo").exists())

    def test_second_run_is_unchanged_and_check_detects_moves(self):
        work = self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.set_config([self.base_upstream(["alpha"])])
        self.assert_ok(self.sync())
        before = (self.root / "upstreams.lock.json").read_bytes()

        proc = self.sync()
        self.assert_ok(proc)
        self.assertIn("0 added, 0 updated, 1 unchanged", proc.stdout)
        self.assertEqual(before, (self.root / "upstreams.lock.json").read_bytes())

        self.assert_ok(self.sync("--check"))
        self.update_repo(
            "acme/skills",
            work,
            {"skills/alpha/SKILL.md": skill_md("alpha") + "\nUpstream change.\n"},
        )
        check = self.sync("--check")
        self.assert_failed(check)
        self.assertIn("stale", check.stderr)

        proc = self.sync()
        self.assert_ok(proc)
        self.assertIn("1 updated", proc.stdout)
        self.assertEqual(
            self.lock()["upstreams"]["acme"]["commit"],
            self.git(work, "rev-parse", "HEAD").strip(),
        )

    def test_tag_and_commit_tracks(self):
        work = self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.git(work, "tag", "v1.0.0")
        self.git(work, "push", "-q", str(self.bare["acme/skills"]), "v1.0.0")
        first = self.git(work, "rev-parse", "HEAD").strip()

        self.set_config([self.base_upstream(["alpha"], track="tag:v1.0.0")])
        self.assert_ok(self.sync())
        self.assertEqual(
            self.lock()["upstreams"]["acme"]["commit"], first
        )

        self.update_repo(
            "acme/skills",
            work,
            {"skills/alpha/SKILL.md": skill_md("alpha") + "\nTag change.\n"},
        )
        second = self.git(work, "rev-parse", "HEAD").strip()
        self.git(work, "tag", "-f", "v1.0.0")
        self.git(
            work,
            "push",
            "-q",
            "--force",
            str(self.bare["acme/skills"]),
            "refs/tags/v1.0.0",
        )
        self.assert_failed(self.sync("--check"))
        self.assert_ok(self.sync())
        self.assertEqual(
            self.lock()["upstreams"]["acme"]["commit"], second
        )

        # A pinned SHA must fetch that exact commit, even though the branch and
        # the tag have moved on.
        self.set_config(
            [self.base_upstream(["alpha"], track=first)]
        )
        self.assert_ok(self.sync())
        self.assertEqual(
            self.lock()["upstreams"]["acme"]["commit"], first
        )
        self.assertNotIn(
            "Tag change",
            (self.skill_dir("alpha") / "SKILL.md").read_text(encoding="utf-8"),
        )

    def test_local_drift_requires_force(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.set_config([self.base_upstream(["alpha"])])
        self.assert_ok(self.sync())

        local = self.skill_dir("alpha") / "SKILL.md"
        local.write_text(
            local.read_text(encoding="utf-8") + "\nlocal edit\n",
            encoding="utf-8",
        )
        proc = self.sync()
        self.assert_failed(proc)
        self.assertIn("local edit", local.read_text(encoding="utf-8"))

        proc = self.sync("--force")
        self.assert_ok(proc)
        self.assertNotIn("local edit", local.read_text(encoding="utf-8"))
        self.assertIn("1 updated", proc.stdout)

    def test_license_and_portability_gates_skip_but_continue(self):
        self.make_repo(
            "acme/skills",
            {
                "skills/good/SKILL.md": skill_md("good"),
                "skills/good/LICENSE.txt": LICENSE_MIT,
                "skills/bad/SKILL.md": skill_md("bad", "context: fork\n"),
                "skills/bad/LICENSE.txt": LICENSE_MIT,
                "skills/missing-license/SKILL.md": skill_md("missing-license"),
            },
        )
        self.set_config(
            [
                {
                    "id": "acme",
                    "repo": "acme/skills",
                    "track": "branch:main",
                    "license": "per-skill",
                    "licenses": {
                        "good": "MIT",
                        "bad": "MIT",
                        "missing-license": "MIT",
                    },
                    "licenseFile": "LICENSE.txt",
                    "paths": ["skills"],
                    "include": ["good", "bad", "missing-license"],
                }
            ]
        )
        proc = self.sync()
        self.assert_ok(proc)
        self.assertTrue((self.skill_dir("good") / "SKILL.md").is_file())
        self.assertFalse(self.skill_dir("bad").exists())
        self.assertFalse(self.skill_dir("missing-license").exists())
        self.assertIn("skipped", proc.stdout)
        self.assertIn("context", proc.stdout)
        self.assertIn("license file is missing", proc.stdout)
        self.assertEqual(
            set(self.lock()["upstreams"]["acme"]["skills"]), {"good"}
        )

    def test_duplicate_skill_names_abort_without_writing(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.make_repo(
            "acme/other",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.set_config(
            [
                self.base_upstream(["alpha"]),
                {
                    "id": "other",
                    "repo": "acme/other",
                    "track": "branch:main",
                    "license": "MIT",
                    "licenseFile": "LICENSE",
                    "paths": ["skills"],
                    "include": ["alpha"],
                },
            ]
        )
        proc = self.sync()
        self.assert_failed(proc)
        self.assertIn("included by both", proc.stderr)
        self.assertFalse(self.skill_dir("alpha").exists())
        self.assertFalse((self.root / "upstreams.lock.json").exists())

    def test_removed_skills_are_kept_then_pruned(self):
        work = self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
                "skills/beta/SKILL.md": skill_md("beta"),
                "skills/other/SKILL.md": skill_md("other"),
            },
        )
        self.set_config([self.base_upstream(["alpha", "beta"])])
        self.assert_ok(self.sync())

        # Keep the skills/ directory populated, remove the two imported skills.
        self.update_repo(
            "acme/skills",
            work,
            {"skills/other/notes.md": "still here\n"},
            remove=("alpha", "beta"),
        )
        local_beta = self.skill_dir("beta") / "SKILL.md"
        local_beta.write_text(
            local_beta.read_text(encoding="utf-8") + "\nlocal beta\n",
            encoding="utf-8",
        )

        proc = self.sync()
        self.assert_ok(proc)
        self.assertTrue(self.skill_dir("alpha").exists())
        self.assertTrue(self.skill_dir("beta").exists())
        self.assertIn("orphaned", proc.stdout)

        proc = self.sync("--prune")
        self.assert_ok(proc)
        self.assertFalse(self.skill_dir("alpha").exists())
        self.assertTrue(self.skill_dir("beta").exists())
        self.assertIn("not pruned", proc.stdout)
        self.assertEqual(
            set(self.lock()["upstreams"]["acme"]["skills"]), {"beta"}
        )
        self.assertNotIn("alpha", self.sources()["skills"])

        # --force must not weaken the prune safety rule.
        proc = self.sync("--prune", "--force")
        self.assert_ok(proc)
        self.assertTrue(self.skill_dir("beta").exists())

    def test_disabled_upstream_is_skipped_then_prunable(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        config = self.base_upstream(["alpha"])
        self.set_config([config])
        self.assert_ok(self.sync())
        self.assertTrue(self.skill_dir("alpha").exists())

        config["enabled"] = False
        self.set_config([config])
        proc = self.sync()
        self.assert_ok(proc)
        self.assertIn("disabled", proc.stdout)
        self.assertTrue(self.skill_dir("alpha").exists())

        proc = self.sync("--prune")
        self.assert_ok(proc)
        self.assertFalse(self.skill_dir("alpha").exists())
        self.assertNotIn("acme", self.lock()["upstreams"])

    def test_removed_config_entry_can_still_be_pruned(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.set_config([self.base_upstream(["alpha"])])
        self.assert_ok(self.sync())
        self.set_config([])

        proc = self.sync()
        self.assert_ok(proc)
        self.assertIn("orphaned", proc.stdout)
        self.assertTrue(self.skill_dir("alpha").exists())

        proc = self.sync("--prune")
        self.assert_ok(proc)
        self.assertFalse(self.skill_dir("alpha").exists())
        self.assertEqual(self.lock()["upstreams"], {})
        self.assertNotIn("alpha", self.sources()["skills"])

    def test_unmanaged_path_is_never_overwritten(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.set_config([self.base_upstream(["alpha"])])
        local = self.skill_dir("alpha") / "SKILL.md"
        local.parent.mkdir(parents=True)
        local.write_text("user content\n", encoding="utf-8")

        for extra in ((), ("--force",)):
            proc = self.sync(*extra)
            self.assert_failed(proc)
            self.assertIn("not managed", proc.stderr)
            self.assertEqual(local.read_text(encoding="utf-8"), "user content\n")
        self.assertFalse((self.root / "upstreams.lock.json").exists())

    def test_dry_run_and_summary_write_nothing(self):
        self.make_repo(
            "acme/skills",
            {
                "LICENSE": LICENSE_MIT,
                "skills/alpha/SKILL.md": skill_md("alpha"),
            },
        )
        self.set_config([self.base_upstream(["alpha"])])
        shutil.rmtree(str(self.root / "skills"))
        summary = self.tmp / "summary.md"
        proc = self.sync("--dry-run", "--summary", str(summary))
        self.assert_ok(proc)
        self.assertFalse((self.root / "skills").exists())
        self.assertFalse((self.root / "upstreams.lock.json").exists())
        self.assertIn("Dry run", summary.read_text(encoding="utf-8"))
        self.assertIn("## Added", summary.read_text(encoding="utf-8"))
        self.assertIn("alpha", summary.read_text(encoding="utf-8"))

    def test_invalid_config_fails_closed(self):
        self.make_repo(
            "acme/skills",
            {"LICENSE": LICENSE_MIT, "skills/alpha/SKILL.md": skill_md("alpha")},
        )
        self.set_config(
            [
                self.base_upstream(["alpha"], license="GPL-3.0"),
            ]
        )
        proc = self.sync()
        self.assert_failed(proc)
        self.assertIn("not accepted", proc.stderr)
        self.assertFalse((self.root / "upstreams.lock.json").exists())

        self.set_config([self.base_upstream(["alpha"], unknown=True)])
        proc = self.sync()
        self.assert_failed(proc)
        self.assertIn("unknown field", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
