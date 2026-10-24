#!/bin/sh
# Convenience wrapper: install skills from this repository.
#
#   ./install.sh --tool auto
#
# The real CLI lives in bin/awesome-skills (install, uninstall, list, doctor).

set -eu

_self=$0
case $_self in
  */*) ;;
  *) _self=$(command -v "$_self" 2>/dev/null || printf '%s\n' "$_self") ;;
esac

while [ -L "$_self" ]; do
  _dir=$(CDPATH= cd -- "$(dirname -- "$_self")" && pwd -P)
  _link=$(readlink "$_self")
  case $_link in
    /*) _self=$_link ;;
    *) _self=$_dir/$_link ;;
  esac
done

ROOT=$(CDPATH= cd -- "$(dirname -- "$_self")" && pwd -P)
exec sh "$ROOT/bin/awesome-skills" install "$@"
