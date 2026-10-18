#!/usr/bin/env python3
"""Recreate project-local Python entry points; never alter shared environments."""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import venv
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
RUNTIME = WORKSPACE / '.toolchains/python/cpython-3.11.17-macos-aarch64-none'
ENVS = PROJECT / '.migration-envs'
PACKAGES = {
    'relay': PROJECT / '.venv/lib/python3.11/site-packages',
    'idf': WORKSPACE / '.toolchains/espressif/python_env/idf5.5_py3.11_env/lib/python3.11/site-packages',
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('This bundle requires an Apple-silicon Mac.')
    if sys.version_info[:3] != (3, 11, 17) or Path(sys.base_prefix).resolve() != RUNTIME.resolve():
        raise SystemExit('Run this script using the bundled standalone Python 3.11.17.')
    for name, packages in PACKAGES.items():
        if not packages.is_dir():
            raise SystemExit(f'Missing copied dependency folder: {packages}')
        target = ENVS / name
        identity = {'workspace': str(WORKSPACE), 'runtime': str(RUNTIME), 'packages': str(packages)}
        marker = target / 'migration.json'
        current = json.loads(marker.read_text()) if marker.exists() else None
        if current != identity or not (target / 'bin/python').exists():
            if not args.prepare:
                raise SystemExit('Local environment needs setup; rerun with --prepare.')
            target.mkdir(parents=True, exist_ok=True)
            for exe in ('python', 'python3', 'python3.11'):
                path = target / 'bin' / exe
                if path.is_symlink() or path.exists(): path.unlink()
            venv.EnvBuilder(with_pip=False, symlinks=True).create(target)
            site = target / 'lib/python3.11/site-packages'
            (site / 'copied-packages.pth').write_text(str(packages) + '\n')
            if name == 'idf': (target / 'idf_version.txt').write_text('5.5\n')
            marker.write_text(json.dumps(identity, indent=2))
        code = ('import aiohttp,google.genai,credentials' if name == 'relay'
                else 'import serial,esptool,idf_component_manager')
        env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONPATH=str(PROJECT / 'server'))
        subprocess.run([str(target / 'bin/python'), '-c', code], env=env, check=True)
    # A fresh SDK location has no target record. Keep this state project-local,
    # otherwise export.sh requests toolchains for every ESP chip, not just C3.
    tools_state = ENVS / 'espressif'
    tools_state.mkdir(exist_ok=True)
    for name in ('tools', 'espidf.constraints.v5.5.txt'):
        link = tools_state / name
        destination = WORKSPACE / '.toolchains/espressif' / name
        if link.is_symlink() and link.resolve() != destination.resolve(): link.unlink()
        if not link.exists(): link.symlink_to(destination)
    sdk = str(WORKSPACE / '.toolchains/esp-idf-v5.5.3')
    record = {'idfInstalled': {sdk+'-v5.5': {
        'version': '5.5', 'path': sdk, 'features': ['core'], 'targets': ['esp32c3']}}}
    (tools_state / 'idf-env.json').write_text(json.dumps(record, indent=2))
    # Component-manager lockfiles contain local absolute BSP dependency paths.
    # Rebase just these project-owned references, preserving every version/hash.
    dependency_lock = PROJECT / 'firmware/dependencies.lock'
    original = dependency_lock.read_text()
    def rebase(match):
        value = match.group(2).strip().strip('\"\'')
        marker = '/ai-passport/managed_components/'
        if marker not in value: return match.group(0)
        suffix = value.split(marker,1)[1]
        target = WORKSPACE / 'ai-passport/managed_components' / suffix
        if not target.is_dir(): raise SystemExit('Missing BSP dependency: '+str(target))
        return match.group(1) + json.dumps(str(target))
    rewritten = re.sub(r'(?m)^(\s+path:\s+)([^\n]+)$', rebase, original)
    if rewritten != original:
        if not args.prepare: raise SystemExit('Project dependency lock needs path rebasing.')
        dependency_lock.write_text(rewritten)
    (PROJECT / 'scratch').mkdir(exist_ok=True)
    print('Project-local relay and ESP-IDF Python environments ready; no device opened.')

if __name__ == '__main__': main()
