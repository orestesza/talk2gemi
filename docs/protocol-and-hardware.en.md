# talk2gemi Protocol & Hardware Technical Specification

This document provides a factual, engineering-level reference for talk2gemi's hardware pinouts, Bluetooth Low Energy (BLE) framing protocol, audio codec pipelines, and bidirectional flow control mechanisms.

---

## I. Hardware Platform & Pin Definitions

The project targets the **FoloToy AI Passport** form factor. The core hardware components are detailed below:

### 1. SoC & Memory
- **SoC**: ESP32-C3-WROOM-02
- **Architecture**: 32-bit single-core RISC-V, up to 160 MHz
- **SRAM**: 400 KB internal SRAM
- **Storage**: 8 MB external SPI Flash (Quad SPI, 80MHz)

### 2. Audio Codec (ES8311)
The Everest ES8311 is a low-power mono audio codec configured via I2C and fed via I2S.
- **I2C Control Bus**:
  - `SDA`: GPIO 6
  - `SCL`: GPIO 7
  - `Port`: I2C_NUM_0, standard 100 kHz clock
- **I2S Audio Bus**:
  - `MCLK`: GPIO 3
  - `BCLK`: GPIO 2
  - `WS (LRCK)`: GPIO 10
  - `DOUT (Playback)`: GPIO 4
  - `DIN (Record)`: GPIO 5
- **Audio Format**: 16,000 Hz, 16-bit, Mono PCM.
- **Power Amplifier**: Controlled via GPIO 8 (pulled high during active playback).

### 3. Display (ST7789)
1.54-inch color SPI LCD, 240×320 resolution, RGB565 pixel format.
- `MOSI (SDA)`: GPIO 18
- `SCLK`: GPIO 19
- `CS`: GPIO 1
- `DC (Data/Command)`: GPIO 0
- `RST`: Hardware reset line
- `BL`: PWM-controlled backlight brightness

### 4. Buttons
- **Front OK Button**: Voltage-divider key sampled via GPIO 9 / ADC. Configured as the Push-To-Talk (PTT) input.
- **Side UP / DOWN Buttons**: Handle volume adjustments and settings navigation.

---

## II. Bluetooth Low Energy (BLE GATT) Framing Protocol

The talk2gemi badge acts as a BLE Peripheral, while the host Mac companion serves as the BLE Central. All communications flow through custom GATT characteristics.

### 1. Packet Structure

All transmitted packets use a binary header with little-endian fields:

```text
+--------+--------+--------+--------+------------------+---------------------+
| Byte 0 | Byte 1 | Byte 2 | Byte 3 |  Bytes 4 ~ 5     |  Bytes 6 ~ N        |
| Magic  |  Seq   |  Kind  | Flags  | Payload Length   |      Payload        |
| (0x54) | (0-255)| (Type) |(Flags) |  (uint16_le)     |  (Variable bytes)   |
+--------+--------+--------+--------+------------------+---------------------+
```

- **Magic**: Constant `0x54` (ASCII character `'T'`).
- **Seq**: Cyclic 0–255 packet sequence counter for integrity verification.
- **Kind**:
  - `kind = 1`: Control frame (JSON payload for `start`, `stop`, `hello`, `state`, `codec`).
  - `kind = 2`: Downlink audio chunk (Opus or ADPCM frame).
  - `kind = 3`: Transport ACK frame (acknowledges receipt of a specific sequence number).
  - `kind = 4`: Uplink audio chunk (microphone ADPCM compressed voice chunk).
- **Flags**: Reserved control bits.
- **Payload Length**: Size of payload in bytes.

---

## III. Streaming & Flow Control Architecture

BLE channels offer limited bandwidth (typically 20–50 KB/s in practice) and discrete connection intervals (15–30ms). The streaming pipeline uses targeted strategies to avoid dropouts and buffer overflows:

### 1. Uplink Streaming Strategy
- **Issue**: Early revisions applied application-level ACKs to each microphone audio chunk. During minor latency spikes, the uplink queue filled up the FreeRTOS ring buffer, resulting in `Uplink buffer full / Audio link interrupted` disconnects.
- **Solution**:
  - Uplink audio packets are assigned to dedicated `kind = 4` frames.
  - Chunks (1024-byte PCM compressed to 256-byte ADPCM) stream unidirectionally without per-chunk application ACKs.
  - Crucial boundaries (`start` and `stop`) retain synchronous acknowledgment.
  - Eliminates uplink queue congestion entirely.

