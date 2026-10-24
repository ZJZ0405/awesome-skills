# Repository guide (for agents and humans)

This repository is a **portable collection of Agent Skills** plus a POSIX
installer that copies them into the skill directories of ~80 coding agents.

## Layout

- `skills/<name>/SKILL.md` — one skill per folder. This is the only content that
  gets installed.
- `upstreams.json` — reviewed upstream whitelist; `upstreams.lock.json` — resolved
  commits and per-file SHA-256. Vendored skills are read-only; edit the config and
  re-run the sync, not the copy under `skills/`.
- `lib/agents.tsv` — the single source of truth for tool aliases and paths.
  Columns: `alias`, `project`, `global`, `detect` (`;`-separated, `cmd:` prefix
  means "binary on PATH").
- `bin/awesome-skills` — installer CLI. POSIX sh. Do not add Bash 4 features.
- `scripts/` — maintainer tooling. Python 3 standard library only.
- `tests/test-install.sh` — installer regression tests; runs against a temp HOME.
- `tests/test_sync.py` — upstream sync tests; local `file://` git fixtures, offline.

## Commands

```sh
sh tests/test-install.sh          # full installer test suite
python3 tests/test_sync.py        # upstream sync tests
python3 scripts/validate.py       # frontmatter, links, hygiene
python3 scripts/catalog.py        # regenerate README table + catalog.json + docs/tools.md
python3 scripts/catalog.py --check
python3 scripts/sync_upstreams.py --dry-run  # upstream plan, writes nothing
python3 scripts/sync_upstreams.py --check    # is upstream HEAD newer than the lock?
./scripts/pack.sh                 # build the offline bundle
sh -n bin/awesome-skills          # syntax check
```

Run `sh tests/test-install.sh` after any change to `bin/awesome-skills`,
`lib/agents.tsv`, or `install.sh`. Run `python3 tests/test_sync.py` after touching
`scripts/sync_upstreams.py`, `upstreams.json`, or the lock format.

## Conventions

- **Installer must stay dependency-free**: no Node, no Python, no network, no
  `bash`-only syntax. It must run on an air-gapped macOS/Linux machine.
- **Never delete unowned paths.** New destructive behaviour needs a test.
- **Skills are portable**: frontmatter limited to `name`, `description`,
  `license`, `allowed-tools`, `metadata`, `version`. Avoid agent-specific keys
  in bundled skills.
- **Name = folder**, lowercase with single hyphens, max 64 chars.
- **Description** says when to use the skill, not just what it is; max 1024 chars.
- Keep `SKILL.md` under ~500 lines. Move detail into `references/` and load it
  explicitly.
- Every bundled skill needs a `license` and, if third-party, provenance in
  `sources.json`. The sync script only accepts the licenses listed in
  [docs/upstreams.md](docs/upstreams.md).

## Adding a skill

Original skill:

```sh
cp -R templates/skill skills/my-skill
# edit skills/my-skill/SKILL.md
python3 scripts/validate.py
python3 scripts/catalog.py
sh tests/test-install.sh
```

Reviewed third-party skill: add the repository to `upstreams.json`, put the skill
in its `include` list, then run `python3 scripts/sync_upstreams.py --upstream
<id>`. It writes `skills/<name>/`, `upstreams.lock.json` and `sources.json`.
Never edit a vendored skill by hand — copy it to a new name and remove it from
`include` if you need a local variant.

Do not commit generated `dist/` output or local `.agents/` installs.
