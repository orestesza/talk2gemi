#!/usr/bin/env python3
"""Verify a freshly extracted development handoff before editing it."""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
manifest = root / 'HANDOFF-MANIFEST.json'
if not manifest.exists(): raise SystemExit('Run inside the extracted migration workspace.')
data = json.loads(manifest.read_text())
errors = []
for name, expected in data['files'].items():
    path = root / name
    if expected['type'] == 'directory':
        if not path.is_dir() or path.is_symlink(): errors.append(name + ': missing directory')
        elif stat.S_IMODE(path.stat().st_mode) != expected['mode']: errors.append(name + ': directory permissions differ')
    elif expected['type'] == 'symlink':
        if not path.is_symlink() or os.readlink(path) != expected['target']:
            errors.append(name + ': symlink differs')
        elif not path.resolve().is_relative_to(root):
            errors.append(name + ': symlink escapes workspace')
        elif not path.exists():
            errors.append(name + ': symlink target missing')
    elif not path.is_file() or path.is_symlink(): errors.append(name + ': missing regular file')
    else:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
        if digest.hexdigest() != expected['sha256']: errors.append(name + ': content differs')
        if path.stat().st_size != expected['bytes']: errors.append(name + ': size differs')
        if stat.S_IMODE(path.stat().st_mode) != expected['mode']: errors.append(name + ': permissions differ')
if errors:
    print('\n'.join(errors[:30]));raise SystemExit(f'FAIL: {len(errors)} differences')
print(f"PASS: {len(data['files'])} entries verified; source, binaries, toolchain and empty directories preserved.")
