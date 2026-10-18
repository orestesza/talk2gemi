#!/usr/bin/env bash
# 一键烧录 talk2gemi 固件至 AI Passport (ESP32-C3)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FIRMWARE_BUILD="${ROOT_DIR}/talk2gemi/firmware/build"

# 检查串口设备
PORT=""
for p in /dev/cu.usbmodem*; do
    if [[ -e "$p" ]]; then
        PORT="$p"
        break
    fi
done

if [[ -z "${PORT}" ]]; then
    echo "❌ 未检测到 AI Passport 串口设备 (/dev/cu.usbmodem*)，请检查 USB 连接。"
    exit 1
fi

if [[ ! -f "${FIRMWARE_BUILD}/talk2gemi.bin" ]]; then
    echo "❌ 固件未构建，请先运行 ./talk2gemi/scripts/build_firmware.sh"
    exit 1
fi

echo "========================================"
echo "🎯 目标设备: ${PORT}"
echo "📦 刷写固件: talk2gemi (ESP-IDF v5.5.3)"
echo "⚠️  注意: 原厂名片备份位于 talk2gemi/backup/，随时可恢复"
echo "========================================"

ESPTOOL="${ROOT_DIR}/.venv/bin/python -m esptool"

${ESPTOOL} --chip esp32c3 --port "${PORT}" -b 460800 \
    --before default_reset --after hard_reset \
    write_flash --flash_mode dio --flash_size 8MB --flash_freq 80m \
    0x0 "${FIRMWARE_BUILD}/bootloader/bootloader.bin" \
    0x8000 "${FIRMWARE_BUILD}/partition_table/partition-table.bin" \
    0x10000 "${FIRMWARE_BUILD}/talk2gemi.bin"

echo "========================================"
echo "🎉 烧录成功！设备已自动重启。"
echo "========================================"
