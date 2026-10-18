#!/usr/bin/env python3
"""
talk2gemi 响应时延与耗时科学测定脚本
针对 gemini-3.8-live 进行多轮真实延迟基准测试：
1. 首个音频块返回延迟 (First Audio Chunk Latency / TTFT)
2. 实时字幕首字返回延迟 (Transcription Latency)
3. 完整语音生成结束耗时 (Turn Complete Duration)
4. 真实 16kHz PCM 语音送入时的端到端响应耗时 (Audio-to-Audio Latency)
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
TEST_PCM = BASE_DIR / "assets" / "timeout_16k.pcm"


async def run_benchmark():
    if not API_KEY:
        print("❌ Error: GEMINI_API_KEY not found in .env")
        return

    print("=" * 65)
    print(f"🔬 开始进行 Gemini Live ({MODEL}) 响应耗时基准评测...")
    print("=" * 65)

    client = genai.Client(api_key=API_KEY)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(
            parts=[
                types.Part(
                    text="你是 talk2gemi 随身硬件助手。请用简短自然口语化中文回答，一至两句话即可。"
                )
            ]
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        ),
    )

    t0 = time.time()
    print("📡 正在建立 Gemini Live WebSocket 长连接...")
    async with client.aio.live.connect(model=MODEL, config=config) as session:
        conn_time = (time.time() - t0) * 1000
        print(f"✅ 长连接建立成功，耗时: {conn_time:.1f} ms\n")

        # 启动接收任务
        queue = asyncio.Queue()

        async def receiver():
            try:
                while True:
                    async for response in session.receive():
                        await queue.put(response)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                await queue.put(("ERROR", e))

        rx_task = asyncio.create_task(receiver())

        # 准备测试集
        test_cases = [
            {"type": "text", "payload": "你好，请确认系统可用"},
            {"type": "text", "payload": "请用简短一句话介绍今天星期几，以及你的名字"},
            {"type": "text", "payload": "如果我想要查天气，你最快能怎么帮我？"},
        ]

        if TEST_PCM.exists():
            test_cases.append({"type": "audio", "payload": TEST_PCM.read_bytes()})

        results = []

        for idx, tc in enumerate(test_cases, 1):
            print(f"--- [测试轮次 {idx}/{len(test_cases)}] 类型: {tc['type']} ---")
            send_time = time.time()
            first_text_time = None
            first_audio_time = None
            first_user_stt_time = None
            turn_complete_time = None
            audio_bytes = 0
            model_text = ""
            user_text = ""

            if tc["type"] == "text":
                print(f"📤 发送文本: \"{tc['payload']}\"")
                await session.send_realtime_input(text=tc["payload"])
            elif tc["type"] == "audio":
                pcm = tc["payload"]
                print(f"📤 模拟推送真实 16kHz PCM 音频流 ({len(pcm)} 字节, 约 {len(pcm)/32000:.1f} 秒)...")
                # 模拟 32ms 分块推送
                chunk_size = 1024
                for i in range(0, len(pcm), chunk_size):
                    chunk = pcm[i:i+chunk_size]
                    await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
                    await asyncio.sleep(0.01) # 快速流式推送
                # 记录音频推流结束时间点 (PTT Release)
                send_time = time.time()
                print("🛑 模拟 PTT 按键松开: 发送 audio_stream_end=True")
                await session.send_realtime_input(audio_stream_end=True)

            # 等待本轮响应
            timeout = 20.0
            start_wait = time.time()

            while time.time() - start_wait < timeout:
                try:
                    resp = await asyncio.wait_for(queue.get(), timeout=1.0)
                    now = time.time()

                    if isinstance(resp, tuple) and resp[0] == "ERROR":
                        print(f"❌ 接收异常: {resp[1]}")
                        break

                    sc = resp.server_content
                    if not sc:
                        continue

                    # 用户输入转录
                    if sc.input_transcription and sc.input_transcription.text:
                        if first_user_stt_time is None:
                            first_user_stt_time = now
                        user_text += sc.input_transcription.text

                    # 模型回复转录
                    if sc.output_transcription and sc.output_transcription.text:
                        if first_text_time is None:
                            first_text_time = now
                        model_text += sc.output_transcription.text

                    # 音频流下行
                    if sc.model_turn and sc.model_turn.parts:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                if first_audio_time is None:
                                    first_audio_time = now
                                audio_bytes += len(part.inline_data.data)

                    # 轮次结束
                    if sc.turn_complete:
                        turn_complete_time = now
                        break

                except asyncio.TimeoutError:
                    if first_audio_time and (time.time() - first_audio_time > 4.0):
                        # 如果已经开始播放且超过 4 秒没有新块，视为播发完成
                        turn_complete_time = time.time()
                        break

            # 统计指标
            ttft_audio = (first_audio_time - send_time) * 1000 if first_audio_time else None
            ttft_text = (first_text_time - send_time) * 1000 if first_text_time else None
            total_duration = (turn_complete_time - send_time) * 1000 if turn_complete_time else None

            res_entry = {
                "round": idx,
                "type": tc["type"],
                "ttft_audio_ms": ttft_audio,
                "ttft_text_ms": ttft_text,
                "total_duration_ms": total_duration,
                "audio_bytes": audio_bytes,
                "user_text": user_text.strip(),
                "model_text": model_text.strip(),
            }
            results.append(res_entry)

            print(f"  ⚡ 首包音频返回延迟 (TTFT Audio): {ttft_audio:.1f} ms" if ttft_audio else "  ⚠️ 未收到音频首包")
            print(f"  📝 首字字幕返回延迟 (TTFT Text) : {ttft_text:.1f} ms" if ttft_text else "  ⚠️ 未收到字幕首字")
            print(f"  ⏱️ 完整轮次完成总耗时 (Total)     : {total_duration:.1f} ms" if total_duration else "  ⚠️ 未完成")
            if user_text:
                print(f"  🗣️ 用户识别文本: \"{user_text}\"")
            if model_text:
                print(f"  🤖 模型回复内容: \"{model_text}\"")
            print()

            await asyncio.sleep(1.0) # 轮次间隔

        rx_task.cancel()

        # 汇总报告
        print("=" * 65)
        print("📊 综合延迟与时长分析报告:")
        print("=" * 65)
        audio_ttfts = [r["ttft_audio_ms"] for r in results if r["ttft_audio_ms"] is not None]
        total_durations = [r["total_duration_ms"] for r in results if r["total_duration_ms"] is not None]

        if audio_ttfts:
            avg_audio_ttft = sum(audio_ttfts) / len(audio_ttfts)
            max_audio_ttft = max(audio_ttfts)
            min_audio_ttft = min(audio_ttfts)
            print(f"🎯 首包音频延迟 (从发送完毕到听到第一个字):")
            print(f"   * 平均时延: {avg_audio_ttft:.1f} ms ({avg_audio_ttft/1000:.2f} 秒)")
            print(f"   * 最快时延: {min_audio_ttft:.1f} ms ({min_audio_ttft/1000:.2f} 秒)")
            print(f"   * 最慢时延: {max_audio_ttft:.1f} ms ({max_audio_ttft/1000:.2f} 秒)")
        else:
            print("⚠️ 未测得有效音频首包延迟")

        if total_durations:
            avg_total = sum(total_durations) / len(total_durations)
            max_total = max(total_durations)
            min_total = min(total_durations)
            print(f"⏳ 完整回复播发耗时 (从发送完毕到整句播报完成):")
            print(f"   * 平均总耗时: {avg_total:.1f} ms ({avg_total/1000:.2f} 秒)")
            print(f"   * 最长耗时  : {max_total:.1f} ms ({max_total/1000:.2f} 秒)")
            print(f"   * 最短耗时  : {min_total:.1f} ms ({min_total/1000:.2f} 秒)")

        print("=" * 65)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
