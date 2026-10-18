#!/usr/bin/env python3
"""
针对真实语音 PCM 测定 Gemini 3.8 Live API 语音输入到语音回复的时延：
1. 模拟用户按住 PTT 讲话推流 (1x 真实采样率实时推流)
2. 模拟用户松开 PTT (发送 audio_stream_end=True)
3. 毫秒级测量：
   - 用户输入语音识别耗时 (User STT Latency)
   - 首次文本字幕返回耗时 (TTFT Text)
   - 首次语音音频块返回耗时 (TTFT Audio - 首声延迟)
   - 完整语音生成结束总耗时 (Turn Duration)
"""

import asyncio
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")

TEST_CASES = [
    {
        "name": "第 1 轮语音测试",
        "pcm": BASE_DIR / "scratch" / "q_weekday.pcm",
        "expected": "你好，请问今天星期几？"
    },
    {
        "name": "第 2 轮语音测试",
        "pcm": BASE_DIR / "scratch" / "q_time.pcm",
        "expected": "请问现在几点了？"
    }
]

async def measure():
    if not API_KEY:
        print("❌ GEMINI_API_KEY not found in .env")
        return

    print("=" * 65)
    print(f"🎙️ 正在测定 Gemini Live ({MODEL}) 真实【语音进 $\\to$ 语音出】时延...")
    print("=" * 65)

    client = genai.Client(api_key=API_KEY)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(
            parts=[types.Part(text="你是一个语音助手。请用简短一句话回答。")]
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        )
    )

    t_conn_start = time.time()
    async with client.aio.live.connect(model=MODEL, config=config) as session:
        conn_time = (time.time() - t_conn_start) * 1000
        print(f"📡 WebSocket 长连接建立完成: {conn_time:.1f} ms\n")

        # 启动持久后台队列收集
        queue = asyncio.Queue()

        async def rx():
            try:
                while True:
                    async for msg in session.receive():
                        await queue.put(msg)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                await queue.put(("ERROR", e))

        rx_task = asyncio.create_task(rx())
        await asyncio.sleep(0.3)

        report = []

        for tc in TEST_CASES:
            print(f"▶️ [{tc['name']}] 载入音频: {tc['pcm'].name} (期望语音: '{tc['expected']}')")
            pcm_data = tc["pcm"].read_bytes()
            duration_sec = len(pcm_data) / 32000.0
            print(f"   音频大小: {len(pcm_data)} 字节, 录音时长: {duration_sec:.2f} 秒")

            # 模拟用户按住 PTT 讲话：按 16kHz 16bit mono (每 1024 字节 = 32ms) 实时推流
            print("   🔴 模拟用户按住 PTT 开始讲话，实时流式推送音频...")
            t_speech_start = time.time()
            chunk_size = 1024
            for i in range(0, len(pcm_data), chunk_size):
                chunk = pcm_data[i : i + chunk_size]
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
                await asyncio.sleep(0.030) # 模拟真实说话节奏

            t_speech_end = time.time()
            print(f"   🛑 模拟用户讲完松开 PTT: 下发 audio_stream_end=True (推流耗时 {(t_speech_end - t_speech_start):.2f}s)")
            await session.send_realtime_input(audio_stream_end=True)

            # 开始严密测定返回时延 (以松开按键瞬间为基准 T0)
            t0 = time.time()
            t_user_stt = None
            t_first_text = None
            t_first_audio = None
            t_turn_complete = None
            user_stt_text = ""
            model_text = ""
            audio_bytes_total = 0

            timeout = 15.0
            while time.time() - t0 < timeout:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                    now = time.time()

                    if isinstance(msg, tuple) and msg[0] == "ERROR":
                        print(f"   ❌ 接收异常: {msg[1]}")
                        break

                    sc = msg.server_content
                    if not sc:
                        continue

                    # 用户语音识别事件
                    if sc.input_transcription and sc.input_transcription.text:
                        if t_user_stt is None:
                            t_user_stt = now
                        user_stt_text += sc.input_transcription.text

                    # 模型字幕返回事件
                    if sc.output_transcription and sc.output_transcription.text:
                        if t_first_text is None:
                            t_first_text = now
                        model_text += sc.output_transcription.text

                    # 模型音频流返回事件
                    if sc.model_turn and sc.model_turn.parts:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                if t_first_audio is None:
                                    t_first_audio = now
                                audio_bytes_total += len(part.inline_data.data)

                    # 轮次完成
                    if sc.turn_complete:
                        t_turn_complete = now
                        break

                    if sc.interrupted:
                        print("   ⚠️ 检测到被打断信号 interrupted=True")
                        break

                except asyncio.TimeoutError:
                    if t_first_audio and (time.time() - t_first_audio > 3.0):
                        t_turn_complete = time.time()
                        break

            # 统计
            stt_latency = (t_user_stt - t0) * 1000 if t_user_stt else None
            text_latency = (t_first_text - t0) * 1000 if t_first_text else None
            audio_ttft = (t_first_audio - t0) * 1000 if t_first_audio else None
            total_duration = (t_turn_complete - t0) * 1000 if t_turn_complete else None

            entry = {
                "name": tc["name"],
                "user_stt": user_stt_text.strip(),
                "model_text": model_text.strip(),
                "stt_ms": stt_latency,
                "text_ttft_ms": text_latency,
                "audio_ttft_ms": audio_ttft,
                "total_ms": total_duration,
                "audio_bytes": audio_bytes_total
            }
            report.append(entry)

            print(f"   🗣️ 用户语音识别内容 : \"{user_stt_text.strip()}\" (耗时: {stt_latency:.1f} ms)" if stt_latency else "   ⚠️ 未收到用户语音识别")
            print(f"   📝 模型回复首字字幕 : \"{model_text.strip()}\" (首字延迟: {text_latency:.1f} ms)" if text_latency else "   ⚠️ 未收到字幕")
            print(f"   ⚡ 【首包语音返回延迟】: {audio_ttft:.1f} ms ({audio_ttft/1000:.2f} 秒听到第一声)" if audio_ttft else "   ⚠️ 未收到语音")
            print(f"   ⏱️ 完整语音播发总耗时 : {total_duration:.1f} ms ({total_duration/1000:.2f} 秒)" if total_duration else "   ⚠️ 轮次未完成")
            print()

            await asyncio.sleep(1.5) # 轮次间隔

        rx_task.cancel()

        # 汇总表
        print("=" * 65)
        print("📊 真实语音输入端到端耗时实测结果汇总：")
        print("=" * 65)
        for r in report:
            print(f"【{r['name']}】")
            print(f"  - 用户识别: \"{r['user_stt']}\"")
            print(f"  - 模型回答: \"{r['model_text']}\"")
            print(f"  - 听到回复首声延时 (TTFT): {r['audio_ttft_ms']:.1f} ms ({r['audio_ttft_ms']/1000:.2f} 秒)")
            print(f"  - 完整回答语音总时长    : {r['total_ms']:.1f} ms ({r['total_ms']/1000:.2f} 秒)")
        print("=" * 65)

if __name__ == "__main__":
    asyncio.run(measure())
