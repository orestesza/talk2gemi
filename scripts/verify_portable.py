#!/usr/bin/env python3
"""Exercise the packaged interpreter in a new path and two clean Mac states."""
import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from aiohttp import ClientSession
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'dist/talk2gemi-mac-arm64.zip'
TEST=ROOT/'scratch/migration_simulation'/hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()[:12]
PORT=8877
async def run_case(resources,state,send_test_key=False):
    state.mkdir(parents=True,exist_ok=True)
    log=state/'test-service.log'
    env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(resources/'packages'),'PYTHONNOUSERSITE':'1',
         'TALK2GEMI_STATE_DIR':str(state),'SERVER_PORT':str(PORT),'SERVER_HOST':'127.0.0.1'}
    with log.open('w') as stream:
        process=subprocess.Popen([str(resources/'runtime/bin/python3'),str(resources/'server/relay_server.py')],cwd=TEST,env=env,stdout=stream,stderr=stream)
        try:
            async with ClientSession() as client:
                for _ in range(100):
                    if process.poll() is not None:raise RuntimeError('Portable relay exited: '+log.read_text()[-2000:])
                    try:
                        async with client.get(f'http://127.0.0.1:{PORT}/api/status') as response:status=await response.json()
                        break
                    except Exception:await asyncio.sleep(.1)
                else:raise RuntimeError('Portable relay did not start')
                assert not status['credentials']['configured']
                assert status['settings']['voice']=='Sulafat'
                if send_test_key:
                    key='test-only-not-a-real-google-key-827491'
                    token=(state/'bridge.token').read_text().strip()
                    async with client.ws_connect(f'http://127.0.0.1:{PORT}/ws/ble-bridge',headers={'Authorization':'Bearer '+token}) as ws:
                        assert (await ws.receive_json())['event']=='identity'
                        await ws.send_json({'event':'credentials','key':key})
                        assert (await ws.receive_json())['event']=='credentials_saved'
                        async with client.get(f'http://127.0.0.1:{PORT}/api/status') as response:public=await response.json()
                        assert public['credentials']['configured']
                        assert key not in json.dumps(public)
                result={'host_id':status['settings']['host_id'],'voice':status['settings']['voice'],
                        'key_initially_missing':True,'runtime':str(resources/'runtime/bin/python3')}
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    for path in state.rglob('*'):
        if path.is_file():assert b'test-only-not-a-real-google-key-827491' not in path.read_bytes(),str(path)
    return result
async def main():
    TEST.mkdir(parents=True,exist_ok=True)
    target=TEST/'Different Mac Folder'
    if not (target/'talk2gemi-mac-arm64').exists():
        subprocess.run(['ditto','-x','-k',str(ARCHIVE),str(target)],check=True)
    resources=target/'talk2gemi-mac-arm64/talk2gemi.app/Contents/Resources'
    a=await run_case(resources,TEST/'Mac A',True)
    again=await run_case(resources,TEST/'Mac A')
    b=await run_case(resources,TEST/'Mac B')
    assert a['host_id']==again['host_id'] and a['host_id']!=b['host_id']
    report={'relocated_runtime':True,'same_mac_identity_persists':True,'new_mac_identity_differs':True,
            'key_not_persisted_by_relay':True,'credentials_ipc_and_public_redaction':True,
            'hardware_scope':'same Apple-silicon Mac, fresh directories; separate physical Mac not yet tested'}
    (TEST/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':asyncio.run(main())
