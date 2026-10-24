#!/bin/sh
# Regression tests for the installer. Uses throwaway HOME directories and never
# touches the real user configuration.
#
# Run: sh tests/test-install.sh

set -eu

_self=$0
case $_self in
  */*) ;;
  *) _self=$(command -v "$_self" 2>/dev/null || printf '%s\n' "$_self") ;;
esac
REPO=$(CDPATH= cd -- "$(dirname -- "$_self")/.." && pwd -P)
CLI=$REPO/bin/awesome-skills
INSTALL=$REPO/install.sh

TESTROOT=$(mktemp -d "${TMPDIR:-/tmp}/awesome-skills-test.XXXXXX")
trap 'rm -rf "$TESTROOT"' EXIT HUP INT TERM

PASS=0
FAIL=0

# Three managed paths per skill: hub + two tool mirrors.
SKILL_COUNT=0
for _skill_md in "$REPO"/skills/*/SKILL.md; do
  [ -f "$_skill_md" ] || continue
  case $_skill_md in
    */.*) continue ;;
  esac
  SKILL_COUNT=$((SKILL_COUNT + 1))
done
MANIFEST_COUNT=$((SKILL_COUNT * 3))

pass() {
  PASS=$((PASS + 1))
  printf 'ok   %s\n' "$1"
}

fail() {
  FAIL=$((FAIL + 1))
  printf 'FAIL %s\n' "$1"
}

run_q() {
  _home=$1
  shift
  HOME=$_home "$CLI" "$@" >/dev/null 2>&1
}

run_out() {
  _home=$1
  shift
  HOME=$_home "$CLI" "$@"
}

expect_ok() {
  _desc=$1
  shift
  if "$@" >/dev/null 2>&1; then pass "$_desc"; else fail "$_desc"; fi
}

expect_fail() {
  _desc=$1
  shift
  if "$@" >/dev/null 2>&1; then fail "$_desc"; else pass "$_desc"; fi
}

expect_file() {
  if [ -f "$2" ]; then pass "$1"; else fail "$1 ($2 missing)"; fi
}

expect_dir() {
  if [ -d "$2" ]; then pass "$1"; else fail "$1 ($2 missing)"; fi
}

expect_absent() {
  if [ -e "$2" ] || [ -L "$2" ]; then fail "$1 ($2 exists)"; else pass "$1"; fi
}

expect_link_to() {
  if [ ! -L "$2" ]; then
    fail "$1 ($2 is not a symlink)"
    return
  fi
  if [ "$(readlink "$2")" = "$3" ]; then
    pass "$1"
  else
    fail "$1 (link points to $(readlink "$2"))"
  fi
}

expect_contains() {
  if grep -Fq "$2" "$3" 2>/dev/null; then pass "$1"; else fail "$1"; fi
}

expect_count() {
  _actual=$(awk '!/^#/' "$2" | wc -l | tr -d ' ')
  if [ "$_actual" = "$3" ]; then pass "$1"; else fail "$1 (got $_actual, want $3)"; fi
}

printf 'awesome-skills installer tests\n'
printf 'repo: %s\n\n' "$REPO"

# ---------------------------------------------------------------------------
# 1. Basic global install with links
# ---------------------------------------------------------------------------
H1=$TESTROOT/h1
mkdir -p "$H1"

expect_ok "install: claude-code + cursor" run_q "$H1" install --tool claude-code,cursor --quiet
expect_file "install: hub copy" "$H1/.agents/skills/handoff/SKILL.md"
expect_file "install: nested reference" "$H1/.agents/skills/handoff/references/template.md"
expect_link_to "install: claude link" "$H1/.claude/skills/handoff" "$H1/.agents/skills/handoff"
expect_link_to "install: cursor link" "$H1/.cursor/skills/shell-review" "$H1/.agents/skills/shell-review"
expect_file "install: marker" "$H1/.agents/skills/handoff/.awesome-skills-skill"
expect_count "install: manifest entries" "$H1/.agents/.awesome-skills/manifest.tsv" "$MANIFEST_COUNT"

# Idempotency
expect_ok "install: rerun is idempotent" run_q "$H1" install --tool claude-code,cursor --quiet
expect_count "install: no duplicate manifest entries" "$H1/.agents/.awesome-skills/manifest.tsv" "$MANIFEST_COUNT"

# Skill filter
H1B=$TESTROOT/h1b
mkdir -p "$H1B"
expect_ok "install: --skill filter" run_q "$H1B" install --tool claude-code --skill handoff --quiet
expect_file "install: selected skill installed" "$H1B/.claude/skills/handoff/SKILL.md"
expect_absent "install: unselected skill skipped" "$H1B/.claude/skills/shell-review"
expect_file "install: hub contains selected skill" "$H1B/.agents/skills/handoff/SKILL.md"
expect_absent "install: hub does not contain unselected skill" "$H1B/.agents/skills/shell-review"

# ---------------------------------------------------------------------------
# 2. Copy mode
# ---------------------------------------------------------------------------
H2=$TESTROOT/h2
mkdir -p "$H2"
expect_ok "copy: install" run_q "$H2" install --tool gemini-cli --copy --quiet
expect_file "copy: file exists" "$H2/.gemini/skills/handoff/SKILL.md"
if [ -L "$H2/.gemini/skills/handoff" ]; then
  fail "copy: must not be a symlink"
