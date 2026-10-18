#!/usr/bin/env python3
"""Explicit app-only deployment. Preserves partition table, NVS and 1.0 archive."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from serial.tools import list_ports
ROOT=Path(__file__).resolve().parents[1]
EXPECTED='4c:11:ae:2f:ba:2c'
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--port',required=True)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    image=args.image.resolve(); data=image.read_bytes()
    if not str(image).startswith(str(ROOT)+'/') or not 0<len(data)<=0x400000:
        raise SystemExit('Invalid project application image')
    print('Application:',image.name,'bytes:',len(data),'sha256:',hashlib.sha256(data).hexdigest(),flush=True)
    ports=[p for p in list_ports.comports() if p.device==args.port and p.vid==0x303a and p.pid==0x1001 and (p.serial_number or '').lower()==EXPECTED]
    if len(ports)!=1: raise SystemExit('USB identity does not match the authorized AI Passport')
    if not args.write: print('Verification only; no device opened.');return
    import sys
    base=[sys.executable,'-m','esptool','--chip','esp32c3','--port',args.port]
    identity=subprocess.check_output(base+['--after','hard_reset','read_mac'],text=True)
    if EXPECTED not in identity.lower(): raise SystemExit('ROM MAC mismatch')
    with tempfile.TemporaryDirectory(dir=ROOT/'scratch') as temp:
        partition=Path(temp)/'partition.bin'
        subprocess.run(base+['--after','hard_reset','read_flash','0x8000','0xc00',str(partition)],check=True)
        partition_ref = ROOT/'releases/0.5/firmware/partition-table.bin'
        if not partition_ref.exists():
            partition_ref = ROOT/'releases/1.0/firmware/partition-table.bin'
        expected = partition_ref.read_bytes()[:0xc00]
        if partition.read_bytes()!=expected:raise SystemExit('Partition layout changed; nothing written')
    subprocess.run(base+['-b','460800','--before','default_reset','--after','hard_reset','write_flash','--flash_mode','dio','--flash_size','8MB','--flash_freq','80m','0x10000',str(image)],check=True)
if __name__=='__main__': main()
