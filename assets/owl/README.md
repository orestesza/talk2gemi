# Snowy owl assets

Approved character: `approved-concept.png`. Built-in imagegen generated the reference and six-state atlas. Final generation prompt: preserve the approved round icy-blue snowy owl in an aligned 3x2 grid, idle/blink/listening/thinking/speaking/sleeping, simplified pixel clusters and limited flat colors, transparent outside silhouettes. `atlas-alpha-attempt.png` has meaningful alpha (corners alpha=0); the dark preview outside sprites is not opaque background. Original drafts retained for traceability.

`convert_owl_assets.py` performs deterministic format conversion: binary alpha threshold, common palette, 80x80 nearest-neighbor source frames, then pre-expands 2x to 160x160 display frames. Current common palette has 13 colors. Assets are opaque RGB565 against the exact UI fill. The RGB565 C arrays reside in Flash (307200 bytes for six frames), deliberately avoiding indexed-color decode buffers and runtime scaling. The six `frame-N.png` files and `frames-preview.png` are the actual 80x80 art for inspection.

Reproduce with a Python runtime containing Pillow:

```
python3 talk2gemi/scripts/convert_owl_assets.py
```

Do not install image tooling into the shared firmware Python environment. The conversion used the Codex bundled image runtime. Firmware itself needs no Pillow or image codecs. `firmware/main/owl_assets.c` is generated; keep its header/data geometry consistent. A full 160x160 framebuffer is not allocated in RAM.

The minimal animation reuses six poses with blinking/position-only breathing, listening/thinking loops and mouth open/closed cycling. It is not a 24-fps motion atlas or audio-amplitude lipsync.
