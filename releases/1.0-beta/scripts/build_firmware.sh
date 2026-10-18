#!/usr/bin/env bash
# 构建 talk2gemi ESP32-C3 固件
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# 激活共享 ESP-IDF 工具链
source "${ROOT_DIR}/passport_lab/activate.sh"

cd "${ROOT_DIR}/talk2gemi/firmware"

echo "========================================"
echo "🛠️  Building talk2gemi Firmware (ESP-IDF v5.5.3)"
echo "========================================"
idf.py build
echo "========================================"
echo "✅ Build completed successfully!"
echo "========================================"
