#!/usr/bin/env python3
"""Import a skill directory into this collection.

Usage:
    python3 scripts/import.py ~/Downloads/some-skill
    python3 scripts/import.py ./vendor/foo --name foo
    python3 scripts/import.py ./vendor/foo --force

The source must contain a SKILL.md with valid frontmatter. The skill keeps its
own license; provenance is recorded in sources.json.
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sys
from pathlib import Path

from skilllib import NAME_RE, load_skill, read_json, repo_root, write_json


def rewrite_frontmatter_name(text, new_name):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md has no frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            break
        if lines[index].startswith("name:"):
            lines[index] = "name: %s" % new_name
            return "\n".join(lines) + "\n"
    lines.insert(1, "name: %s" % new_name)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="import.py", description="import a skill")
    parser.add_argument("source", help="directory containing SKILL.md")
    parser.add_argument("--name", help="override the skill name (renames the folder)")
    parser.add_argument("--force", action="store_true", help="replace an existing skill")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        print("error: not a directory: %s" % source, file=sys.stderr)
        return 1
    if not (source / "SKILL.md").is_file():
        print("error: no SKILL.md in %s" % source, file=sys.stderr)
        return 1

    skill = load_skill(source)
    if skill.error:
        print("error: %s" % skill.error, file=sys.stderr)
        return 1

    name = (args.name or skill.name or source.name).strip()
    if not NAME_RE.match(name):
        print(
            "error: invalid skill name %r (lowercase letters, digits, single hyphens)"
            % name,
            file=sys.stderr,
        )
        return 1

    root = repo_root()
    dest = root / "skills" / name
    if dest.exists():
        if not args.force:
            print(
                "error: skills/%s already exists (use --force to replace it)" % name,
                file=sys.stderr,
            )
            return 1
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            ".DS_Store",
            "node_modules",
            ".awesome-skills-skill",
        ),
    )

    renamed = name != (skill.name or source.name)
    if renamed:
        md = dest / "SKILL.md"
        md.write_text(
            rewrite_frontmatter_name(md.read_text(encoding="utf-8"), name),
            encoding="utf-8",
        )

    sources_path = root / "sources.json"
    sources = read_json(sources_path, {"schemaVersion": 1, "skills": {}})
    if not isinstance(sources.get("skills"), dict):
        sources["skills"] = {}
    sources["skills"][name] = {
        "source": str(source),
        "license": skill.license,
        "importedAt": datetime.date.today().isoformat(),
    }
    write_json(sources_path, sources)

    print("imported skills/%s from %s" % (name, source))
    if renamed:
        print("  renamed frontmatter name -> %s" % name)
    print("")
    print("Next:")
    print("  python3 scripts/validate.py")
    print("  python3 scripts/catalog.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
