#!/usr/bin/env bash
# 一键还原 AI Passport 原厂 muse 8MB 全盘镜像
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKUP_BIN="${SCRIPT_DIR}/ai_passport_muse_card_8mb_original.bin"
EXPECTED_SHA256="ca618d49ba43956bb91ec8f727d5d26f77fd6a94cb7f382295eb26d6471a699c"
if command -v esptool.py >/dev/null 2>&1; then
    ESPTOOL_CMD="esptool.py"
elif "${PROJECT_DIR}/scripts/python.sh" -c "import esptool" >/dev/null 2>&1; then
    ESPTOOL_CMD="${PROJECT_DIR}/scripts/python.sh -m esptool"
else
    ESPTOOL_CMD="python3 -m esptool"
fi

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
PORT=""
for p in /dev/cu.usbmodem* /dev/cu.usbserial*; do
    if [[ -e "$p" ]]; then
        PORT="$p"
        break
    fi
done

if [[ -z "${PORT}" ]]; then
    echo "❌ 未检测到 AI Passport 串口设备，请检查 USB 连接并确认开机。"
    exit 1
fi

echo "🎯 目标端口: ${PORT}"
echo "⚠️  准备将原厂 muse 8MB 镜像写回设备，这会覆盖当前 Flash 内容。"
read -r -p "确认恢复? (y/N) " response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo "🚀 开始恢复刷写..."
    ${ESPTOOL_CMD} --chip esp32c3 --port "${PORT}" -b 460800 write_flash 0x0 "${BACKUP_BIN}"
    echo "🎉 恢复完成！设备已重启至原厂 muse 固件。"
else
    echo "已取消恢复。"
fi
