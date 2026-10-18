#!/usr/bin/env bash
# 查看 AI Passport 串口运行日志
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PORT=""
for p in /dev/cu.usbmodem*; do
    if [[ -e "$p" ]]; then
        PORT="$p"
        break
    fi
done

if [[ -z "${PORT}" ]]; then
    echo "❌ 未检测到 AI Passport 串口设备 (/dev/cu.usbmodem*)"
    exit 1
fi

echo "👀 打开串口监视器: ${PORT} (115200 波特率，按 Ctrl+] 退出)..."
exec "${ROOT_DIR}/.venv/bin/python" -m serial.tools.miniterm --raw --dtr 0 --rts 0 "${PORT}" 115200
