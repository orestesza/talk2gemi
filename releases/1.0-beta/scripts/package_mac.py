#!/usr/bin/env python3
"""Build a portable Apple-silicon app from a strict allowlist; no user state."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "talk2gemi-mac-arm64"


def package():
    subprocess.run(["bash", str(ROOT/"scripts/build_companion.sh")], check=True)
    app = OUTPUT / "talk2gemi.app"
    if app.exists():
        shutil.rmtree(app)  # Only this script's deterministic generated output.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT/"mac/build/talk2gemi.app", app)
    resources = app / "Contents/Resources"
    resources.mkdir(parents=True, exist_ok=True)
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    # Do not copy the project, .venv, .env, runtime, logs, recordings or releases.
    for name in ("relay_server.py", "settings.py", "credentials.py", "ble_bridge.py", "ble_protocol.py", "conversations.py", "opus_downlink.py"):
        dest = resources / "server" / name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT/"server"/name, dest)
    shutil.copytree(ROOT/"server/static", resources/"server/static", ignore=ignored)
    (resources/"assets").mkdir()
    shutil.copy2(ROOT/"assets/timeout_16k.pcm", resources/"assets/timeout_16k.pcm")
    shutil.copytree(ROOT/"vendor/opus-mac",resources/"vendor/opus-mac",symlinks=True)
    # The existing standalone CPython is relocatable, unlike a copied venv.
    runtime = Path(sys.base_prefix)
    if runtime.name != "cpython-3.11.17-macos-aarch64-none":
        raise SystemExit("Run with the project Python 3.11.17 standalone runtime")
    shutil.copytree(runtime, resources/"runtime", symlinks=True, ignore=ignored)
    packages = ROOT/".venv/lib/python3.11/site-packages"
    shutil.copytree(packages, resources/"packages", symlinks=True, ignore=ignored)
    # All symlinks must resolve inside the deliverable (no source-machine paths).
    for path in app.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(app.resolve()):
            raise SystemExit("Nonportable symlink: " + str(path.relative_to(app)))
    # Verify a relocated interpreter with a clean process environment.
    env = {"PATH":"/usr/bin:/bin", "PYTHONPATH":str(resources/"packages"), "PYTHONNOUSERSITE":"1"}
    result = subprocess.check_output([str(resources/"runtime/bin/python3"), "-c",
        "import sys,aiohttp,google.genai,audioop,cryptography; print(sys.version.split()[0])"], env=env, text=True)
    # Preserve this Mac's stable identity for nested payloads and the outer app.
    # This local signing identity is not Developer ID or Apple notarization.
    identity = subprocess.check_output([sys.executable,str(ROOT/"scripts/local_signing_identity.py")],text=True).strip()
    macho = []
    for path in app.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f: magic=f.read(4)
            if magic in (b"\xcf\xfa\xed\xfe",b"\xca\xfe\xba\xbe",b"\xfe\xed\xfa\xcf"):
                macho.append(path)
    for path in macho:
        subprocess.run(["codesign","--force","--sign",identity,str(path)], check=True, capture_output=True)
    subprocess.run(["codesign","--force","--sign",identity,str(app)], check=True)
    subprocess.run(["codesign","--verify","--deep","--strict",str(app)], check=True)
    manifest = {"architecture":"arm64", "macos_minimum":"13.0", "python":result.strip(),
                "contains_credentials":False,"requires_firmware_reflash":False,"files":{}}
    for path in sorted(app.rglob("*")):
        if path.is_file() and not path.is_symlink():
            manifest["files"][str(path.relative_to(OUTPUT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (OUTPUT/"MANIFEST.json").write_text(json.dumps(manifest,indent=2))
    shutil.copy2(ROOT/"docs/new-mac.zh_CN.md", OUTPUT/"新Mac使用说明.md")
    archive=ROOT/"dist/talk2gemi-mac-arm64.zip"
    subprocess.run(["ditto","-c","-k","--sequesterRsrc","--keepParent",str(OUTPUT),str(archive)],check=True)

    dmg_staging = ROOT / "dist" / "dmg_staging"
    if dmg_staging.exists():
        shutil.rmtree(dmg_staging)
    dmg_staging.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app, dmg_staging / "talk2gemi.app", symlinks=True)
    os.symlink("/Applications", str(dmg_staging / "Applications"))
    shutil.copy2(ROOT / "docs/new-mac.zh_CN.md", dmg_staging / "新Mac使用说明.md")

    dmg_path = ROOT / "dist" / "Talk2Gemi.dmg"
    if dmg_path.exists():
        dmg_path.unlink()

    subprocess.run([
        "hdiutil", "create",
        "-volname", "Talk2Gemi",
        "-srcfolder", str(dmg_staging),
        "-ov",
        "-format", "UDZO",
        str(dmg_path)
    ], check=True)
    shutil.rmtree(dmg_staging)
    subprocess.run(["codesign", "--force", "--sign", identity, str(dmg_path)], check=True)

    print(json.dumps({
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "dmg": str(dmg_path),
        "dmg_bytes": dmg_path.stat().st_size,
        "dmg_sha256": hashlib.sha256(dmg_path.read_bytes()).hexdigest(),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()
    }))

if __name__=="__main__":package()
