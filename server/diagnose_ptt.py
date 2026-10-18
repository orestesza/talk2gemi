"""Compare cloud turn boundaries using the same saved device PCM; no device takeover."""
import argparse
import asyncio
import audioop
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / '.env')


async def probe(mode, files):
    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    config = types.LiveConnectConfig(
        response_modalities=['AUDIO'],
        input_audio_transcription={}, output_audio_transcription={},
        system_instruction='请用一句简短的中文回答用户的问题。',
        **({'realtime_input_config': {'automatic_activity_detection': {'disabled': True}}}
           if mode == 'manual' else {}),
    )
    report = []
    async with client.aio.live.connect(model=os.getenv('GEMINI_LIVE_MODEL', 'gemini-3.8-live'), config=config) as session:
        queue = asyncio.Queue()

        async def receive():
            try:
                while True:
                    async for msg in session.receive():
                        await queue.put(msg)
            except Exception as e:
                await queue.put(e)

        task = asyncio.create_task(receive())
        try:
            for path in files:
                pcm = Path(path).read_bytes()
                result = dict(mode=mode, file=str(path), bytes=len(pcm),
                              seconds=len(pcm)/32000, rms=audioop.rms(pcm, 2),
                              peak=audioop.max(pcm, 2), dc=audioop.avg(pcm, 2),
                              input='', output='', audio_bytes=0, completed=False)
                if mode == 'manual':
                    await session.send_realtime_input(activity_start=types.ActivityStart())
                for i in range(0, len(pcm), 2048):
                    await session.send_realtime_input(audio=types.Blob(data=pcm[i:i+2048], mime_type='audio/pcm;rate=16000'))
                    await asyncio.sleep(len(pcm[i:i+2048])/32000)
                started = time.monotonic()
                if mode == 'manual':
                    await session.send_realtime_input(activity_end=types.ActivityEnd())
                else:
                    for _ in range(3):
                        await session.send_realtime_input(audio=types.Blob(data=b'\0'*3200, mime_type='audio/pcm;rate=16000'))
                        await asyncio.sleep(.01)
                try:
                    async with asyncio.timeout(15):
                        while True:
                            msg = await queue.get()
                            if isinstance(msg, Exception):
                                raise msg
                            sc = msg.server_content
                            if not sc:
                                continue
                            if sc.input_transcription:
                                result['input'] += sc.input_transcription.text or ''
                            if sc.output_transcription:
                                result['output'] += sc.output_transcription.text or ''
                            for part in (sc.model_turn.parts if sc.model_turn else []):
                                if part.inline_data and part.inline_data.data:
                                    result.setdefault('first_audio_ms', round((time.monotonic()-started)*1000))
                                    result['audio_bytes'] += len(part.inline_data.data)
                            if sc.interrupted:
                                result['interrupted'] = True
                            if sc.turn_complete:
                                result['completed'] = True
                                break
                except Exception as e:
                    result['error'] = type(e).__name__ + ': ' + str(e)
                report.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    await client.aio.aclose()
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['auto300', 'manual'])
    p.add_argument('files', nargs='+')
    p.add_argument('--report', required=True)
    args = p.parse_args()
    result = asyncio.run(probe(args.mode, args.files))
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2))
