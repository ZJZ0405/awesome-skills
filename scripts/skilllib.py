"""Shared helpers for the awesome-skills maintainer scripts.

Standard library only, so the scripts also work on an air-gapped machine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024



class Skill:
    """One skill directory, loaded from its SKILL.md."""

    def __init__(self, path, frontmatter, body, text, error=None):
        self.path = path
        self.dir_name = path.name
        self.frontmatter = frontmatter or {}
        self.body = body or ""
        self.text = text or ""
        self.error = error
        self.name = str(self.frontmatter.get("name", "") or "")
        self.description = str(self.frontmatter.get("description", "") or "")
        meta = self.frontmatter.get("metadata")
        self.metadata = meta if isinstance(meta, dict) else {}
        self.version = str(
            self.frontmatter.get("version", "")
            or self.metadata.get("version", "")
            or ""
        )
        self.license = str(
            self.frontmatter.get("license", "")
            or self.metadata.get("license", "")
            or ""
        )
        self.internal = str(self.metadata.get("internal", "")).lower() == "true"

    @property
    def rel_path(self):
        try:
            return str(self.path.relative_to(repo_root()))
        except ValueError:
            return str(self.path)

    @property
    def skill_md(self):
        return self.path / "SKILL.md"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def skills_root(root=None) -> Path:
    return (Path(root) if root else repo_root()) / "skills"


def iter_skill_dirs(root=None):
    """Yield skill directories, matching the Agent Skills discovery rules.

    Supports flat (skills/<name>) and catalog (skills/<cat>/<name>,
    skills/<cat>/<cat>/<name>) layouts. A SKILL.md nested inside another skill
    folder is ignored, mirroring the "shallower skill shadows deeper" rule.
    """
    base = skills_root(root)
    if not base.is_dir():
        return
    candidates = []
    for path in base.rglob("SKILL.md"):
        if not path.is_file() or path.parent == base:
            continue
        parts = path.parent.relative_to(base).parts
        if len(parts) > 3:
            continue
        candidates.append(path.parent)

    chosen = []
    for candidate in sorted(candidates, key=lambda p: (len(p.parts), str(p))):
        if any(parent in chosen for parent in candidate.parents):
            continue
        chosen.append(candidate)
        yield candidate


def load_skill(path):
    path = Path(path)
    md = path / "SKILL.md"
    try:
        text = md.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Skill(path, {}, "", "", error="SKILL.md is missing")
    except UnicodeDecodeError as exc:
        return Skill(path, {}, "", "", error="SKILL.md is not valid UTF-8: %s" % exc)

    block, body = split_frontmatter(text)
    if block is None:
        return Skill(path, {}, body, text, error="missing YAML frontmatter")
    if "\r\n" in text:
        # Kept as a warning by the validator; still parse fine.
        pass
    try:
        frontmatter = parse_frontmatter(block)
    except ValueError as exc:
        return Skill(path, {}, body, text, error="invalid frontmatter: %s" % exc)
    if not frontmatter:
        return Skill(path, {}, body, text, error="frontmatter is empty")
    return Skill(path, frontmatter, body, text)


def split_frontmatter(text):
    """Return (frontmatter block, body) or (None, text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1:])
    return None, text


def parse_scalar(raw):
    value = raw.strip()
    if not value:
        return ""
    if value[0] == '"' and value[-1:] == '"' and len(value) >= 2:
        try:
            return json.loads(value)
        except ValueError:
            return value[1:-1].replace('\\"', '"')
    if value[0] == "'" and value[-1:] == "'" and len(value) >= 2:
        return value[1:-1].replace("''", "'")
    if value in ("null", "~"):
        return ""
    return value


def parse_frontmatter(block):
    """Parse the small YAML subset used by SKILL.md frontmatter.

    Supports top-level scalars, quoted strings, block scalars (| and >) and one
    level of nested mapping (used for metadata). Anything more exotic is
    ignored rather than guessed at.
    """
    data = {}
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1] in (" ", "\t"):
            index += 1
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+):(.*)$", line)
        if not match:
            index += 1
            continue

        key, rest = match.group(1), match.group(2).strip()
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            index += 1
            collected = []
            while index < len(lines):
                nxt = lines[index]
                if nxt.strip() and nxt[:1] not in (" ", "\t"):
                    break
                if nxt.strip():
                    collected.append(nxt.strip())
                index += 1
            joiner = "\n" if rest.startswith("|") else " "
            data[key] = joiner.join(collected)
            continue

        if rest == "":
            nested = {}
            look = index + 1
            while look < len(lines):
                nxt = lines[look]
                if nxt.strip() and nxt[:1] not in (" ", "\t"):
                    break
                nested_match = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", nxt)
                if nested_match:
                    nested[nested_match.group(1)] = parse_scalar(nested_match.group(2))
                look += 1
            if nested:
                data[key] = nested
                index = look
                continue
            data[key] = ""
            index += 1
            continue

        data[key] = parse_scalar(rest)
        index += 1

    return data


def normalize_ws(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate(value, limit):
    value = normalize_ws(value)
    if len(value) <= limit:
        return value
    cut = value[: limit - 1].rstrip()
    if " " in cut[limit // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def markdown_escape(value):
    return value.replace("|", "\\|")


def collect_skills(root=None, include_internal=True):
    skills = []
    for path in iter_skill_dirs(root):
        skill = load_skill(path)
        if not include_internal and skill.internal:
            continue
        skills.append(skill)
    skills.sort(key=lambda skill: (skill.name or skill.dir_name, str(skill.path)))
    return skills


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
