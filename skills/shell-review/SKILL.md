---
name: shell-review
description: Review shell scripts for destructive commands, quoting and portability bugs, and offline-install safety before running or shipping them. Use when the user asks to review, harden, or debug a .sh script, an installer, a Makefile shell block, or a command containing rm -rf, curl | sh, sudo, or eval.
license: MIT
metadata:
  version: "1.0.0"
  author: ZJZ0405
---

# Shell Review

Find the bug that deletes the wrong directory, the one that only appears on
macOS, and the one that silently succeeds after a failed command — before the
script runs.

## When to use

- The user asks to review, audit, harden, or debug a shell script or installer.
- A script contains `rm -rf`, `sudo`, `eval`, `curl | sh`, `chmod 777`, or writes outside a temp dir.
- A script must run on an air-gapped machine, as root, or in CI.
- A shell command is about to be run that could be destructive.

## Non-goals

- **Never execute the script under review** unless the user explicitly asks and
  the environment is disposable. Static review is the default.
- Not a replacement for `shellcheck`; run it if available and triage its output,
  but do not treat a clean scan as proof.
- Not a style review. Formatting nits go last, if at all.

## Workflow

1. **Read the whole file.** Note the shebang, the entry point, and every path
   that is written or deleted. Do not review a script from grep output alone.
2. **Establish the dialect.** `#!/bin/sh` means POSIX: `[[`, arrays, `local`,
   `source`, and `pipefail` are bugs. `#!/usr/bin/env bash` permits them.
3. **Syntax-check without executing.**
   - POSIX: `sh -n script.sh`
   - Bash: `bash -n script.sh`
   - If `shellcheck` exists: `shellcheck -x -S warning script.sh`
4. **Run the bundled heuristic** (read-only):
   `sh scripts/scan.sh path/to/script.sh`
   Treat every hit as a question, not a verdict.
5. **Walk the checklist** in [references/checklist.md](references/checklist.md).
   Prioritise sections 2 (destructive paths), 4 (error handling), and 5 (trust).
6. **Report** with the format below. If there are no blockers, say so plainly.

## Severity

| Level | Meaning | Examples |
| --- | --- | --- |
| **Blocker** | Can destroy data, execute untrusted code, or corrupt an install. Do not run. | `rm -rf "$DIR/"` with a possibly-empty `DIR`; `curl … \| sh`; writing through an unvalidated symlink; `eval "$1"`. |
| **Risk** | Wrong behaviour under realistic conditions (failure, spaces, macOS, rerun). | Unquoted `$var`; `cd` without `\|\| exit`; `local x=$(cmd)`; GNU-only `sed -i`; missing `--`. |
| **Nit** | Style, naming, duplicated logic. | `echo` instead of `printf`; long functions; inconsistent quoting of literals. |

## Report format

```markdown
## Blockers
- `install.sh:42` — `rm -rf "$TARGET"` runs when `TARGET` is empty. Fix: `[ -n "$TARGET" ] && [ "$TARGET" != "/" ] || exit 1` before the delete.

## Risks
- `scripts/sync.sh:17` — unquoted `$FILES` splits on spaces and globs. Fix: `for f in "$@"; do …`.
- `scripts/sync.sh:31` — `readlink -f` is GNU-only; macOS uses `cd "$(dirname "$f")" && pwd -P`.

## Nits
- `scripts/sync.sh:8` — prefer `printf '%s\n'` over `echo` for data.

## Checked, no issue
- Argument validation, trap cleanup, secret handling.
```

Rules for findings:

- Cite `file:line`. Explain the failure mode, not just the rule.
- Give a fix that is a drop-in line or a small block.
- Do not pad the list. Three real findings beat fifteen maybes.
- If the script is fine, say "no blockers" and name what you checked.

## High-value traps

| Trap | Why it bites | Fix |
| --- | --- | --- |
| `rm -rf "$DIR/"` | Empty `DIR` becomes `rm -rf /` | Assert non-empty and not `/` before deleting |
| `cd "$dir"` then delete | Failed `cd` leaves you in the caller's cwd | `cd "$dir" \|\| exit 1` |
| `set -e` + `local x=$(cmd)` | `local` masks the command's status | Declare, then assign separately |
| `cmd \| tee log` | Pipeline status hides `cmd` failure | `set -o pipefail` (bash) or check `PIPESTATUS` |
| `for f in $(ls)` | Breaks on spaces/newlines | `for f in "$dir"/*` or `find … -print0` |
| `echo "$data"` | `-n`, escapes, and backslashes are implementation-defined | `printf '%s\n' "$data"` |
| `sed -i` on macOS | BSD sed needs `sed -i ''` | Detect `uname` or use a temp file |
| `ln -s` over an existing dir | Creates a link *inside* the directory | `[ -e "$dest" ] \|\| ln -s …` |
| Refusing nothing on `--force` | Clobbers user files silently | Back up, then replace |

## Bundled files

- `scripts/scan.sh` — POSIX-sh heuristic scanner. Exit 1 on findings, 2 on usage.
- `references/checklist.md` — the full review checklist, grouped by concern.

Keep the scan output and the manual findings separate: the scanner is noisy by
design, the review is judged by signal.
