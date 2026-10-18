# AI Passport Muse Firmware 8MB Full Flash Backup Archive

This directory stores the 100% bit-exact full physical flash dump extracted from the AI Passport (ESP32-C3) hardware card running the factory `muse` firmware. It includes the factory bootloader, dual-slot OTA partition table, NVS storage, RF calibration, `muse-gadget` application, and factory production calibration data.

---

## 1. Backup Metadata

* **Firmware Name**: `muse`
* **Application Project**: `muse-gadget`
* **Application Version**: `1.0.5` (Build date: `Oct 11 2026 01:09:58`)
* **Backup Time**: 2026-10-18 16:31:47 (UTC+8)
* **Image File**: `ai_passport_muse_card_8mb_original.bin`
* **Size**: `8,388,608` bytes (Exact 8.00 MB, `0x00000000` ~ `0x00800000`)
* **SHA-256 Checksum**:
  ```text
  ca618d49ba43956bb91ec8f727d5d26f77fd6a94cb7f382295eb26d6471a699c
  ```
* **Hardware Information**:
  * **Chip**: ESP32-C3 (QFN32) (revision v1.1)
  * **Crystal Frequency**: 40 MHz
  * **MAC Address**: `4C:11:AE:32:35:E4`
  * **Flash Specs**: Embedded Flash 8MB (XMC)
  * **USB Mode**: Native USB-Serial/JTAG (`/dev/cu.usbmodem1101`)

---

## 2. Partition Structure

| Partition Name | Type | Subtype | Offset | Size | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `bootloader` | - | - | `0x00000000` | 64 KB | First-stage bootloader and hardware init |
| `partition-table` | - | - | `0x00010000` | 4 KB | ESP-IDF OTA partition table |
| `nvs` | data | nvs | `0x00011000` | 48 KB | Non-volatile runtime storage |
| `otadata` | data | ota | `0x0001D000` | 8 KB | Dual-slot OTA boot selection data |
| `phy_init` | data | phy | `0x0001F000` | 4 KB | Baseband RF calibration parameters |
| `ota_0` | app | ota_0 | `0x00020000` | 3968 KB | `muse-gadget` v1.0.5 active application |
| `ota_1` | app | ota_1 | `0x00400000` | 3968 KB | Secondary OTA slot (currently reserved) |
| `prod_data` | data | 0x40 | `0x007E0000` | 4 KB | Factory production test and provisioning data |
| `prod_bak` | data | 0x41 | `0x007E1000` | 4 KB | Factory production data backup |

---

## 3. One-Click Factory Restoration

To restore the device to its `muse` factory firmware:

```bash
cd talk2gemi/backup/muse
./restore_muse.sh
```

The script automatically verifies image SHA-256 integrity, detects the USB serial port, and flashes the 8MB image back to the device.
