#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$PROJECT_DIR/mac/build/talk2gemi.app"
test -x "$APP/Contents/MacOS/Talk2Gemi" || bash "$PROJECT_DIR/scripts/build_companion.sh"
open "$APP" --args --project "$PROJECT_DIR"
