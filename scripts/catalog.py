#!/usr/bin/env python3
"""Regenerate the README skill table and catalog.json.

Usage:
    python3 scripts/catalog.py          # write files
    python3 scripts/catalog.py --check  # fail if they are stale

The README section between <!-- catalog:start --> and <!-- catalog:end -->
is generated. Everything else in the README is hand-written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skilllib import (
    collect_skills,
    markdown_escape,
    normalize_ws,
    repo_root,
    truncate,
    write_json,
)

START = "<!-- catalog:start -->"
END = "<!-- catalog:end -->"
TOOLS_START = "<!-- tools:start -->"
TOOLS_END = "<!-- tools:end -->"


def render_table(skills):
    lines = [
        START,
        "| 技能 | 用途 | 许可证 |",
        "| :--- | :--- | :--- |",
    ]
    for skill in skills:
        name = skill.name or skill.dir_name
        description = markdown_escape(truncate(skill.description, 160)) or "—"
        license_name = markdown_escape(skill.license) or "未标注"
        lines.append(
            "| [`%s`](%s/) | %s | %s |"
            % (name, skill.rel_path.replace("\\", "/"), description, license_name)
        )
    lines.append(END)
    return "\n".join(lines)


def replace_section(text, section, start_marker=START, end_marker=END):
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(
            "missing %s / %s markers" % (start_marker, end_marker)
        )
    return text[:start] + section + text[end + len(end_marker):]


def render_tools_table(root):
    table = root / "lib" / "agents.tsv"
    lines = [
        TOOLS_START,
        "| 别名 | 项目目录 | 全局目录 |",
        "| --- | --- | --- |",
    ]
    for raw in table.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 4:
            continue
        alias, project, global_dir, _detect = parts
        project_cell = "—" if project == "N/A" else "`%s`" % project
        global_cell = "—" if global_dir == "N/A" else "`%s`" % global_dir
        lines.append("| `%s` | %s | %s |" % (alias, project_cell, global_cell))
    lines.append(TOOLS_END)
    return "\n".join(lines)


def build_catalog(skills):
    entries = []
    for skill in skills:
        entries.append(
            {
                "name": skill.name or skill.dir_name,
                "description": normalize_ws(skill.description),
                "path": skill.rel_path.replace("\\", "/"),
                "version": skill.version,
                "license": skill.license,
            }
        )
    return {"schemaVersion": 1, "skills": entries}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="catalog.py")
    parser.add_argument("--check", action="store_true", help="do not write; fail if stale")
    args = parser.parse_args(argv)

    root = repo_root()
    readme_path = root / "README.md"
    catalog_path = root / "catalog.json"
    tools_path = root / "docs" / "tools.md"

    skills = [skill for skill in collect_skills(root) if not skill.internal]
    section = render_table(skills)
    catalog = build_catalog(skills)
    tools_section = render_tools_table(root)

    for required in (readme_path, tools_path):
        if not required.is_file():
            print("error: %s not found" % required.relative_to(root), file=sys.stderr)
            return 1

    old_readme = readme_path.read_text(encoding="utf-8")
    new_readme = replace_section(old_readme, section)
    old_tools = tools_path.read_text(encoding="utf-8")
    new_tools = replace_section(old_tools, tools_section, TOOLS_START, TOOLS_END)
    new_catalog_text = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        stale = []
        if old_readme != new_readme:
            stale.append("README.md")
        if old_tools != new_tools:
            stale.append("docs/tools.md")
        try:
            old_catalog = catalog_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            old_catalog = ""
        if old_catalog != new_catalog_text:
            stale.append("catalog.json")
        if stale:
            print(
                "stale generated files: %s (run: python3 scripts/catalog.py)"
                % ", ".join(stale),
                file=sys.stderr,
            )
            return 1
        print("catalog is up to date (%d skill(s))" % len(skills))
        return 0

    readme_path.write_text(new_readme, encoding="utf-8")
    tools_path.write_text(new_tools, encoding="utf-8")
    catalog_path.write_text(new_catalog_text, encoding="utf-8")
    print("updated README.md, docs/tools.md, catalog.json (%d skill(s))" % len(skills))
    return 0


if __name__ == "__main__":
    sys.exit(main())
