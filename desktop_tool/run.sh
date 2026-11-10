#!/usr/bin/env bash
# No installation or global configuration changes.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/src"
exec "${SCS_GUI_PYTHON:-python3}" -m scs_manager "$@"