else
  pass "copy: not a symlink"
fi
expect_file "copy: marker" "$H2/.gemini/skills/handoff/.awesome-skills-skill"
expect_ok "copy: uninstall all" run_q "$H2" uninstall --all --quiet
expect_absent "copy: mirror removed" "$H2/.gemini/skills/handoff"
expect_absent "copy: hub removed" "$H2/.agents/skills"

# ---------------------------------------------------------------------------
# 3. Project install (path with a space)
# ---------------------------------------------------------------------------
PROJ="$TESTROOT/proj with space"
mkdir -p "$PROJ"
PROJ_ABS=$(cd "$PROJ" && pwd -P)
expect_ok "project: install" run_q "$TESTROOT" install --project "$PROJ" --tool claude-code,opencode --quiet
expect_file "project: hub" "$PROJ/.agents/skills/handoff/SKILL.md"
expect_link_to "project: claude mirror" "$PROJ/.claude/skills/handoff" "$PROJ_ABS/.agents/skills/handoff"
expect_file "project: manifest" "$PROJ/.agents/.awesome-skills/manifest.tsv"
expect_absent "project: no stray project skills dir" "$PROJ/skills"

mkdir -p "$PROJ/.claude/skills/keep-me"
printf 'keep\n' >"$PROJ/.claude/skills/keep-me/SKILL.md"
expect_ok "project: uninstall all" run_q "$TESTROOT" uninstall --project "$PROJ" --all --quiet
expect_absent "project: hub removed" "$PROJ/.agents/skills"
expect_file "project: unmanaged skill kept" "$PROJ/.claude/skills/keep-me/SKILL.md"

# ---------------------------------------------------------------------------
# 4. Conflict handling and backup
# ---------------------------------------------------------------------------
H4=$TESTROOT/h4
mkdir -p "$H4/.claude/skills/handoff"
printf 'original content\n' >"$H4/.claude/skills/handoff/SKILL.md"

expect_fail "conflict: refuses unowned path" run_q "$H4" install --tool claude-code --quiet
expect_ok "conflict: --force succeeds" run_q "$H4" install --tool claude-code --force --quiet
expect_link_to "conflict: replaced with link" "$H4/.claude/skills/handoff" "$H4/.agents/skills/handoff"

BACKUP_FILE=$(find "$H4/.agents/.awesome-skills/backups" -name SKILL.md -print 2>/dev/null | sed -n '1p')
if [ -n "$BACKUP_FILE" ] && grep -Fq 'original content' "$BACKUP_FILE"; then
  pass "conflict: original backed up"
else
  fail "conflict: backup missing"
fi

# ---------------------------------------------------------------------------
# 4b. A path the user replaced must not be deleted
# ---------------------------------------------------------------------------
H10=$TESTROOT/h10
mkdir -p "$H10"
expect_ok "safety: install before replacement" run_q "$H10" install --tool claude-code --quiet
rm -f "$H10/.claude/skills/handoff"
mkdir -p "$H10/.claude/skills/handoff"
printf 'user replacement\n' >"$H10/.claude/skills/handoff/SKILL.md"
expect_ok "safety: uninstall succeeds" run_q "$H10" uninstall --all --quiet
expect_file "safety: replaced path kept" "$H10/.claude/skills/handoff/SKILL.md"
expect_absent "safety: hub still removed" "$H10/.agents/skills"

# ---------------------------------------------------------------------------
# 5. Dry run changes nothing
# ---------------------------------------------------------------------------
H5=$TESTROOT/h5
mkdir -p "$H5"
expect_ok "dry-run: succeeds" run_q "$H5" install --tool claude-code --dry-run --quiet
expect_absent "dry-run: no hub written" "$H5/.agents"
expect_absent "dry-run: no tool dir written" "$H5/.claude"

# ---------------------------------------------------------------------------
# 6. Auto detection (stub claude only)
# ---------------------------------------------------------------------------
H6=$TESTROOT/h6
STUB=$TESTROOT/stub-bin
mkdir -p "$H6" "$STUB"
printf '#!/bin/sh\nexit 0\n' >"$STUB/claude"
chmod +x "$STUB/claude"

if PATH="$STUB:/usr/bin:/bin" HOME="$H6" sh "$INSTALL" --tool auto --quiet >/dev/null 2>&1; then
  pass "auto: install with stub claude"
else
  fail "auto: install with stub claude"
fi
expect_file "auto: claude detected" "$H6/.claude/skills/handoff/SKILL.md"
expect_absent "auto: cursor not touched" "$H6/.cursor"

mkdir -p "$H6/.claude/skills/keep-me"
printf 'keep\n' >"$H6/.claude/skills/keep-me/SKILL.md"
expect_ok "auto: uninstall all" run_q "$H6" uninstall --all --quiet
expect_file "auto: unmanaged skill kept" "$H6/.claude/skills/keep-me/SKILL.md"
expect_absent "auto: managed skill removed" "$H6/.claude/skills/handoff"

