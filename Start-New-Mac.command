#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_PYTHON="$PROJECT_DIR/../.toolchains/python/cpython-3.11.17-macos-aarch64-none/bin/python3"
unset GEMINI_API_KEY PYTHONPATH PYTHONHOME
export TALK2GEMI_LOAD_DOTENV=0
export TALK2GEMI_STATE_DIR="${TALK2GEMI_STATE_DIR:-$HOME/Library/Application Support/talk2gemi}"
export TALK2GEMI_AUTOSTART_RELAY=1
"$BASE_PYTHON" "$PROJECT_DIR/scripts/prepare_new_mac.py" --prepare
APP="$PROJECT_DIR/mac/build/talk2gemi.app"
if [[ ! -x "$APP/Contents/MacOS/Talk2Gemi" ]]; then
  bash "$PROJECT_DIR/scripts/build_companion.sh"
fi
open -n "$APP" --args --project "$PROJECT_DIR" --migration
echo 'T2G is starting. Enter the Gemini API key in T2G → Gemini API Key… on this Mac.'
