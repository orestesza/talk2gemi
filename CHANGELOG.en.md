# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0-beta] - 2026-10-18

### Added
- **Physical Push-To-Talk (PTT) Voice Pipeline**:
  - Built for FoloToy AI Passport (ESP32-C3 + ES8311 + ST7789 + Front OK button).
  - Implements Google's official Manual Activity boundaries (`activity_start` on press, 16kHz mono PCM with IMA ADPCM compression, `activity_end` on release).
- **Pure BLE GATT Bidirectional Transport**:
  - Replaces device Wi-Fi dependency with Bluetooth Low Energy (GATT) connection to Mac companion.
  - Custom framing protocol: `kind=1` control frames, `kind=4` independent uplink ADPCM voice chunks, `kind=2` downlink audio, `kind=3` packet ACKs.
  - 3-frame bounded sliding window with hardware playback queue backpressure and up to 80ms initial prebuffering to eliminate stutter.
- **Decoupled Long Speech Playback Queue**:
  - Implemented `self.playback_queue` and an asynchronous `_playback_worker` in `server/relay_server.py`, fully decoupling cloud WebSocket reception from BLE hardware playback rate.
  - Empties cloud audio and captions within milliseconds, preventing WebSocket 1006 abnormal closures.
  - Guarded `_gemini_keepalive_loop` with `not self.busy`, preventing keepalive checks from cancelling active `receive_task` while draining speech.
  - Verified on hardware: Bai Juyi's poem *The Charcoal Seller* recited continuously for 36.5 seconds (47 audio chunks, 1,168,320 PCM bytes) to full completion (`status: completed`).
- **High-Fidelity Opus Downlink Audio**:
  - Integrated `libopus` (16kHz 20ms VBR, ~24kbps) to significantly reduce BLE bandwidth while delivering natural voice reproduction.
- **Ice-Blue Snowy Owl Visual Interface**:
  - Redesigned 1.54" ST7789 display (240×320) with clean English layout and ice-blue/cool-gray color theme.
  - 6 animated system states: Ready, Listening, Thinking, Speaking, Resting, Error.
  - Top status bar displays battery gauge, BLE connection status, scrolling Mac hostname, and network latency.
- **macOS Companion App & Standard Installer**:
  - Native Swift menu bar companion with a black snowy owl template icon, automatically adapting to macOS Light and Dark appearance.
  - Packaged as a standard `dist/Talk2Gemi.dmg` installer (94MB UDZO image with an `/Applications` drag-and-drop symlink) and portable zip `dist/talk2gemi-mac-arm64.zip`.
  - Secure API key storage in the macOS Keychain under a persistent self-signed code identity.
- **Local Conversation History & Web Dashboard**:
  - Lightweight SQLite persistence recording speech audio, recognized transcripts, and assistant responses; supports context recovery after disconnection.
  - Web dashboard at `http://localhost:8765` for real-time bilingual transcripts and telemetry.
- **Voice Switching Resilience**:
  - Clears stale session resumption handles when changing voices, switching to local text history for context reconstruction.
  - Reconnects cloud session asynchronously in the background and decouples BLE packet ACKs from cloud reconnection latency.
- **Multi-Card Backup & Device Tooling**:
  - Added `scripts/backup_card.py` to auto-detect card serial ports, read ROM MAC, dump 8MB flash images, and parse partition tables.
  - Verified factory backup and restoration script `backup/muse/restore_muse.sh` on hardware.
- **Automated Test Suite**:
  - Host Python test suite of 43 unit tests, 100% passing.

### Security
- Audited distribution packages to verify zero embedded API keys or credentials. Keys remain strictly within the user's macOS Keychain.
