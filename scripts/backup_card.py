#!/usr/bin/env python3
"""
AI Passport 固件完整无损全盘备份工具
用于备份新卡片（例如 muse）的全部 Flash 数据、分区表、Bootloader 及硬件元数据。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_BASE = ROOT / "backup"
ESPTOOL_BIN = ROOT / ".migration-envs/idf/bin/python"
GEN_PART = ROOT.parent / ".toolchains/esp-idf-v5.5.3/components/partition_table/gen_esp32part.py"


def find_device_port(timeout_sec=0):
    start = time.monotonic()
    while True:
        ports = list(Path("/dev").glob("cu.usbmodem*")) + list(Path("/dev").glob("cu.usbserial*"))
        if ports:
            return str(ports[0])
        if timeout_sec <= 0 or (time.monotonic() - start) >= timeout_sec:
            return None
        time.sleep(1)


def run_esptool(port, args):
    cmd = [str(ESPTOOL_BIN), "-m", "esptool", "--chip", "esp32c3", "--port", port] + args
    return subprocess.check_output(cmd, text=True)


def parse_flash_size(flash_info: str) -> int:
    # Look for "Detected flash size: 8MB"
    match = re.search(r"Detected flash size:\s*(\d+)(MB|KB)", flash_info, re.IGNORECASE)
    if match:
        val = int(match.group(1))
        unit = match.group(2).upper()
        if unit == "MB":
            return val * 1024 * 1024
        elif unit == "KB":
            return val * 1024
    # Default fallback for AI Passport
    return 8 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description="Backup AI Passport complete flash image")
    parser.add_argument("--name", default="muse", help="Name/label of this card/firmware (default: muse)")
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    parser.add_argument("--baud", default="921600", help="Baud rate for reading flash (default: 921600)")
    parser.add_argument("--wait", type=int, default=30, help="Wait timeout in seconds for device to connect")
    args = parser.parse_args()

    port = args.port or find_device_port(timeout_sec=args.wait)
    if not port:
        print("❌ 未检测到 AI Passport 串口设备 (/dev/cu.usbmodem* 或 /dev/cu.usbserial*)。")
        print("请用 USB 数据线连接卡片并开机，然后重试。")
        sys.exit(1)

    print(f"🎯 检测到串口设备: {port}")
    print(f"📦 备份目标固件名称: {args.name}")

    out_dir = BACKUP_BASE / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 读取硬件芯片信息与 MAC
    print("🔍 正在读取芯片与 MAC 信息...")
    try:
        chip_info = run_esptool(port, ["chip_id"])
        print(chip_info.strip())
        mac_info = run_esptool(port, ["read_mac"])
        print(mac_info.strip())
        flash_info = run_esptool(port, ["flash_id"])
        print(flash_info.strip())
    except Exception as e:
        print(f"❌ 读取硬件信息失败: {e}")
        sys.exit(1)

    # 提取 MAC 地址
    mac_match = re.search(r"MAC:\s*([0-9a-fA-F:]{17})", mac_info)
    mac_addr = mac_match.group(1).upper() if mac_match else "UNKNOWN"

    flash_size_bytes = parse_flash_size(flash_info)
    flash_size_mb = flash_size_bytes // (1024 * 1024)
    print(f"💾 检测到 Flash 大小: {flash_size_mb} MB ({flash_size_bytes} 字节)")

    raw_bin_name = f"ai_passport_{args.name}_card_{flash_size_mb}mb_original.bin"
    raw_bin_path = out_dir / raw_bin_name

    # 2. 读取全盘镜像
    print(f"🚀 开始全盘备份 (0x0 ~ {hex(flash_size_bytes)})...")
    start_time = time.monotonic()
    try:
        cmd = [
            str(ESPTOOL_BIN), "-m", "esptool",
            "--chip", "esp32c3",
            "--port", port,
            "-b", args.baud,
            "--before", "default_reset",
            "--after", "hard_reset",
            "read_flash",
            "0x0",
            hex(flash_size_bytes),
            str(raw_bin_path)
        ]
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("⚠️ 高速读取失败，尝试使用降级波特率 460800 重新读取...")
        cmd[7] = "460800"
        subprocess.run(cmd, check=True)

    elapsed = time.monotonic() - start_time
    file_bytes = raw_bin_path.stat().st_size
    print(f"✅ 全盘备份完成！耗时: {elapsed:.2f}秒，大小: {file_bytes} 字节")

    # 3. 计算 SHA256 校验和
    sha256 = hashlib.sha256(raw_bin_path.read_bytes()).hexdigest()
    sha_file = out_dir / f"{raw_bin_name}.sha256"
    sha_file.write_text(f"{sha256}  {raw_bin_name}\n")
    print(f"🔑 SHA256: {sha256}")

    # 4. 提取并解析分区表 (Offset 0x8000, 0xC00 bytes)
    partition_csv = ""
    part_bin_path = out_dir / "partition-table.bin"
    if file_bytes >= 0x8C00:
        with open(raw_bin_path, "rb") as f:
            f.seek(0x8000)
            part_data = f.read(0xC00)
            part_bin_path.write_bytes(part_data)
        if GEN_PART.exists():
            try:
                res = subprocess.run(
                    [str(ESPTOOL_BIN), str(GEN_PART), str(part_bin_path)],
                    capture_output=True, text=True, check=True
                )
                partition_csv = res.stdout.strip()
                (out_dir / "partitions.csv").write_text(partition_csv)
                print("\n📋 解析到分区表结构:")
                print(partition_csv)
            except Exception as e:
                print(f"⚠️ 分区表解析异常: {e}")

    # 5. 生成一键还原脚本 restore.sh
    restore_sh = out_dir / f"restore_{args.name}.sh"
    restore_sh_content = f"""#!/usr/bin/env bash
