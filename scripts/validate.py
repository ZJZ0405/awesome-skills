#!/usr/bin/env python3
"""Validate the skill collection.

Checks the Agent Skills frontmatter contract, folder/name agreement, broken
relative links, and a few hygiene rules. No third-party dependencies.

Usage:
    python3 scripts/validate.py [--strict] [--quiet] [paths...]

Exit codes: 0 valid, 1 invalid.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from skilllib import (
    MAX_DESCRIPTION_LEN,
    MAX_NAME_LEN,
    NAME_RE,
    collect_skills,
    load_skill,
    normalize_ws,
    repo_root,
)

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
CODE_SPAN_RE = re.compile(r"`+[^`]*`+")
INTERNAL_PREFIXES = ("references/", "scripts/", "assets/", "./references/", "./scripts/", "./assets/")


def strip_code(text):
    """Drop fenced blocks and inline code so examples are not checked as links."""
    lines = []
    fence = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            lines.append("")
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            lines.append("")
        else:
            lines.append(line)
    return CODE_SPAN_RE.sub("", "\n".join(lines))


def check_links(skill):
    problems = []
    body = strip_code(skill.body or "")
    for match in LINK_RE.finditer(body):
        target = match.group(1).strip()
        if not target or target.startswith(("#", "<", "mailto:")):
            continue
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        target = unquote(target.split("#", 1)[0].strip())
        if not target or "{{" in target or target.startswith("$"):
            continue
        candidate = (skill.path / target).resolve()
        if candidate.exists():
            continue
        if target.startswith(INTERNAL_PREFIXES):
            problems.append(("error", "broken internal link: %s" % target))
        else:
            problems.append(("warning", "relative link does not resolve: %s" % target))
    return problems


def needs_exec_bit(path):
    """Only files meant to be executed directly need the executable bit.

    A Python module without a shebang is imported, not run; warning about it
    would reject perfectly portable upstream skills.
    """
    if path.suffix in (".sh", ".bash"):
        return True
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def check_scripts(skill):
    warnings = []
    scripts = skill.path / "scripts"
    if not scripts.is_dir():
        return warnings
    for script in sorted(scripts.rglob("*")):
        if not script.is_file():
            continue
        if (
            script.suffix in (".sh", ".py", ".bash")
            and needs_exec_bit(script)
            and not os.access(str(script), os.X_OK)
        ):
            warnings.append(
                "not executable: %s (chmod +x; git tracks the bit)"
                % script.relative_to(skill.path)
            )
    return warnings


def check_hygiene(skill):
    errors = []
    warnings = []

    if "\r\n" in skill.text:
        warnings.append("SKILL.md uses CRLF line endings; use LF")

    line_count = skill.text.count("\n") + 1
    if line_count > 500:
        warnings.append("SKILL.md is %d lines; keep the main file under ~500" % line_count)

    size = skill.skill_md.stat().st_size
    if size > 5 * 1024 * 1024:
        warnings.append("SKILL.md is larger than 5 MB")

    total = 0
    for item in skill.path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    if total > 10 * 1024 * 1024:
        warnings.append("skill folder is %.1f MB; move heavy assets elsewhere" % (total / 1048576.0))

    for name, kind in ((".git", "error"), ("node_modules", "error"), (".DS_Store", "warning"), ("__pycache__", "warning")):
        if (skill.path / name).exists():
            message = "unexpected %s in skill folder" % name
            if kind == "error":
                errors.append(message)
            else:
                warnings.append(message)

    return errors, warnings


def validate_one(skill, seen):
    errors = []
    warnings = []

    if skill.error:
        return [skill.error], [], []

    fm = skill.frontmatter
    meta = fm.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        errors.append("metadata must be a mapping")

    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("missing required field: name")
    else:
        if len(name) > MAX_NAME_LEN:
            errors.append("name is longer than %d characters" % MAX_NAME_LEN)
        if not NAME_RE.match(name):
            errors.append("name must be lowercase letters, digits and single hyphens: %r" % name)
        if name != skill.dir_name:
            errors.append("name %r does not match folder %r" % (name, skill.dir_name))
        if name in seen:
            errors.append("duplicate skill name %r (also in %s)" % (name, seen[name]))
        else:
            seen[name] = str(skill.path)

    description = fm.get("description")
    if not isinstance(description, str) or not normalize_ws(description):
        errors.append("missing required field: description")
    elif len(normalize_ws(description)) > MAX_DESCRIPTION_LEN:
        errors.append(
            "description is %d characters (max %d)"
            % (len(normalize_ws(description)), MAX_DESCRIPTION_LEN)
        )

    if not skill.license:
        warnings.append("no license field; add license: MIT (or the real license)")

    link_problems = check_links(skill)
    for level, message in link_problems:
        if level == "error":
            errors.append(message)
        else:
            warnings.append(message)

    warnings.extend(check_scripts(skill))
    hygiene_errors, hygiene_warnings = check_hygiene(skill)
    errors.extend(hygiene_errors)
    warnings.extend(hygiene_warnings)

    return errors, warnings, []


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="Validate awesome-skills SKILL.md files",
    )
    parser.add_argument("paths", nargs="*", help="specific skill directories (default: all)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--quiet", action="store_true", help="only print problems")
    args = parser.parse_args(argv)

    root = repo_root()
    if args.paths:
        skills = [load_skill(Path(p)) for p in args.paths]
    else:
        skills = collect_skills(root)

    if not skills:
        print("error: no skills found under %s" % (root / "skills"), file=sys.stderr)
        return 1

    github = bool(os.environ.get("GITHUB_ACTIONS"))
    seen = {}
    total_errors = 0
    total_warnings = 0

    for skill in skills:
        rel = skill.rel_path
        errors, warnings, _ = validate_one(skill, seen)
        total_errors += len(errors)
        total_warnings += len(warnings)

        if github:
            for message in errors:
                print("::error file=%s/SKILL.md::%s" % (rel, message))
            for message in warnings:
                print("::warning file=%s/SKILL.md::%s" % (rel, message))

        if errors or warnings:
            print("%s/SKILL.md" % rel)
            for message in errors:
                print("  error: %s" % message)
            for message in warnings:
                print("  warning: %s" % message)
        elif not args.quiet:
            print("%s  ok" % rel)

    if not args.quiet:
        print("")
        if total_errors:
            print("%d error(s), %d warning(s)" % (total_errors, total_warnings))
        elif total_warnings:
            print("ok (%d warning(s))" % total_warnings)
        else:
            print("ok: %d skill(s)" % len(skills))

    if total_errors:
        return 1
    if args.strict and total_warnings:
        print("strict mode: warnings are errors", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
