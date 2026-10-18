# AI Passport muse 固件 8MB Flash 全盘备份归档

本目录存放从 AI Passport 新实体硬件卡片（ESP32-C3）中提取的 100% 原始全盘镜像，包含原厂 bootloader、OTA 分区表、系统配置扇区、RF 校准、`muse-gadget` 应用程序及出厂产品配置多分区数据。

---

## 一、 备份元数据

* **固件名称**：`muse`
* **应用工程**：`muse-gadget`
* **应用版本**：`1.0.5`（编译时间：`Oct 11 2026 01:09:58`）
* **备份时间**：2026-10-18 16:31:47 (UTC+8)
* **镜像文件**：`ai_passport_muse_card_8mb_original.bin`
* **物理大小**：`8,388,608` 字节 (精确 8.00 MB, `0x00000000` ~ `0x00800000`)
* **SHA256 校验和**：
  ```text
  ca618d49ba43956bb91ec8f727d5d26f77fd6a94cb7f382295eb26d6471a699c
  ```
* **硬件身份**：
  * **芯片型号**：ESP32-C3 (QFN32) (revision v1.1)
  * **晶振频率**：40 MHz
  * **MAC 地址**：`4C:11:AE:32:35:E4`
  * **Flash 规格**：Embedded Flash 8MB (XMC)
  * **USB 模式**：Native USB-Serial/JTAG (`/dev/cu.usbmodem1101`)

---

## 二、 镜像内包含的分区结构

| 分区名 | 类型 | 子类型 | 偏移 (Offset) | 大小 (Size) | 用途说明 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `bootloader` | - | - | `0x00000000` | 64 KB | 一级引导程序与芯片初始化 |
| `partition-table` | - | - | `0x00010000` | 4 KB | ESP-IDF OTA 分区定义表 |
| `nvs` | data | nvs | `0x00011000` | 48 KB | Wi-Fi 与系统运行时非易失存储 |
| `otadata` | data | ota | `0x0001D000` | 8 KB | 双区 OTA 固件选择引导槽数据 |
| `phy_init` | data | phy | `0x0001F000` | 4 KB | 射频基带校准数据 |
| `ota_0` | app | ota_0 | `0x00020000` | 3968 KB | `muse-gadget` v1.0.5 应用程序主体 |
| `ota_1` | app | ota_1 | `0x00400000` | 3968 KB | 备用升级分区（当前预留空闲） |
| `prod_data` | data | 0x40 | `0x007E0000` | 4 KB | 出厂产测数据与设备专属参数 |
| `prod_bak` | data | 0x41 | `0x007E1000` | 4 KB | 出厂产测参数备份 |

---

## 三、 一键无损恢复出厂

若日后需要将设备还原为该 `muse` 原厂状态，只需在终端中执行：

```bash
cd talk2gemi/backup/muse
./restore_muse.sh
```

脚本会自动校验镜像的 SHA256 完整性，探测设备串口端口并安全全盘刷回。
