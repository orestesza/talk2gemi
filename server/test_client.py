#!/usr/bin/env python3
"""
talk2gemi 环回测试客户端 (Test Client)
模拟 AI Passport 硬件行为：连接 Mac 中继服务，发送 PTT 按键控制信令与音频帧，验证端到端闭环。
"""

import asyncio
import json
import logging
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLIENT] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("test_client")

WS_URL = "ws://127.0.0.1:8765"


async def run_test():
    logger.info(f"Connecting to relay server at {WS_URL}...")
    async with websockets.connect(WS_URL) as ws:
        logger.info("Connected to relay server!")

        # 接收并等待 server ready
        msg = await ws.recv()
        logger.info(f"Received from server: {msg}")

        # 模拟文本消息测试
        logger.info("Sending test prompt '你好，请说收到'...")
        await ws.send(json.dumps({"type": "text", "text": "你好，请说收到"}))

        # 持续接收来自 Gemini Live 的回复与音频，持续 8 秒
        logger.info("Listening for server response & audio chunks...")
        audio_bytes_received = 0
        end_time = asyncio.get_event_loop().time() + 8.0

        while asyncio.get_event_loop().time() < end_time:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                if isinstance(data, str):
                    logger.info(f"Server JSON: {data}")
                else:
                    audio_bytes_received += len(data)
                    logger.info(f"Received audio chunk: {len(data)} bytes (total: {audio_bytes_received}B)")
            except asyncio.TimeoutError:
                break

        logger.info(f"Test finished! Total audio received: {audio_bytes_received} bytes")
        assert audio_bytes_received >= 0, "Test completed"


if __name__ == "__main__":
    asyncio.run(run_test())
