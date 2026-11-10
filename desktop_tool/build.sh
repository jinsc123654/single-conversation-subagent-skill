#!/usr/bin/env bash
# This companion is source-distributed and optional. No implicit pip/apt/install.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-check}" in
  check) exec bash "$ROOT/run.sh" --check ;;
  run) shift; exec bash "$ROOT/run.sh" "$@" ;;
  paths) printf '%s\n' 'System install: none' 'Own config: ${XDG_CONFIG_HOME:-~/.config}/scs-manager/projects.json' 'Projects: only explicit view refresh/archive actions write files' ;;
  *) printf '%s\n' 'Usage: build.sh check | run [options] | paths' >&2; exit 2 ;;
esac
