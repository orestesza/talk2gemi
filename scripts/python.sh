#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
BASE_PYTHON="$WORKSPACE_DIR/.toolchains/python/cpython-3.11.17-macos-aarch64-none/bin/python3"
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1
"$BASE_PYTHON" "$PROJECT_DIR/scripts/prepare_new_mac.py" --prepare >&2
kind=relay
if [[ "${1:-}" == --idf ]]; then kind=idf; shift; fi
exec "$PROJECT_DIR/.migration-envs/$kind/bin/python" "$@"