# ---------------------------------------------------------------------------
# 7. List, tools, doctor
# ---------------------------------------------------------------------------
LIST_OUT=$(run_out "$H1" list --installed)
case $LIST_OUT in
  *handoff*) pass "list: shows installed skill" ;;
  *) fail "list: shows installed skill" ;;
esac

TOOLS_OUT=$(run_out "$H1" tools)
case $TOOLS_OUT in
  *claude-code*)
    case $TOOLS_OUT in
      *dsh*) pass "tools: lists aliases" ;;
      *) fail "tools: lists aliases" ;;
    esac
    ;;
  *) fail "tools: lists aliases" ;;
esac

DOCTOR_OUT=$(run_out "$H1" doctor 2>&1 || true)
case $DOCTOR_OUT in
  *'frontmatter: valid'*) pass "doctor: validates frontmatter" ;;
  *) fail "doctor: validates frontmatter" ;;
esac

rm -f "$H1/.claude/skills/handoff"
ln -s "$TESTROOT/does-not-exist" "$H1/.claude/skills/handoff"
DOCTOR_OUT=$(run_out "$H1" doctor 2>&1 || true)
case $DOCTOR_OUT in
  *'broken symlink'*) pass "doctor: reports broken symlink" ;;
  *) fail "doctor: reports broken symlink" ;;
esac

# ---------------------------------------------------------------------------
# 8. Fail closed on bad input
# ---------------------------------------------------------------------------
expect_fail "errors: unknown tool" run_q "$H1" install --tool nope --quiet
expect_fail "errors: unknown skill" run_q "$H1" install --skill nope --quiet
expect_fail "errors: missing project dir" run_q "$H1" install --project "$TESTROOT/nope" --quiet
expect_fail "errors: uninstall without selection" run_q "$H1" uninstall --quiet

# ---------------------------------------------------------------------------
# 9. --tool all is syntactically valid (dry run)
# ---------------------------------------------------------------------------
H9=$TESTROOT/h9
mkdir -p "$H9"
expect_ok "all: dry-run with every alias" run_q "$H9" install --tool all --dry-run --quiet
expect_absent "all: dry-run writes nothing" "$H9/.agents"

# ---------------------------------------------------------------------------
# 10. Install wrapper help
# ---------------------------------------------------------------------------
HELP_OUT=$(sh "$INSTALL" --help)
case $HELP_OUT in
  *USAGE*) pass "install.sh: help works" ;;
  *) fail "install.sh: help works" ;;
esac

# ---------------------------------------------------------------------------
# 11. Config-directory environment variables
# ---------------------------------------------------------------------------
H11=$TESTROOT/h11
CFG11=$TESTROOT/cfg11
XDG11=$TESTROOT/xdg11
mkdir -p "$H11" "$CFG11" "$XDG11"

expect_ok "env: install with CLAUDE_CONFIG_DIR" \
  env HOME="$H11" CLAUDE_CONFIG_DIR="$CFG11" "$CLI" install \
  --tool claude-code --skill handoff --quiet
expect_file "env: hub copy still under HOME" "$H11/.agents/skills/handoff/SKILL.md"
expect_link_to "env: claude mirror follows CLAUDE_CONFIG_DIR" \
  "$CFG11/skills/handoff" "$H11/.agents/skills/handoff"
expect_absent "env: legacy ~/.claude mirror not created" "$H11/.claude/skills/handoff"

expect_ok "env: install with XDG_CONFIG_HOME" \
  env HOME="$H11" XDG_CONFIG_HOME="$XDG11" "$CLI" install \
  --tool opencode,goose --skill handoff --quiet
expect_link_to "env: opencode mirror under XDG_CONFIG_HOME" \
  "$XDG11/opencode/skills/handoff" "$H11/.agents/skills/handoff"
expect_link_to "env: goose mirror under XDG_CONFIG_HOME" \
  "$XDG11/goose/skills/handoff" "$H11/.agents/skills/handoff"

expect_ok "env: empty variable falls back to the default" \
  env HOME="$H11" CLAUDE_CONFIG_DIR= XDG_CONFIG_HOME= "$CLI" install \
  --tool claude-code --skill shell-review --quiet
expect_link_to "env: default claude mirror used when unset" \
  "$H11/.claude/skills/shell-review" "$H11/.agents/skills/shell-review"

TOOLS_ENV=$(env HOME="$H11" CLAUDE_CONFIG_DIR="$CFG11" "$CLI" tools --tool claude-code 2>&1)
case $TOOLS_ENV in
  *"$CFG11/skills"*) pass "env: tools shows the resolved path" ;;
  *) fail "env: tools shows the resolved path" ;;
esac

expect_ok "env: uninstall honours the same directories" \
  env HOME="$H11" CLAUDE_CONFIG_DIR="$CFG11" "$CLI" uninstall \
  --tool claude-code --skill handoff --quiet
expect_absent "env: claude mirror removed from CLAUDE_CONFIG_DIR" "$CFG11/skills/handoff"
expect_file "env: hub copy kept while other skills remain" "$H11/.agents/skills/handoff/SKILL.md"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
