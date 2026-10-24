#!/bin/sh
# pack.sh — build a portable, offline-friendly bundle.
#
# Usage: scripts/pack.sh [--out DIR] [--zip]
#
# Produces dist/awesome-skills-<version>.tar.gz plus a .sha256 file. The
# archive contains the repository without .git, dist, caches, or local installs.

set -eu

_self=$0
case $_self in
  */*) ;;
  *) _self=$(command -v "$_self" 2>/dev/null || printf '%s\n' "$_self") ;;
esac
ROOT=$(CDPATH= cd -- "$(dirname -- "$_self")/.." && pwd -P)
VERSION=$(sed -n '1p' "$ROOT/VERSION" 2>/dev/null || printf 'dev')
NAME=awesome-skills-$VERSION
OUT=$ROOT/dist
MAKE_ZIP=0

while [ $# -gt 0 ]; do
  case $1 in
    --out)
      [ $# -ge 2 ] || {
        printf 'error: --out requires a directory\n' >&2
        exit 1
      }
      OUT=$2
      shift 2
      ;;
    --out=*)
      OUT=${1#*=}
      shift
      ;;
    --zip)
      MAKE_ZIP=1
      shift
      ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

case $OUT in
  /*) ;;
  *) OUT=$(pwd)/$OUT ;;
esac

mkdir -p "$OUT"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/awesome-skills-pack.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM

if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null || true)" ]; then
    printf 'note: working tree has uncommitted changes; they will be included\n' >&2
  fi
fi

mkdir -p "$STAGE/$NAME"
(
  cd "$ROOT"
  COPYFILE_DISABLE=1 tar -cf - \
    --exclude='./.git' \
    --exclude='./dist' \
    --exclude='./.agents' \
    --exclude='*.pyc' \
    --exclude='*/__pycache__' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    --exclude='./.venv' \
    --exclude='./node_modules' \
    . 2>/dev/null
) | (
  cd "$STAGE/$NAME"
  tar -xf -
)

if find "$STAGE/$NAME" -name .git -print -quit | grep -q .; then
  printf 'error: .git leaked into the bundle\n' >&2
  exit 1
fi

ARCHIVE=$OUT/$NAME.tar.gz
rm -f "$ARCHIVE"
(
  cd "$STAGE"
  COPYFILE_DISABLE=1 tar -czf "$ARCHIVE" "$NAME"
)

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    return 1
  fi
}

if HASH=$(hash_file "$ARCHIVE"); then
  printf '%s  %s\n' "$HASH" "$NAME.tar.gz" >"$ARCHIVE.sha256"
else
  HASH="(sha256 tool not found)"
fi

if [ "$MAKE_ZIP" = 1 ]; then
  if command -v zip >/dev/null 2>&1; then
    rm -f "$OUT/$NAME.zip"
    (
      cd "$STAGE"
      zip -qr "$OUT/$NAME.zip" "$NAME"
    )
    if ZIPHASH=$(hash_file "$OUT/$NAME.zip"); then
      printf '%s  %s\n' "$ZIPHASH" "$NAME.zip" >"$OUT/$NAME.zip.sha256"
    fi
  else
    printf 'note: zip not installed; skipped .zip\n' >&2
  fi
fi

printf '\nCreated %s (%s)\n' "$ARCHIVE" "$(du -h "$ARCHIVE" | cut -f1 | tr -d ' ')"
if [ "$HASH" != "(sha256 tool not found)" ]; then
  printf 'sha256  %s\n' "$HASH"
  printf '\nVerify and install on the offline machine:\n'
  printf '  sha256sum -c %s.sha256   # or: shasum -a 256 -c ...\n' "$NAME.tar.gz"
  printf '  tar xzf %s\n' "$NAME.tar.gz"
  printf '  cd %s && ./install.sh --tool auto\n' "$NAME"
fi
