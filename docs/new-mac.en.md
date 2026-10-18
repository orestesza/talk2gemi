# Using talk2gemi on an Apple Silicon Mac

Applies to Apple Silicon (M-series), macOS 14 or later.

## First Launch

1. Open `Talk2Gemi.dmg` and drag `Talk2Gemi` into your `/Applications` folder (or unzip `talk2gemi-mac-arm64.zip`).
2. Launch the application. A black **snowy owl icon** appears in the menu bar; the backend relay starts automatically in the background. No Python or ESP-IDF toolchain is required.
3. Grant Bluetooth permissions when prompted. Click the snowy owl icon → **Gemini API Key…**, paste your key, and save it to the system Keychain.
4. Click **Open control panel** to view relay status, test cloud connectivity, and select your preferred voice or volume.

This app is signed locally and is not notarized by Apple. If macOS warns about an unidentified developer on first launch, open **System Settings** → **Privacy & Security** and click **Open Anyway**.

## Rebinding Badge to a New Mac

**BLE Direct Connection:**
1. On the badge, long-press the side `DOWN` button to open `Settings` → `Connection` → `Bluetooth` → `Change Mac`.
2. Select your new Mac and press `OK`. The first connection requires two distinct verification steps:
   - **Step 1**: The device displays a **System code**. Verify it against the macOS system Bluetooth pairing alert. If identical, confirm on macOS and press `OK` on the device.
   - **Step 2**: The device displays a **Mac link code**. Verify it against the Talk2Gemi **Pair talk2gemi** window, and click **Confirm matching code**.
3. Once paired, subsequent connections reconnect automatically.

**Switching Connection Preference:**
- In device `Settings` → `Connection`, ensure **Prefer** is set to **BLE**, then select **Reconnect**.

## Storage of API Keys and Preferences

- Your API Key is stored securely in the macOS Keychain under **talk2gemi Gemini API Key**.
- The app retrieves the key locally at runtime to authenticate with Google Gemini. The key is never sent to or stored on the badge.
- Packages, firmwares, and repositories contain zero pre-configured keys, pairing credentials, or user recordings.
- Runtime logs and application support files reside in `~/Library/Application Support/talk2gemi/`.