### 2. Downlink 3-Frame Sliding Window & Backpressure
- **Issue**: ESP32-C3 audio decoding and I2S playback are driven by hardware clocking (consuming exactly 16,000 samples per second). If the host Mac pumps audio faster than real time, the device's finite RAM quickly overflows.
- **Solution**:
  - **Bounded Flight Window**: The Mac host tracks in-flight audio frames and caps unacknowledged frames at **3 frames**.
  - **Playback Queue ACKs**: Once the badge successfully pushes a packet into its I2S playback FIFO, it returns a `kind = 3` ACK. The host only slides the window and sends subsequent frames upon receiving this ACK.
  - **Prebuffering**: The host sends up to 80ms of audio continuously at turn start to establish a healthy water mark before relying on regular ACK pacing.

### 3. Decoupled Playback Queue
- **Issue**: For long responses (>30 seconds, e.g., reciting a poem), Gemini Cloud generates and transmits the entire response within seconds over WebSocket. If the host blocks reading the WebSocket inside a tight loop waiting for hardware playback backpressure, the socket read buffer starves, causing WebSocket 1006 abnormal termination. Concurrently, the 15-second keepalive timer misinterprets the stalled read as an idle hang.
- **Solution**:
  - Separates network intake from device delivery via `self.playback_queue` and an independent `_playback_worker`.
  - The WebSocket receive loop (`_gemini_receive_loop`) immediately empties network packets into the queue in memory.
  - `_playback_worker` meters delivery to the badge according to the 3-frame BLE window.
  - `_gemini_keepalive_loop` checks `not self.busy`, preventing preemptive task cancellation while playback remains in progress.

---

## IV. Audio Codec Pipeline

```text
[Uplink Record Pipeline]
ES8311 Mic -> 16kHz 16-bit Mono PCM (1024 bytes / 32ms) -> IMA ADPCM Encoder (256 bytes)
           -> BLE kind=4 Frame -> Mac Companion -> ADPCM Decoder to 16kHz PCM
           -> Stream to Google Gemini Live API

[Downlink Playback Pipeline]
Gemini 3.8 Live API -> 24kHz Mono PCM Audio Stream
                    -> Host Resampling (audioop) to 16,000 Hz
                    -> libopus Encoder (16kHz, 20ms frame, VBR ~24kbps)
                    -> Push to playback_queue
                    -> _playback_worker + 3-frame pacing over BLE kind=2
                    -> Device libopus Decoder -> 16kHz 16-bit PCM
                    -> I2S DMA Buffer -> ES8311 Codec -> 1W Speaker
```

---

## V. Flash Partitioning & Safe Recovery

### 1. Flash Partition Table (8MB Layout)

```text
# Name,   Type, SubType, Offset,   Size,     Flags
nvs,      data, nvs,     0x9000,   0x6000,
phy_init, data, phy,     0xf000,   0x1000,
factory,  app,  factory, 0x10000,  0x300000,
storage,  data, fat,     0x310000, 0x4f0000,
```

- `0x0000`: Secondary Bootloader
- `0x8000`: Partition Table
- `0x9000`: NVS Storage (pairing state, preferences)
- `0x10000`: Application Partition (Factory App, ~2.1 MB)

### 2. Flashing Guidelines
To prevent accidental erasure of bootloader or NVS calibration partitions, use the dedicated tool:
```bash
python3 talk2gemi/scripts/flash_candidate.py --port /dev/cu.usbmodem1101 --write
```
This utility validates the USB VID:PID (`303a:1001`), the device ROM MAC, and the partition table header before flashing strictly to `0x10000`. Full chip erases are explicitly prevented.

### 3. Factory Firmware Restoration
To restore the device to its factory state (e.g., original Muse or Trae badge firmware):
```bash
bash talk2gemi/backup/muse/restore_muse.sh /dev/cu.usbmodem1101
```
This writes the pristine 8MB physical binary image back to the hardware.
