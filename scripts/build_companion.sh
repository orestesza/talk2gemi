#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$PROJECT_DIR/mac/build/talk2gemi.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$PROJECT_DIR/.runtime" "$PROJECT_DIR/mac/build/module-cache"
cp "$PROJECT_DIR/mac/Info.plist" "$APP/Contents/Info.plist"
cp "$PROJECT_DIR/mac/owl_status.png" "$APP/Contents/Resources/owl_status.png"
cp "$PROJECT_DIR/mac/owl_status@2x.png" "$APP/Contents/Resources/owl_status@2x.png"
xcrun swiftc -swift-version 5 -target arm64-apple-macos13.0 -O -module-cache-path "$PROJECT_DIR/mac/build/module-cache" \
  -framework AppKit -framework CoreBluetooth -framework ServiceManagement -framework Security \
  "$PROJECT_DIR/mac/Talk2Gemi.swift" "$PROJECT_DIR/mac/Keychain.swift" "$PROJECT_DIR/mac/main.swift" -o "$APP/Contents/MacOS/Talk2Gemi"
/usr/bin/xattr -dr com.apple.FinderInfo "$APP" 2>/dev/null || true
/usr/bin/xattr -dr com.apple.ResourceFork "$APP" 2>/dev/null || true
SIGNING_IDENTITY="$("$PROJECT_DIR/../.toolchains/python/cpython-3.11.17-macos-aarch64-none/bin/python3" "$PROJECT_DIR/scripts/local_signing_identity.py" --create)"
codesign --force --sign "$SIGNING_IDENTITY" "$APP"
echo "$APP"
