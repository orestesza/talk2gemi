# talk2gemi Development Status & Work Record

## Current Milestone: 1.0 Beta Release Candidate (2026-10-18)

Archived under `releases/1.0-beta/` and `releases/talk2gemi-1.0-beta.zip`. Fully verified on hardware:

1. **Complete Fix for Long Speech Truncation (>30 Seconds)**:
   - Introduced an asynchronous in-memory queue `playback_queue` and an independent `_playback_worker` on the server, completely decoupling cloud WebSocket reception from BLE hardware playback backpressure.
   - `_gemini_receive_loop` drains cloud audio packets and `turn_complete` events within seconds without blocking the socket.
   - Guarded `_gemini_keepalive_loop` with `not self.busy`, preventing keepalive checks from cancelling active playback tasks.
   - Verified on hardware: Bai Juyi's poem *The Charcoal Seller* recited continuously for 36.5 seconds (1,168,320 bytes, 47 audio chunks), with status properly recorded as `completed`.

2. **Mac Menu Bar Snowy Owl Icon**:
   - Status bar item uses a black snowy owl template image (`owl_status.png` 18x18, `owl_status@2x.png` 36x36), `isTemplate = true`, automatically adapting to macOS Light and Dark appearance.

3. **Standard macOS DMG Installer**:
   - Built a standard disk image `dist/Talk2Gemi.dmg` (with an `/Applications` drag-and-drop symlink) and preserved a portable zip `dist/talk2gemi-mac-arm64.zip`.

4. **Pure BLE Bidirectional Transport & Opus Audio**:
   - Uses custom framing with a 3-frame bounded sliding window and playback queue backpressure flow control to eliminate audio stutter.
   - Uses `libopus` at 16kHz 20ms VBR (~24kbps) to maintain natural voice quality while reducing BLE bandwidth requirements.

5. **Ice-Blue Snowy Owl Visual Interface**:
   - 1.54" ST7789 display displays 6 animated system states (Ready, Listening, Thinking, Speaking, Resting, Error), along with battery gauge and scrolling Mac hostname.

6. **Session Persistence & Voice Switching**:
   - Lightweight local SQLite database records transcripts and metadata, allowing seamless context reconstruction after disconnection.
   - Smooth asynchronous cloud reconnection when switching voices with cleanup of residual recording state.

7. **Multi-Card Backup Tooling**:
   - Developed `scripts/backup_card.py` to auto-detect card serial ports and dump 8MB physical flash images (`backup/muse/`).

8. **Test Coverage**:
   - All 43 host unit tests pass cleanly.
