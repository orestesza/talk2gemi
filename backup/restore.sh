#!/usr/bin/env bash
# 一键还原 AI Passport 原厂 8MB 全盘镜像
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKUP_BIN="${SCRIPT_DIR}/ai_passport_trae_card_8mb_original.bin"
EXPECTED_SHA256="360b594ef5ab8bda25ba788f60e7db3b512cc044de0520834514968f3ed88d27"

if [[ ! -f "${BACKUP_BIN}" ]]; then
    echo "❌ 备份文件不存在: ${BACKUP_BIN}"
    exit 1
fi

echo "🔍 校验备份文件 SHA256 完整性..."
ACTUAL_SHA256=$(shasum -a 256 "${BACKUP_BIN}" | awk '{print $1}')
if [[ "${ACTUAL_SHA256}" != "${EXPECTED_SHA256}" ]]; then
    echo "❌ SHA256 校验失败!"
    echo "期望: ${EXPECTED_SHA256}"
    echo "实际: ${ACTUAL_SHA256}"
    exit 1
fi
echo "✅ SHA256 校验通过!"

# 查找设备端口
if command -v esptool.py >/dev/null 2>&1; then
    ESPTOOL="esptool.py"
elif [[ -f "${ROOT_DIR}/talk2gemi/scripts/python.sh" ]] && "${ROOT_DIR}/talk2gemi/scripts/python.sh" -c "import esptool" >/dev/null 2>&1; then
    ESPTOOL="${ROOT_DIR}/talk2gemi/scripts/python.sh -m esptool"
elif [[ -f "${ROOT_DIR}/.venv/bin/python" ]]; then
    ESPTOOL="${ROOT_DIR}/.venv/bin/python -m esptool"
else
    ESPTOOL="python3 -m esptool"
fi
PORT=""
for p in /dev/cu.usbmodem*; do
    if [[ -e "$p" ]]; then
        PORT="$p"
        break
    fi
done

if [[ -z "${PORT}" ]]; then
    echo "❌ 未检测到 AI Passport 串口设备 (/dev/cu.usbmodem*)，请检查 USB 连接并确认开机。"
    exit 1
fi

echo "🎯 目标端口: ${PORT}"
echo "⚠️  准备将原厂 8MB 镜像写回设备，这会覆盖当前 Flash 内容。"
read -r -p "确认恢复出厂? (y/N) " response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo "🚀 开始恢复刷写..."
    ${ESPTOOL} --chip esp32c3 --port "${PORT}" write_flash 0x0 "${BACKUP_BIN}"
    echo "🎉 恢复完成！设备已重启至原厂 trae_card 名片系统。"
else
    echo "已取消恢复。"
fi
