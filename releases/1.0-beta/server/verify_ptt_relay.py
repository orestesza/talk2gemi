"""Exercise a separate relay instance with non-private synthesized audio."""
import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp


async def run(url, pcm_path, report_path):
    reports = []
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url) as ws:
            while True:
                msg = await ws.receive(timeout=15)
                if msg.type == aiohttp.WSMsgType.TEXT and msg.json().get('value') == 'ready':
                    break
            for mode in ['audio', 'audio', 'text', 'audio']:
                queue = asyncio.Queue()

                async def receive():
                    async for msg in ws:
                        await queue.put(msg)

                receiver = asyncio.create_task(receive())
                report = dict(mode=mode, input='', output='', audio_bytes=0, completed=False)
                try:
                    if mode == 'audio':
                        pcm = pcm_path.read_bytes()
                        await ws.send_json({'type': 'start'})
                        for i in range(0, len(pcm), 1024):
                            await ws.send_bytes(pcm[i:i+1024])
                            await asyncio.sleep(len(pcm[i:i+1024])/32000)
                        started = time.monotonic()
                        await ws.send_json({'type': 'stop'})
                    else:
                        started = time.monotonic()
                        await ws.send_json({'type': 'text', 'text': '请只回答，文字测试成功。'})
                    async with asyncio.timeout(20):
                        while True:
                            msg = await queue.get()
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                report.setdefault('first_audio_ms', round((time.monotonic()-started)*1000))
                                report['audio_bytes'] += len(msg.data)
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.json()
                                if data.get('type') == 'transcript':
                                    role = 'input' if data['role'] == 'user' else 'output'
                                    report[role] += data.get('text', '')
                                elif data.get('type') == 'state' and data.get('value') == 'ready':
                                    report['completed'] = True
                                    break
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                reports.append(report)
                report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
                print(json.dumps(report, ensure_ascii=False), flush=True)
                assert report['completed'] and report['audio_bytes'] > 0, report
                if mode == 'audio':
                    assert report['input'], 'No input transcription'
                await asyncio.sleep(.5)
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    parser.add_argument('--pcm', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.pcm, args.report))
