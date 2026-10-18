# AI Passport Factory 8MB Full Flash Backup Archive

This directory stores the 100% bit-exact full physical flash dump extracted from the AI Passport (ESP32-C3) hardware card. It includes the factory bootloader, partition table, NVS parameters, RF calibration, `trae_card` firmware application, and personalized image/card assets.

---

## 1. Backup Metadata

* **Backup Time**: 2026-10-15 18:20:44 (UTC+8)
* **Image File**: `ai_passport_trae_card_8mb_original.bin`
* **Size**: `8,388,608` bytes (Exact 8.00 MB, `0x00000000` ~ `0x00800000`)
* **SHA-256 Checksum**:
  ```text
  360b594ef5ab8bda25ba788f60e7db3b512cc044de0520834514968f3ed88d27
  ```
* **Hardware Information**:
  * **Chip**: ESP32-C3 (QFN32) (revision v1.1)
  * **MAC Address**: `4C:11:AE:2F:BA:2C`
  * **Flash Vendor**: XMC (ID: `0x20`, Device: `0x4017`)
  * **Factory Firmware**: `trae_card` v1.2.2 (Build date: Sep 15 2026 11:35:38)

---

## 2. Partition Structure

| Partition Name | Offset | Size | Purpose |
| :--- | :--- | :--- | :--- |
| `nvs` | `0x00009000` | 24 KB | Wi-Fi and system configuration parameters |
| `phy_init` | `0x0000F000` | 4 KB | RF calibration data |
| `factory` | `0x00010000` | 3 MB | `trae_card` 1.2.2 main application |
| `imgstore` | `0x00310000` | 128 KB | System image storage |
| `imgframe` | `0x00330000` | 152 KB | UI frame assets |
| `cardid` | `0x00356000` | 16 KB | Hardware card ID and authentication metadata |
| `imguser` | `0x0035A000` | 128 KB | User card background image |
| `audio` | `0x0037A000` | 512 KB | Audio cue assets |
| `imgava` | `0x003FA000` | 1 MB | Custom high-resolution avatar assets |

---

## 3. One-Click Factory Restoration

To restore the device to its factory firmware:

```bash
cd talk2gemi/backup
./restore.sh
```

The script automatically verifies the image SHA-256, detects the USB serial port, and flashes the 8MB image back to the card.
