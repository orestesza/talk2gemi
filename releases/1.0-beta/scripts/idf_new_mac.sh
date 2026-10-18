#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
BASE_BIN="$WORKSPACE_DIR/.toolchains/python/cpython-3.11.17-macos-aarch64-none/bin"
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1
"$BASE_BIN/python3" "$PROJECT_DIR/scripts/prepare_new_mac.py" --prepare
export IDF_TOOLS_PATH="$PROJECT_DIR/.migration-envs/espressif"
export IDF_PYTHON_ENV_PATH="$PROJECT_DIR/.migration-envs/idf"
export IDF_COMPONENT_MANAGER=1
export IDF_COMPONENT_CACHE_PATH="$WORKSPACE_DIR/.toolchains/component-cache"
export XDG_CACHE_HOME="$PROJECT_DIR/.migration-envs/cache"
export CC="$WORKSPACE_DIR/passport_lab/tools/macos-host-cc"
export PATH="$BASE_BIN:$PATH"
source "$WORKSPACE_DIR/.toolchains/esp-idf-v5.5.3/export.sh"
cd "$PROJECT_DIR/firmware"
exec "$IDF_PYTHON_ENV_PATH/bin/python" "$IDF_PATH/tools/idf.py" -B build-new-mac "$@"
