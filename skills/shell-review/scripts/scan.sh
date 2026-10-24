#!/bin/sh
# scan.sh — heuristic static checks for shell scripts.
#
# Usage: scan.sh FILE...
# Exit:  0 no findings, 1 findings, 2 usage/read error.
#
# This is a triage aid, not a linter. It only reads files; it never executes them.

set -eu

[ $# -gt 0 ] || {
  printf 'usage: %s FILE...\n' "${0##*/}" >&2
  exit 2
}

status=0
for f in "$@"; do
  if [ ! -f "$f" ]; then
    printf '%s: not a file\n' "$f" >&2
    status=2
    continue
  fi
  if ! awk -v file="$f" '
    BEGIN { findings = 0; has_sete = 0 }

    FNR == 1 && $0 !~ /^#!/ {
      printf "%s:%d: [note] no shebang: the invoking shell decides the dialect\n", file, FNR
      findings++
    }

    /^[[:space:]]*#/ { next }

    {
      if ($0 ~ /rm[[:space:]]+-[a-zA-Z]*[rR][a-zA-Z]*f/ ||
          $0 ~ /rm[[:space:]]+-[a-zA-Z]*f[a-zA-Z]*[rR]/) {
        if ($0 ~ /rm[[:space:]]+.*-[a-zA-Z]*[rR][a-zA-Z]*f[[:space:]]+\/([[:space:]]|$|\*)/) {
          printf "%s:%d: [blocker] recursive rm on /: stop and fix the target\n", file, FNR
        } else if ($0 ~ /\$/) {
          printf "%s:%d: [risk] recursive rm with a variable: quote it and guard emptiness\n", file, FNR
        } else {
          printf "%s:%d: [note] recursive rm: verify the scope before running\n", file, FNR
        }
        findings++
      }

      if ($0 ~ /(curl|wget)[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(sh|bash|zsh)/) {
        printf "%s:%d: [blocker] pipes a download into a shell\n", file, FNR
        findings++
      }

      if ($0 ~ /(^|[^[:alnum:]_])eval[[:space:]]/) {
        printf "%s:%d: [risk] eval: input can become code\n", file, FNR
        findings++
      }

      if ($0 ~ /\$\*/) {
        printf "%s:%d: [risk] uses %s%s: prefer \"$@\" unless one joined string is intended\n", file, FNR, "$", "*"
        findings++
      }

      if ($0 ~ /chmod[[:space:]]+777/) {
        printf "%s:%d: [risk] chmod %s: grant the minimum permissions instead\n", file, FNR, "777"
        findings++
      }

      if ($0 ~ /^[[:space:]]*set[[:space:]]+.*(-[a-zA-Z]*e|errexit)/) {
        has_sete = 1
      }
    }

    END {
      if (!has_sete) {
        printf "%s:0: [note] no set -e/errexit: failed commands may be ignored\n", file
        findings++
      }
      if (findings > 0) exit 1
    }
  ' "$f"; then
    status=1
  fi
done

exit "$status"