# 一键还原 AI Passport 原厂 {args.name} {flash_size_mb}MB 全盘镜像
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PROJECT_DIR="$(cd "${{SCRIPT_DIR}}/../.." && pwd)"
BACKUP_BIN="${{SCRIPT_DIR}}/{raw_bin_name}"
EXPECTED_SHA256="{sha256}"
ESPTOOL_BIN="${{PROJECT_DIR}}/.migration-envs/idf/bin/python"

if [[ ! -f "${{BACKUP_BIN}}" ]]; then
    echo "❌ 备份文件不存在: ${{BACKUP_BIN}}"
    exit 1
fi

echo "🔍 校验备份文件 SHA256 完整性..."
ACTUAL_SHA256=$(shasum -a 256 "${{BACKUP_BIN}}" | awk '{{print $1}}')
if [[ "${{ACTUAL_SHA256}}" != "${{EXPECTED_SHA256}}" ]]; then
    echo "❌ SHA256 校验失败!"
    echo "期望: ${{EXPECTED_SHA256}}"
    echo "实际: ${{ACTUAL_SHA256}}"
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

if [[ -z "${{PORT}}" ]]; then
    echo "❌ 未检测到 AI Passport 串口设备，请检查 USB 连接并确认开机。"
    exit 1
fi

echo "🎯 目标端口: ${{PORT}}"
echo "⚠️  准备将原厂 {args.name} {flash_size_mb}MB 镜像写回设备，这会覆盖当前 Flash 内容。"
read -r -p "确认恢复? (y/N) " response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo "🚀 开始恢复刷写..."
    "${{ESPTOOL_BIN}}" -m esptool --chip esp32c3 --port "${{PORT}}" -b 460800 write_flash 0x0 "${{BACKUP_BIN}}"
    echo "🎉 恢复完成！设备已重启至原厂 {args.name} 固件。"
else
    echo "已取消恢复。"
fi
"""
    restore_sh.write_text(restore_sh_content)
    restore_sh.chmod(0o755)

    # 6. 生成详细 README.md 记录文档
    readme_path = out_dir / "README.md"
    readme_content = f"""# AI Passport {args.name} 固件 {flash_size_mb}MB Flash 全盘备份归档

本目录存放从 AI Passport 新卡片实体硬件（ESP32-C3）中提取的 100% 原始全盘镜像，包含原厂 bootloader、系统配置扇区、RF 校准、`{args.name}` 应用程序及内置数据。

---

## 一、 备份元数据

* **固件名称**：`{args.name}`
* **备份时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}
* **镜像文件**：`{raw_bin_name}`
* **物理大小**：`{file_bytes}` 字节 ({flash_size_mb}.00 MB, `0x00000000` ~ `{hex(flash_size_bytes)}`)
* **SHA256 校验和**：
  ```text
  {sha256}
  ```
* **硬件身份**：
  * **芯片**：ESP32-C3
  * **MAC 地址**：`{mac_addr}`
  * **端口**：`{port}`
  * **原始固件**：`{args.name}`

---

## 二、 镜像内包含的分区结构

```csv
{partition_csv if partition_csv else "未提取或无标准分区表"}
```

---

## 三、 一键无损恢复原厂

若日后需要将设备还原为 `{args.name}` 状态，只需在终端中执行：

```bash
cd talk2gemi/backup/{args.name}
./restore_{args.name}.sh
```

脚本会自动校验镜像的 SHA256，探测设备端口并安全全盘刷回。
"""
    readme_path.write_text(readme_content)
    print(f"\n📄 备份说明已生成: {readme_path}")
    print(f"🔧 一键还原脚本已生成: {restore_sh}")
    print("🎉 全部备份工作准备就绪！")


if __name__ == "__main__":
    main()
