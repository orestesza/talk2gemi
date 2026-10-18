# talk2gemi Release Archives

The current candidate release is **1.0 Beta** (`v1.0.0-beta`).

## Current Release Overview

| Version | Release Date | Status & Milestone Highlights | Source Package | SHA-256 Checksum |
| :--- | :--- | :--- | :--- | :--- |
| **[1.0 Beta](1.0-beta/README.md)** | 2026-10-18 | **Current Candidate**: Fixed >30s speech truncation (decoupled playback queue), black snowy owl menu bar icon, standard DMG installer, Muse card backup support; 43 tests passed | [talk2gemi-1.0-beta.zip](talk2gemi-1.0-beta.zip) | [Checksum](talk2gemi-1.0-beta.zip.sha256) |

## Firmware Binary Verification (SHA-256)

The application firmware binary is byte-for-byte identical to the accepted on-device testing build:

- **Firmware File**: `firmware/talk2gemi_v1.0-beta.bin`
- **File Size**: `2,161,712` bytes
- **Flash Offset**: `0x10000`
- **SHA-256**: `446d99097c7c0762fb59639aa2fdc1bc817d9875eb978630585eed049cb39a75`

## Verification Instructions

To verify all files within the release subdirectory:
```bash
shasum -a 256 -c SHA256SUMS
```

To verify the integrity of the release ZIP archive:
```bash
shasum -a 256 -c talk2gemi-1.0-beta.zip.sha256
```
