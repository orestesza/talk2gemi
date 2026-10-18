#!/usr/bin/env python3
"""Create the self-contained development ZIP from an explicit project allowlist."""
import hashlib
import json
import os
import stat
import time
import unicodedata
import zipfile
from pathlib import Path
from dotenv import dotenv_values

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
OUT = PROJECT / 'dist/talk2gemi-development-handoff-20261016.zip'
PREFIX = 'talk2gemi-workspace/'
TREES = [
    'ai-passport', 'passport_lab',
    '.toolchains/esp-idf-v5.5.3', '.toolchains/python', '.toolchains/espressif/tools', '.toolchains/component-cache',
    '.toolchains/espressif/python_env/idf5.5_py3.11_env/lib/python3.11/site-packages',
    'talk2gemi/.venv/lib/python3.11/site-packages',
    'talk2gemi/server', 'talk2gemi/scripts', 'talk2gemi/docs', 'talk2gemi/migration',
    'talk2gemi/assets', 'talk2gemi/firmware/main', 'talk2gemi/firmware/tests',
    'talk2gemi/firmware/managed_components', 'talk2gemi/mac/build/talk2gemi.app',
    'talk2gemi/releases', 'talk2gemi/backup', 'talk2gemi/candidates/2.0-dev-ble-gatt',
    'talk2gemi/snapshots/20261016-paused',
]
FILES = [
    'talk2gemi/README.md','talk2gemi/Start-New-Mac.command',
    'talk2gemi/firmware/CMakeLists.txt','talk2gemi/firmware/dependencies.lock',
    'talk2gemi/firmware/partitions.csv','talk2gemi/firmware/sdkconfig','talk2gemi/firmware/sdkconfig.defaults',
    'talk2gemi/mac/Talk2Gemi.swift','talk2gemi/mac/Keychain.swift','talk2gemi/mac/main.swift','talk2gemi/mac/Info.plist',
    '.toolchains/espressif/espidf.constraints.v5.5.txt',
    'talk2gemi/scratch/repair_20261015/synthetic_question.pcm',
    'talk2gemi/scratch/v2_implementation/device_rounds.py',
    'talk2gemi/scratch/v2_implementation/usb_harness.py',
    'talk2gemi/scratch/v2_implementation/rounds-ble.json',
    'talk2gemi/scratch/v2_implementation/rounds-ble.log',
    'talk2gemi/scratch/v2_implementation/ble-numeric-probe.log',
    'talk2gemi/scratch/v2_implementation/macos-ble-drop-context.log',
    'talk2gemi/scratch/v2_implementation/host-tests-ble-auth.log',
    'talk2gemi/scratch/v2_implementation/physical-acceptance.json',
]
SKIP = {'__pycache__','.DS_Store','.env','.runtime','.migration-envs','build-new-mac'}

def walk(folder):
    for item in sorted(folder.iterdir()):
        if item.name in SKIP: continue
        if item.is_symlink() or item.is_file(): yield item
        elif item.is_dir():
            children = list(walk(item))
            if children: yield from children
            else: yield item

def main():
    paths = {}
    for tree in TREES:
        for path in walk(ROOT/tree): paths[str(path.relative_to(ROOT))] = path
    for name in FILES:
        if not (ROOT/name).is_file(): raise SystemExit('Missing required input: '+name)
        paths[name] = ROOT/name
    paths['README.md'] = PROJECT/'migration/ROOT_README.md'
    paths['README.en.md'] = PROJECT/'migration/ROOT_README.en.md'
    secret = (dotenv_values(PROJECT/'.env').get('GEMINI_API_KEY') or '').encode()
    manifest = {'schema':1,'target':'Apple-silicon macOS 13+','scope':'talk2gemi development handoff only',
                'excluded':['talk2mira','ESP8266 project','API key','old Mac pairing state','recorded user speech'],
                'installed_firmware_sha256':'1c19e5bbef9b933d07e48148af0bf2b6c7cfc79eaf8856e75d8a9008421acccc',
                'status':'BLE audio unusably choppy; not a 2.0 acceptance release','files':{}}
    OUT.parent.mkdir(exist_ok=True)
    normalized=set();started=time.monotonic()
    with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED,compresslevel=4,allowZip64=True) as archive:
        for index,(name,path) in enumerate(sorted(paths.items())):
            normalized_name=unicodedata.normalize('NFC',name)
            if normalized_name in normalized: raise SystemExit('Unicode path collision: '+name)
            normalized.add(normalized_name)
            if name.startswith('talk2mira/'): raise SystemExit('Protected scope violation')
            entry_name = PREFIX+name+('/' if path.is_dir() and not path.is_symlink() else '')
            info=zipfile.ZipInfo(entry_name,time.localtime(path.lstat().st_mtime)[:6]);info.create_system=3
            info.external_attr=path.lstat().st_mode<<16;info.compress_type=zipfile.ZIP_DEFLATED
            if path.is_dir() and not path.is_symlink():
                archive.writestr(info,b'')
                manifest['files'][name]={'type':'directory','mode':stat.S_IMODE(path.stat().st_mode)}
            elif path.is_symlink():
                target=os.readlink(path)
                if os.path.isabs(target):
                    if not path.resolve().is_relative_to(ROOT): raise SystemExit('External symlink: '+name)
                    target=os.path.relpath(path.resolve(),path.parent)
                if not (path.parent/target).resolve().is_relative_to(ROOT): raise SystemExit('External symlink: '+name)
                archive.writestr(info,target.encode())
                manifest['files'][name]={'type':'symlink','target':target}
            else:
                digest=hashlib.sha256();tail=b'';count=0
                with path.open('rb') as source,archive.open(info,'w',force_zip64=True) as destination:
                    for chunk in iter(lambda:source.read(1024*1024),b''):
                        if secret and secret in tail+chunk: raise SystemExit('API key detected in '+name)
                        tail=chunk[-len(secret):] if secret else b''
                        digest.update(chunk);destination.write(chunk);count+=len(chunk)
                manifest['files'][name]={'type':'file','bytes':count,'mode':stat.S_IMODE(path.stat().st_mode),'sha256':digest.hexdigest()}
            if index%5000==0: print(f'Packed {index}/{len(paths)} entries',flush=True)
        archive.writestr(PREFIX+'HANDOFF-MANIFEST.json',json.dumps(manifest,indent=2))
    digest=hashlib.sha256()
    with OUT.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    report={'archive':str(OUT),'bytes':OUT.stat().st_size,'sha256':digest.hexdigest(),'entries':len(paths),'seconds':round(time.monotonic()-started,1)}
    (PROJECT/'dist/development-handoff-report.json').write_text(json.dumps(report,indent=2))
    OUT.with_suffix('.zip.sha256').write_text(digest.hexdigest()+'  '+OUT.name+'\n')
    print(json.dumps(report),flush=True)

if __name__=='__main__':main()
