# Shell review checklist

Use after reading the script top to bottom. Every item is a question, not a
mandate: some are intentional trade-offs. Mark findings **Blocker / Risk / Nit**
and cite `file:line`.

## 1. Contract

- [ ] Is the target dialect explicit? (`#!/bin/sh`, `#!/usr/bin/env bash`, or "sourced library")
- [ ] Does the header state usage, inputs, required env vars, and exit codes?
- [ ] Are destructive actions (delete, overwrite, push, deploy) documented or behind `--yes`?
- [ ] Is the script safe to run twice (idempotent), or does it say it is not?

## 2. Destructive paths

- [ ] Every `rm -rf` target is either a literal, a validated prefix, or refuses to run when empty.
- [ ] Variables are quoted: `rm -rf -- "$dir"`, and `[ -n "$dir" ]` is checked first.
- [ ] `cd` failure is handled: `cd "$dir" || exit 1` (a failed `cd` does not stop the script under plain `set -e` in some shells).
- [ ] Writes resolve through symlinks only when intended; a hostile `$HOME` or symlinked target cannot redirect them.
- [ ] Temporary files use `mktemp`, are removed by a trap, and never live in a predictable world-writable path.
- [ ] `--` precedes user-controlled paths that may start with `-`.

## 3. Quoting and expansion

- [ ] Expansions are quoted: `"$var"`, `"$@"`, `"$(cmd)"`.
- [ ] `$*` is not used for argument forwarding.
- [ ] Globs that may match nothing are guarded (`for f in "$dir"/*; do [ -e "$f" ] || continue`).
- [ ] `read` uses `-r`; `IFS` changes are scoped.
- [ ] Word splitting is intentional where it happens (`for x in $list` is suspect).

## 4. Error handling

- [ ] `set -e` has a deliberate policy; traps and `|| true` do not hide real failures.
- [ ] Pipeline failures are visible (`set -o pipefail` in bash, or explicit status checks).
- [ ] Functions do not mask failures with `local x=$(cmd)`.
- [ ] `trap` cleanup runs on `EXIT HUP INT TERM` and does not delete outside the intended path.
- [ ] Exit codes are meaningful; a failed precondition exits non-zero.

## 5. Input and trust

- [ ] Arguments are validated before use (existence, type, allowed characters).
- [ ] No `eval`, no `sh -c "$user_input"`, no unquoted command construction.
- [ ] Downloads are verified (checksum or signature); `curl | sh` is called out.
- [ ] `sudo` is justified and never combined with untrusted input.
- [ ] Secrets are read from env/files, never embedded, never printed; `set -x` cannot leak them.

## 6. Portability

- [ ] Bashisms are absent from `#!/bin/sh` scripts (`[[`, arrays, `local`, `function`, `source`, `${var,,}`).
- [ ] GNU-only flags are not used on macOS/BSD (`sed -i`, `readlink -f`, `date -d`, `grep -P`, `tar --wildcards`).
- [ ] `echo` is not used for arbitrary data; `printf '%s\n'` is.
- [ ] Paths containing spaces/newlines are handled; `find -print0` + `read -d ''` where it matters.
- [ ] Locale-dependent tools (`sort`, `tr`, `awk`) behave the same under `LC_ALL=C` when output is parsed.
- [ ] Line endings are LF; a CRLF shebang fails with `bad interpreter`.

## 7. Network and installers

- [ ] Offline mode is possible, or the network dependency is stated up front.
- [ ] Installers do not overwrite existing user files without a backup or `--force`.
- [ ] Installed paths are validated (no `../` escape, no absolute path injection from config).
- [ ] Uninstall removes only paths the installer recorded; it does not `rm -rf` a guessed prefix.

## 8. Review output

- [ ] Findings ordered by severity, each with `file:line`, impact, and a concrete fix.
- [ ] No finding without a reason ("quote this" requires the failure mode).
- [ ] Clean areas are named briefly, so the user knows they were checked.
- [ ] The script was **not executed** unless the user explicitly asked for it.
