#!/usr/bin/env python3
"""
talk2gemi Mac 中继与实时监控服务 (Mac Relay & Web Monitor)
- 全双工长连接管理：AI Passport (ESP32-C3) ↔ Google Gemini Live (gemini-3.8-live)
- 零延迟音频流处理：实时 24kHz -> 16kHz 单声道重采样 (audioop)
- 动态热备与自动重连：解决 Google Live API idle keepalive ping timeout
- 智能防卡死看门狗：5秒未检测到语音回复自动优雅重置回待机 (解决卡在 THINKING 问题)
- 离线网络超时语音兜底 (timeout_16k.pcm)
- Web 可视化实时监控控制台 (HTTP + WebSocket 统一端口 8765)
"""

import asyncio
import audioop
import json
import logging
import os
import socket
import time
import uuid
import re
import secrets
from ble_bridge import BLEBroker
from settings import Settings, VOICES
from credentials import Credentials
from conversations import Conversations
from pathlib import Path
from typing import Dict, List, Optional, Set

from aiohttp import web, WSMsgType
from dotenv import load_dotenv
from google import genai
from google.genai import types

# 路径与配置
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
if os.getenv("TALK2GEMI_LOAD_DOTENV", "1") != "0":
    load_dotenv(ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
credentials = Credentials(GEMINI_API_KEY)
STATE_DIR = Path(os.getenv("TALK2GEMI_STATE_DIR", str(BASE_DIR / ".runtime")))
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))
LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
TIMEOUT_PCM_PATH = BASE_DIR / "assets" / "timeout_16k.pcm"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_HTML_PATH = STATIC_DIR / "dashboard.html"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
class PrivateFormatter(logging.Formatter):
    def format(self, record):
        value = super().format(record)
        keys = [GEMINI_API_KEY]
        if credentials.configured:
            keys.append(credentials.get())
        for key in keys:
            if key:
                value = value.replace(key, "[API KEY REDACTED]")
        return value

for handler in logging.getLogger().handlers:
    handler.setFormatter(PrivateFormatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

logger = logging.getLogger("talk2gemi")

# 预加载超时提示音 (16kHz 16-bit Mono PCM)
TIMEOUT_PCM = b""
if TIMEOUT_PCM_PATH.exists():
    TIMEOUT_PCM = TIMEOUT_PCM_PATH.read_bytes()
    logger.info(f"Loaded timeout PCM prompt: {len(TIMEOUT_PCM)} bytes")
else:
    logger.warning(f"Timeout prompt PCM not found at {TIMEOUT_PCM_PATH}")


def get_local_ip() -> str:
    """获取 Mac 局域网最佳 IP"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class MonitorHub:
    """全局状态中心，负责收集指标并向 Web 监控面板广播"""

    def __init__(self):
        self.start_time = time.time()
        self.local_ip = get_local_ip()
        self.dashboards: Set[web.WebSocketResponse] = set()
        self.current_state = "idle"
        self.total_turns = 0

        # 设备端指标
        self.device_connected = False
        self.device_ip = ""
        self.device_rx_bytes = 0
        self.device_rx_chunks = 0
        self.device_tx_bytes = 0
        self.device_tx_chunks = 0
        self.device_last_seen = "无记录"

        # Gemini 指标
        self.gemini_connected = False
        self.gemini_latency = None

        # 当前会话引用
        self.active_device_session: Optional["DeviceSession"] = None

    def get_stats_dict(self) -> dict:
        uptime = int(time.time() - self.start_time)
        return {
            "type": "stats",
            "server_uptime": uptime,
            "local_ip": self.local_ip,
            "current_state": self.current_state,
            "total_turns": self.total_turns,
            "settings": {"voice": settings.data["voice"], "voices": VOICES, "downlink_codec": settings.data.get("downlink_codec","opus"),
                         "host_id": settings.data["host_id"], "name": settings.data["name"]},
            "credentials": credentials.public_status(),
            "active_dashboards": len(self.dashboards),
            "device": {
                "connected": self.device_connected,
                "ip": self.device_ip,
                "rx_bytes": self.device_rx_bytes,
                "rx_chunks": self.device_rx_chunks,
                "tx_bytes": self.device_tx_bytes,
                "tx_chunks": self.device_tx_chunks,
                "last_seen": self.device_last_seen,
                "telemetry": self.active_device_session.telemetry if self.active_device_session else {},
                "transport": self.active_device_session.transport if self.active_device_session else None,
                "downlink_codec": getattr(self.active_device_session.ws,"current_codec","pcm") if self.active_device_session else None,
            },
            "gemini": {
                "connected": self.gemini_connected,
                "model": LIVE_MODEL,
                "latency": self.gemini_latency,
            },
        }

    async def register_dashboard(self, ws: web.WebSocketResponse):
        self.dashboards.add(ws)
        await ws.send_str(json.dumps(self.get_stats_dict(), ensure_ascii=False))

    def unregister_dashboard(self, ws: web.WebSocketResponse):
        self.dashboards.discard(ws)

    async def broadcast(self, data: dict):
        """向所有打开网页的控制台广播事件"""
        if not self.dashboards:
            return
        payload = json.dumps(data, ensure_ascii=False)
        closed = []
        for ws in self.dashboards:
            try:
                await ws.send_str(payload)
            except Exception:
                closed.append(ws)
        for ws in closed:
            self.dashboards.discard(ws)

    async def set_state(self, state: str, desc: str = ""):
        self.current_state = state
        await self.broadcast({"type": "state", "value": state, "desc": desc})
        # 如果是完成轮次，稍后切回待机
        if state == "completed":
            self.total_turns += 1
            await self.broadcast(self.get_stats_dict())
            asyncio.create_task(self._delay_return_idle())

    async def _delay_return_idle(self):
        await asyncio.sleep(2.0)
        if self.current_state == "completed":
            await self.set_state("idle", "待机中")

    async def emit_log(self, category: str, message: str):
        logger.info(f"[{category}] {message}")
        await self.broadcast({"type": "event", "category": category, "message": message})


hub = MonitorHub()
settings = Settings(STATE_DIR / "settings.json")
sessions = {}
ble_broker = None
conversation_stores = {}
def conversation_store():
    path = STATE_DIR / "conversations.sqlite3"
    if path not in conversation_stores:
        conversation_stores[path] = Conversations(path)
    return conversation_stores[path]



class DeviceSession:
    """单个 AI Passport 终端连接的会话管理器"""

    def __init__(self, ws: web.WebSocketResponse, client_ip: str):
        self.ws = ws
        self.client_ip = client_ip
        self.gemini_client: Optional[genai.Client] = None
        self.live_session = None
        self.live_session_ctx = None
        self.ratecv_state = None
        self.receive_task: Optional[asyncio.Task] = None
        self.thinking_timer: Optional[asyncio.Task] = None
        self.keepalive_task: Optional[asyncio.Task] = None
        self.current_recording = bytearray()
        self.recording_active = False
        self.connect_lock = asyncio.Lock()
        self.history: List[Dict[str, str]] = []
        self.is_active = True
        self.turn_start_time = 0.0
        self.turn_id = None
        self.busy = False
        self.voice = None
        self.device_id = client_ip
        self.transport = "wifi"
        self.links = {}
        self.reply_started = False
        self.stop_time = None
        self.telemetry = {}
        self.key_revision = -1
        self.conversation_id = None
        self.epoch = 0
        self.retry_count = 0
        self.operation_lock = asyncio.Lock()
        self.user_text = ""
        self.assistant_text = ""
        self.input_text = None
        self.last_handle = None
        self.force_fresh = False
        self.voice_switch_task: Optional[asyncio.Task] = None
        self.playback_queue: asyncio.Queue = asyncio.Queue()
        self.playback_task: Optional[asyncio.Task] = asyncio.create_task(self._playback_worker())
        self.playback_busy = False
        self.cloud_turn_finished = False


    def is_gemini_alive(self) -> bool:
        """检查底层与 Google 的 WebSocket 连接是否处于活跃打开状态"""
        if not self.live_session or not hasattr(self.live_session, "_ws"):
            return False
        ws = self.live_session._ws
        return getattr(ws, "state", None) == 1 and getattr(ws, "close_code", None) is None

    async def ensure_gemini_live(self):
        """确保与 Gemini Live 的长连接活跃；若已断开则自动重连"""
        if (self.voice != settings.data["voice"] or self.key_revision != credentials.revision or self.force_fresh) and not self.busy:
            await self.connect_gemini_live(force=self.force_fresh or self.voice != settings.data["voice"])
        if not self.is_gemini_alive():
            await hub.emit_log("GEMINI", "Gemini 长连接未就绪或因闲置超时，正在自动建立连接...")
            try:
                await self.connect_gemini_live()
            except Exception as e:
                logger.error(f"Failed to auto-reconnect Gemini Live: {e}")
                raise

    async def request_voice_switch(self, new_voice: str):
        """切换音色：清除旧音色恢复句柄，空闲时立即异步建立新音色会话，繁忙时待本轮结束自动切换"""
        self.ensure_memory()
        conversation_store().handle(self.conversation_id, clear=True)
        self.last_handle = None
        self.force_fresh = True
        await hub.emit_log("GEMINI", f"参考声线已设为 {new_voice}（不使用旧音色恢复句柄，保留文字历史）")
        if not self.busy and self.is_active:
            self.schedule_voice_reconnect()

    def schedule_voice_reconnect(self):
        if not self.is_active:
            return
        if self.voice_switch_task and not self.voice_switch_task.done():
            self.voice_switch_task.cancel()
        self.voice_switch_task = asyncio.create_task(self._voice_reconnect_worker())

    async def _voice_reconnect_worker(self):
        try:
            while self.is_active and not self.busy and (
                self.voice != settings.data["voice"] or not self.is_gemini_alive()
            ):
                target_voice = settings.data["voice"]
                self.ensure_memory()
                conversation_store().handle(self.conversation_id, clear=True)
                self.last_handle = None
                self.force_fresh = True
                hub.gemini_connected = False
                await hub.broadcast(hub.get_stats_dict())
                await self.send_json(dict(type="metrics", cloud=False, latency=hub.gemini_latency,
                                          voice=target_voice, model=LIVE_MODEL))
                await self.connect_gemini_live(force=True)
                self.force_fresh = False
                hub.gemini_connected = True
                await hub.broadcast(hub.get_stats_dict())
                if not self.busy and self.voice == settings.data["voice"]:
                    await self.send_json(dict(type="state", value="ready"))
                    await hub.set_state("idle", f"音色 {self.voice} 已就绪")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.cloud_failure(exc)
            hub.gemini_connected = False
            await hub.emit_log("WARN", f"切换音色连接云端失败 ({type(exc).__name__}): {exc}")
            await hub.broadcast(hub.get_stats_dict())
            if hub.active_device_session is self and not self.busy:
                await self.send_json(dict(type="state", value="error", reason="Voice switch failed. Please retry."))
                await hub.set_state("error", "音色切换连接失败，请重试")

    async def send_json(self, data: dict):
        """向终端发送控制信令/状态/字幕"""
        if data.get("type") == "transcript":
            return  # Full Chinese transcript is available on the web panel.
        try:
            await self.ws.send_str(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"Failed to send JSON to {self.client_ip}: {e}")

    async def send_audio_chunk(self, pcm_chunk: bytes):
        """向终端发送二进制音频块"""
        try:
            await self.ws.send_bytes(pcm_chunk)
            hub.device_tx_bytes += len(pcm_chunk)
            hub.device_tx_chunks += 1
        except Exception as e:
            logger.debug(f"Failed to send audio to {self.client_ip}: {e}")

    async def play_timeout_prompt(self):
        """播放预缓存的'网络超时'提示音并下发状态"""
        if self.thinking_timer and not self.thinking_timer.done():
            self.thinking_timer.cancel()

        await hub.set_state("error", "网络超时")
        await hub.emit_log("WARN", f"触发网络超时提示音下发给终端 {self.client_ip}")
        await self.send_json({"type": "state", "value": "error"})
        await self.send_json({"type": "transcript", "role": "model", "text": "Network Timeout"})
        await hub.broadcast({"type": "transcript", "role": "model", "text": "网络超时 (Network Timeout)"})

        if TIMEOUT_PCM:
            chunk_size = 1024
            for i in range(0, len(TIMEOUT_PCM), chunk_size):
                chunk = TIMEOUT_PCM[i : i + chunk_size]
                await self.send_audio_chunk(chunk)
                await asyncio.sleep(0.02)

        await self.send_json({"type": "state", "value": "ready"})
        await hub.set_state("idle", "待机就绪")

    async def connect_gemini_live(self, force=False):
        async with self.connect_lock:
            if not self.is_active:
                return
            if not force and not self.force_fresh and self.is_gemini_alive() and self.voice == settings.data["voice"] and self.key_revision == credentials.revision:
                return
            await asyncio.wait_for(self._connect_gemini_live(), timeout=10.0)

    async def _connect_gemini_live(self):
        """建立与 Gemini Live 的全双工长连接"""
        api_key = credentials.get()
        selected_voice = settings.data["voice"]
        selected_key_revision = credentials.revision

        # 清理可能存在的旧连接
        if self.receive_task and not self.receive_task.done():
            self.receive_task.cancel()
            await asyncio.gather(self.receive_task, return_exceptions=True)
        if self.live_session and self.live_session_ctx:
            try:
                # SDK/WebSocket graceful close may wait for the peer for many
                # seconds. A new PTT turn must not inherit that unbounded wait.
                await asyncio.wait_for(self.live_session_ctx.__aexit__(None, None, None), 1.5)
            except Exception:
                logger.info("Previous cloud session close exceeded budget; opening fresh connection")
        self.live_session = None
        if self.gemini_client:
            try:
                await asyncio.wait_for(self.gemini_client.aio.aclose(), 1.5)
            except Exception:
                logger.info("Previous cloud client cleanup exceeded budget")

        self.gemini_client = genai.Client(api_key=api_key)
        
        system_text = (
            "你是一个运行在随身便携硬件终端上的 AI 语音助手，名叫 talk2gemi。"
            "请使用简短、自然、口语化的中文与用户对话，避免书面长篇大论。"
            "回答要直击要害，适合即时语音收听。"
            "当前应用只有语音和文字对话能力，没有定时器、闹钟、提醒、联网查询或操作电脑的工具。"
            "不得声称已经设置闹钟或提醒，不得声称完成任何实际没有执行的操作。"
        )
        self.ensure_memory()
        if self.voice is not None and self.voice != selected_voice:
            self.force_fresh = True
        if self.force_fresh:
            conversation_store().handle(self.conversation_id, clear=True)
            self.last_handle = None
        resume_handle = None if self.force_fresh else conversation_store().handle(self.conversation_id)

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            session_resumption=types.SessionResumptionConfig(handle=resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            # PTT owns the turn boundary. Do not ask automatic VAD to guess
            # whether a paused/closed microphone means the user has finished.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=types.Content(
                parts=[
                    types.Part(text=system_text)
                ]
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=selected_voice)
                )
            ),
        )

        await hub.emit_log("GEMINI", f"正在连接 Gemini Live API ({LIVE_MODEL}, 音色: {selected_voice})...")
        self.live_session_ctx = self.gemini_client.aio.live.connect(
            model=LIVE_MODEL, config=config
        )
        try:
            self.live_session = await self.live_session_ctx.__aenter__()
        except Exception:
            if not resume_handle:
                raise
            # A rejected/expired handle must not prevent text-history recovery.
            conversation_store().handle(self.conversation_id, clear=True)
            config.session_resumption = types.SessionResumptionConfig()
            resume_handle = None
            self.live_session_ctx = self.gemini_client.aio.live.connect(model=LIVE_MODEL, config=config)
            self.live_session = await self.live_session_ctx.__aenter__()
        self.last_handle = None
        if resume_handle:
            await hub.emit_log("MEMORY", "已使用恢复句柄重新建立云端会话")
        if not resume_handle:
            context = conversation_store().context(self.conversation_id)
            if context:
                await self.live_session.send_client_content(
                    turns=[types.Content(role=x['role'],parts=[types.Part(text=x['text'])]) for x in context],
                    turn_complete=False)
                await hub.emit_log("MEMORY", "已从本地文字历史恢复当前会话上下文")
        if not self.is_active:
            await self.live_session_ctx.__aexit__(None, None, None)
            await self.gemini_client.aio.aclose()
            return
        self.force_fresh = False
        self.voice = selected_voice
        self.key_revision = selected_key_revision
        hub.gemini_connected = True
        await hub.emit_log("GEMINI", f"Gemini Live 会话长连接建立成功 (音色: {selected_voice})")
        await hub.broadcast(hub.get_stats_dict())
        if not self.busy:
            await self.send_json({"type":"state","value":"ready"})

        # 启动后台协程接收 Gemini 的下行音频与字幕
        self.receive_task = asyncio.create_task(self._gemini_receive_loop())

        # 启动热备保活巡检任务
        if not self.keepalive_task or self.keepalive_task.done():
            self.keepalive_task = asyncio.create_task(self._gemini_keepalive_loop())

    async def _gemini_keepalive_loop(self):
        """每 15 秒检查一次长连接状态，断线自动静默热备重连"""
        while self.is_active:
            try:
                await asyncio.sleep(15)
                if self.is_active and not self.busy and not self.is_gemini_alive():
                    await hub.emit_log("GEMINI", "巡检发现连接超时断开，正在热备重连...")
                    await self.connect_gemini_live()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Keepalive loop exception: {e}")

    def ensure_memory(self):
        if self.conversation_id is None:
            self.conversation_id = conversation_store().current(self.device_id)

    async def record_change(self, **fields):
        if self.conversation_id and self.turn_id:
            conversation_store().update(self.turn_id, **fields)
            await hub.broadcast(dict(type="history_changed", conversation=self.conversation_id, turn=self.turn_id))

    async def cancel_turn(self, reason="用户开始了新一轮"):
        self.epoch += 1
        timer = self.thinking_timer
        self.thinking_timer = None
        if timer and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        if self.receive_task and not self.receive_task.done():
            self.receive_task.cancel()
            await asyncio.gather(self.receive_task, return_exceptions=True)
        while not self.playback_queue.empty():
            try:
                self.playback_queue.get_nowait()
                self.playback_queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                break
        if self.busy:
            await self.record_change(status="cancelled", error=reason)
        self.busy = self.recording_active = False
        self.cloud_turn_finished = False
        self.current_recording.clear()
        if hasattr(self.ws, "abort_audio"):
            await self.ws.abort_audio()
        await self.send_json(dict(type="audio_abort"))
        self.ensure_memory()
        conversation_store().handle(self.conversation_id, clear=True)
        self.last_handle = None
        self.force_fresh = True

    async def switch_conversation(self, cid=None):
        async with self.operation_lock:
            if self.busy:
                raise web.HTTPConflict(text="请先完成当前对话")
            return await self._switch_conversation(cid)

    async def _switch_conversation(self, cid=None):
        await self.cancel_turn("切换会话")
        store = conversation_store()
        if cid:
            store.select(self.device_id, cid)
        else:
            cid = store.new(self.device_id)
        self.conversation_id = cid
        await self.connect_gemini_live(force=True)
        await self.send_json(dict(type="state", value="ready"))
        return cid

    async def delete_history(self, cid=None):
        async with self.operation_lock:
            current = cid is None or cid == self.conversation_id
            if current and self.busy:
                raise web.HTTPConflict(text="请先完成当前对话")
            if current:
                await self.cancel_turn("删除当前会话")
            conversation_store().delete(cid)
            if current:
                self.conversation_id = conversation_store().new(self.device_id)
                await self.connect_gemini_live(force=True)

    async def _thinking_timeout_watchdog(self, timeout_sec: float = 12.0):
        epoch, tid = self.epoch, self.turn_id
        try:
            await asyncio.sleep(timeout_sec)
            if epoch != self.epoch or tid != self.turn_id or not self.busy or self.reply_started:
                return
            if self.retry_count >= 1:
                self.busy = False
                self.epoch += 1  # reject late packets before exposing retry failure
                if self.receive_task and not self.receive_task.done():
                    self.receive_task.cancel()
                    await asyncio.gather(self.receive_task, return_exceptions=True)
                await self.record_change(status="timeout", error="重试后仍未收到语音回复")
                self.current_recording.clear()
                self.force_fresh = True
                self.last_handle = None
                conversation_store().handle(self.conversation_id, clear=True)
                await self.send_json(dict(type="state", value="error", reason="Cloud timeout. Please repeat."))
                await hub.set_state("error", "云端超时，请重试")
                return
            self.retry_count = 1
            self.assistant_text = ""
            await self.record_change(status="retrying", retries=1, assistant="")
            await hub.emit_log("WARN", "12 秒无语音回复：重建上下文并重试一次")
            await self.send_json(dict(type="state", value="thinking", reason="Reconnecting... Retry 1/1"))
            # A resumable handle may already contain the unanswered question.
            # Retry from completed history on a fresh connection, replay once.
            self.force_fresh = True
            self.last_handle = None
            conversation_store().handle(self.conversation_id, clear=True)
            await self.connect_gemini_live(force=True)
            if epoch != self.epoch or tid != self.turn_id:
                return
            if self.input_text is not None:
                await self.live_session.send_client_content(turns=types.Content(role="user",parts=[types.Part(text=self.input_text)]),turn_complete=True)
            else:
                await self.live_session.send_realtime_input(activity_start=types.ActivityStart())
                for offset in range(0,len(self.current_recording),1024):
                    await self.live_session.send_realtime_input(audio=types.Blob(data=bytes(self.current_recording[offset:offset+1024]),mime_type="audio/pcm;rate=16000"))
                await self.live_session.send_realtime_input(activity_end=types.ActivityEnd())
            self.stop_time = time.monotonic()
            self.thinking_timer = asyncio.create_task(self._thinking_timeout_watchdog(timeout_sec))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if epoch == self.epoch:
                self.busy = False
                self.current_recording.clear()
                await self.record_change(status="error", error="重连重试失败："+type(exc).__name__)
                await self.send_json(dict(type="state",value="error",reason="Cloud retry failed. Please repeat."))
                await hub.set_state("error", "重试失败，请重新提问")

    async def _gemini_receive_loop(self):
        """持续接收来自 Gemini Live 的语音和字幕并分流回传给设备"""
        epoch = self.epoch
        live = self.live_session
        try:
            while self.is_active and self.is_gemini_alive() and epoch == self.epoch:
                async for response in live.receive():
                    if not self.is_active or epoch != self.epoch:
                        return
                    update = getattr(response, "session_resumption_update", None)
                    if update and update.resumable and update.new_handle:
                        if self.voice == settings.data["voice"] and not self.force_fresh:
                            self.last_handle = update.new_handle
                            if not self.busy and self.conversation_id:
                                conversation_store().handle(self.conversation_id, self.last_handle)
                    if getattr(response, "go_away", None) and not self.busy:
                        asyncio.create_task(self.connect_gemini_live(force=True))
                        return
                    sc = response.server_content
                    if not sc:
                        continue

                    # 1. 实时字幕处理
                    if sc.input_transcription and sc.input_transcription.text:
                        user_text = sc.input_transcription.text
                        if self.retry_count == 0:
                            self.user_text += user_text
                            await self.record_change(user=self.user_text)
                        await hub.emit_log("PTT", f"用户语音识别: {user_text}")
                        await self.send_json({"type": "transcript", "role": "user", "text": user_text})
                        await hub.broadcast({"type": "transcript", "role": "user", "text": user_text})

                    if sc.output_transcription and sc.output_transcription.text:
                        model_text = sc.output_transcription.text
                        self.assistant_text += model_text
                        await self.record_change(assistant=self.assistant_text)
                        await hub.emit_log("GEMINI", f"Gemini 实时字幕: {model_text}")
                        await self.send_json({"type": "transcript", "role": "model", "text": model_text})
                        await hub.broadcast({"type": "transcript", "role": "model", "text": model_text})

                    # 2. 语音流处理 (Gemini 24kHz -> 终端 16kHz)
                    if sc.model_turn and sc.model_turn.parts:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                # An input transcript is not a reply. Keep the
                                # watchdog running until actual model audio.
                                if self.thinking_timer and not self.thinking_timer.done():
                                    self.thinking_timer.cancel()
                                if not self.reply_started:
                                    self.reply_started = True
                                    await self.record_change(status="speaking")
                                    if self.stop_time is not None:
                                        hub.gemini_latency = round((time.monotonic() - self.stop_time) * 1000)
                                        await self.record_change(latency=hub.gemini_latency)
                                raw_24k = part.inline_data.data
                                # 重采样 24kHz -> 16kHz (2 字节 16-bit 单声道)
                                pcm_16k, self.ratecv_state = audioop.ratecv(
                                    raw_24k, 2, 1, 24000, 16000, self.ratecv_state
                                )
                                # 分块推入播放队列 (每块 1024 字节以适配单片机微小缓冲区)
                                chunk_size = 1024
                                for i in range(0, len(pcm_16k), chunk_size):
                                    chunk = pcm_16k[i : i + chunk_size]
                                    await self.playback_queue.put((epoch, self.turn_id, "audio", chunk))

                    # 3. 对话轮次完成
                    if sc.turn_complete:
                        self.cloud_turn_finished = True
                        if self.playback_queue.empty() and not self.playback_busy:
                            await self._complete_turn(epoch, self.turn_id)
                        else:
                            await hub.emit_log("GEMINI", "云端回复流接收完毕，正在下发播放...")
                            await self.playback_queue.put((epoch, self.turn_id, "turn_complete", None))

                    # 4. 被打断处理 (Interruption)
                    if sc.interrupted:
                        while not self.playback_queue.empty():
                            try:
                                self.playback_queue.get_nowait()
                                self.playback_queue.task_done()
                            except (asyncio.QueueEmpty, ValueError):
                                break
                        await self._interrupt_turn(epoch, self.turn_id)

                # 单轮结束后日志提示，准备循环接收下一轮
                logger.debug("Gemini 轮次迭代结束，继续等待下一轮交互...")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Gemini receive loop disconnected for {self.client_ip}: {e}")
            hub.gemini_connected = False
            if self.busy and not self.reply_started:
                await hub.emit_log("WARN", "云端接收中断，当前轮由无回复重试机制接管")
                return
            if self.busy and self.cloud_turn_finished:
                await hub.emit_log("GEMINI", "云端连接已断开，但下发语音播放正常继续中")
                return
            await self.record_change(status="error", error="云端接收中断")
            self.cloud_failure(e)
            await hub.broadcast(hub.get_stats_dict())

            if hub.active_device_session is self:
                await self.send_json({"type": "state", "value": "error", "reason": "Cloud unavailable. Please retry."})
                await hub.set_state("error", "云端暂时不可用")

            # 自动后台热备重连
            if self.is_active and not self.busy:
                asyncio.create_task(self.ensure_gemini_live())

    async def _playback_worker(self):
        """异步消费下行音频队列并受控推送到设备，使 Gemini 接收循环免受 BLE 速率阻塞"""
        while self.is_active:
            try:
                item = await self.playback_queue.get()
            except asyncio.CancelledError:
                break
            self.playback_busy = True
            try:
                epoch, turn_id, kind, chunk = item
                if epoch == self.epoch:
                    if kind == "audio":
                        if hub.current_state != "speaking":
                            await hub.set_state("speaking", "正在语音回复")
                            await self.send_json({"type": "state", "value": "speaking"})
                        await self.send_audio_chunk(chunk)
                        await asyncio.sleep(0.01)
                    elif kind == "turn_complete":
                        await self._complete_turn(epoch, turn_id)
                    elif kind == "interrupted":
                        await self._interrupt_turn(epoch, turn_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Playback worker error: {e}", exc_info=True)
                await asyncio.sleep(0.05)
            finally:
                self.playback_busy = False
                self.playback_queue.task_done()

    async def _complete_turn(self, epoch: int, turn_id: Optional[str]):
        if epoch != self.epoch or (turn_id and turn_id != self.turn_id):
            return
        if hasattr(self.ws, "drain_audio"):
            await self.ws.drain_audio()
        self.busy = False
        await self.record_change(status="completed")
        self.current_recording.clear()
        if self.last_handle and self.conversation_id and self.voice == settings.data["voice"] and not self.force_fresh:
            conversation_store().handle(self.conversation_id, self.last_handle)
        if self.thinking_timer and not self.thinking_timer.done():
            self.thinking_timer.cancel()
        self.ratecv_state = None
        await hub.emit_log("STATE", "本轮对话生成完毕，回复播发完成")
        if not self.recording_active:
            if self.voice != settings.data["voice"] or self.force_fresh:
                self.schedule_voice_reconnect()
            else:
                await self.send_json({"type": "state", "value": "ready"})
                await hub.set_state("completed", "发送成功 / 轮次完成")

    async def _interrupt_turn(self, epoch: int, turn_id: Optional[str]):
        if epoch != self.epoch or (turn_id and turn_id != self.turn_id):
            return
        if hasattr(self.ws, "abort_audio"):
            await self.ws.abort_audio()
        self.busy = False
        self.ratecv_state = None
        await hub.emit_log("STATE", "用户说话打断了当前回复")
        if not self.recording_active:
            if self.voice != settings.data["voice"] or self.force_fresh:
                self.schedule_voice_reconnect()
            else:
                await self.send_json({"type": "state", "value": "ready"})
                await hub.set_state("idle", "待机中")

    async def handle_binary_audio(self, pcm_chunk: bytes):
        """处理设备端上行的 16kHz PCM 音频"""
        hub.device_rx_bytes += len(pcm_chunk)
        hub.device_rx_chunks += 1
        hub.device_last_seen = time.strftime("%H:%M:%S")
        # Ignore in-flight DMA/network tails outside start/stop. Forwarding
        # them after activityEnd could reopen or contaminate the next turn.
        if not self.recording_active:
            return
        if len(self.current_recording) + len(pcm_chunk) > 30 * 32000:
            await self.handle_text_command('{"type":"stop"}')
            return
        self.current_recording.extend(pcm_chunk)

        if self.live_session and self.is_gemini_alive():
            try:
                await self.live_session.send_realtime_input(
                    audio=types.Blob(data=pcm_chunk, mime_type="audio/pcm;rate=16000")
                )
            except Exception as e:
                logger.error(f"Failed to stream audio to Gemini: {e}")
                hub.gemini_connected = False

    async def handle_text_command(self, cmd_str: str):
        """处理设备端或测试端的控制信令"""
        try:
            cmd = json.loads(cmd_str)
            cmd_type = cmd.get("type")

            if cmd_type == "start":
                if self.recording_active:
                    return
                await self.begin_turn()
                # 用户按下按键 (PTT Press) -> 收到语音
                if self.thinking_timer and not self.thinking_timer.done():
                    self.thinking_timer.cancel()

                self.current_recording.clear()
                try:
                    await self.ensure_gemini_live()
                    self.busy = True
                    if not self.receive_task or self.receive_task.done():
                        self.receive_task = asyncio.create_task(self._gemini_receive_loop())

                    self.turn_start_time = time.time()
                    await self.live_session.send_realtime_input(activity_start=types.ActivityStart())
                except Exception as exc:
                    await self.record_change(status="error", error="启动录音会话失败：" + type(exc).__name__)
                    self.cloud_failure(exc)
                    raise
                self.recording_active = True
                await hub.emit_log("PTT", f"PTT 按下: 开始录音 (来自 {self.client_ip})")
                await hub.set_state("listening", "收到语音中")
                await self.send_json({"type": "state", "value": "listening"})

            elif cmd_type == "stop":
                if not self.recording_active:
                    return
                self.recording_active = False
                await self.record_change(status="thinking")
                self.stop_time = time.monotonic()
                # 用户松开按键 (PTT Release) -> 发送语音 / 正在思考
                rec_bytes = bytes(self.current_recording)
                rec_len = len(rec_bytes)
                rms = audioop.rms(rec_bytes, 2) if rec_len > 0 else 0
                max_amp = audioop.max(rec_bytes, 2) if rec_len > 0 else 0

                await hub.emit_log(
                    "PTT",
                    f"PTT 松开: 录音共 {rec_len} 字节 (约 {rec_len/32000:.2f}秒), RMS音量={rms}, 峰值={max_amp}"
                )

                if rec_len > 0 and os.getenv("TALK2GEMI_SAVE_RECORDINGS") == "1":
                    scratch_dir = STATE_DIR / "diagnostics"
                    scratch_dir.mkdir(exist_ok=True)
                    (scratch_dir / "last_ptt.pcm").write_bytes(rec_bytes)

                if rec_len == 0 or rms < 80:
                    await hub.emit_log("WARN", f"⚠️ 录音音量极低或为静音 (RMS={rms})，若无回复请靠近麦克风大声说话")

                await hub.set_state("thinking", "正在思考生成")
                await self.send_json({"type": "state", "value": "thinking"})

                # 启动 12 秒看门狗 (基于实测 Gemini 平均响应时长与网络波动预留充裕空间)
                if self.thinking_timer and not self.thinking_timer.done():
                    self.thinking_timer.cancel()
                self.thinking_timer = asyncio.create_task(self._thinking_timeout_watchdog(12.0))

                if self.is_gemini_alive():
                    try:
                        await self.live_session.send_realtime_input(activity_end=types.ActivityEnd())
                    except Exception as e:
                        logger.error(f"Failed to commit PTT activity end: {e}")
                        await self.play_timeout_prompt()
                else:
                    await hub.emit_log("WARN", "Gemini 会话断开，触发提示音并重连")
                    await self.play_timeout_prompt()
                    asyncio.create_task(self.ensure_gemini_live())

            elif cmd_type == "text":
                if self.recording_active:
                    raise ValueError("请先松开设备录音按键，再发送文字")
                # 文本直推测试
                text_input = cmd.get("text", "")
                if not isinstance(text_input, str) or not text_input.strip() or len(text_input) > 512:
                    raise ValueError("Text must contain 1..512 characters")
                await self.begin_turn(max(2048, len(text_input) * 4))
                self.input_text = self.user_text = text_input
                await self.record_change(user=text_input, status="thinking")
                self.stop_time = time.monotonic()
                await hub.emit_log("STATE", f"直接文本对话测试: '{text_input}'")
                await hub.set_state("thinking", "正在思考生成")
                await self.send_json({"type": "state", "value": "thinking"})

                if self.thinking_timer and not self.thinking_timer.done():
                    self.thinking_timer.cancel()
                self.thinking_timer = asyncio.create_task(self._thinking_timeout_watchdog(12.0))

                await self.ensure_gemini_live()
                self.busy = True
                if not self.receive_task or self.receive_task.done():
                    self.receive_task = asyncio.create_task(self._gemini_receive_loop())
                if self.is_gemini_alive():
                    try:
                        await self.live_session.send_client_content(
                            turns=types.Content(role="user", parts=[types.Part(text=text_input)]),
                            turn_complete=True,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send text to Gemini: {e}")
                        await self.play_timeout_prompt()

            elif cmd_type == "ping":
                await self.send_json({"type": "pong"})
            elif cmd_type == "telemetry":
                self.telemetry = cmd

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {cmd_str[:50]}")

    async def begin_turn(self, new_input=2048):
        async with self.operation_lock:
            return await self._begin_turn(new_input)

    async def _begin_turn(self, new_input=2048):
        if self.voice_switch_task and not self.voice_switch_task.done():
            self.voice_switch_task.cancel()
            await asyncio.gather(self.voice_switch_task, return_exceptions=True)
        if self.busy:
            await hub.emit_log("PTT", "播放/等待期间收到新按键：取消旧轮并建立新轮")
            await self.cancel_turn()
            await self.connect_gemini_live(force=True)
            await hub.emit_log("PTT", "打断交接完成，准备接收新录音")
        else:
            self.epoch += 1
            if self.receive_task and not self.receive_task.done():
                self.receive_task.cancel()
                await asyncio.gather(self.receive_task, return_exceptions=True)
        self.ensure_memory()
        if self.voice is not None and self.voice != settings.data["voice"]:
            conversation_store().handle(self.conversation_id, clear=True)
            self.last_handle = None
            self.force_fresh = True
        self.turn_id = str(uuid.uuid4())
        self.retry_count = 0
        self.user_text = self.assistant_text = ""
        self.input_text = None
        conversation_store().begin(self.conversation_id, self.turn_id)
        self.busy = True
        if self.force_fresh or self.voice != settings.data["voice"] or self.key_revision != credentials.revision:
            try:
                await self.connect_gemini_live(force=self.force_fresh or self.voice != settings.data["voice"])
                self.force_fresh = False
            except Exception as exc:
                await self.record_change(status="error", error="云端连接失败：" + type(exc).__name__)
                self.cloud_failure(exc)
                raise
        self.reply_started = False
        self.stop_time = None
        self.cloud_turn_finished = False

    def cloud_failure(self, error):
        self.busy = False
        self.recording_active = False
        message = str(error)
        logger.warning("Cloud unavailable: %s", message)

    async def close(self):
        """清理会话资源"""
        self.is_active = False
        self.epoch += 1
        if self.voice_switch_task and not self.voice_switch_task.done():
            self.voice_switch_task.cancel()
            await asyncio.gather(self.voice_switch_task, return_exceptions=True)
        if self.busy:
            await self.record_change(status="interrupted",error="设备连接关闭")
        self.current_recording.clear()
        if self.thinking_timer and not self.thinking_timer.done():
            self.thinking_timer.cancel()
        if self.keepalive_task and not self.keepalive_task.done():
            self.keepalive_task.cancel()
        if self.receive_task and not self.receive_task.done():
            self.receive_task.cancel()
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()
            await asyncio.gather(self.playback_task, return_exceptions=True)
        if self.live_session and self.live_session_ctx:
            try:
                await self.live_session_ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"Error closing live session: {e}")
        if self.gemini_client:
            await self.gemini_client.aio.aclose()
        if hub.active_device_session is self:
            hub.gemini_connected = False
        await hub.emit_log("DEVICE", f"设备会话已关闭: {self.client_ip}")


# ======================================================================
# HTTP & WebSocket 路由处理器
# ======================================================================

async def attach_device(ws, device_id, transport, peer):
    if not re.fullmatch(r"[A-Za-z0-9:_-]{4,64}", device_id):
        raise ValueError("Invalid device identity")
    session = sessions.get(device_id)
    if session is None or not session.is_active:
        session = DeviceSession(ws, peer)
        session.device_id = device_id
        sessions[device_id] = session
    previous = session.links.get(transport)
    # Publish the replacement before closing the old socket. Its finally block
    # can run immediately and must not close this same logical device session.
    session.links[transport] = ws
    if previous and previous is not ws:
        await previous.close()
    if hasattr(ws, "session"):
        ws.session = session
    await ws.send_str(json.dumps(dict(type="hello", host_id=settings.data["host_id"],
                                     name=settings.data["name"], model=LIVE_MODEL,
                                     voice=settings.data["voice"], transport=transport,
                                     relay_ip=hub.local_ip, relay_port=SERVER_PORT)))
    return session


async def activate_device(session, ws, transport):
    if session.busy and session.ws is not ws:
        raise ValueError("Active turn cannot change transport")
    session.ws, session.transport = ws, transport
    hub.active_device_session = session
    hub.device_connected, hub.device_ip = True, session.client_ip
    try:
        await session.ensure_gemini_live()
        await session.send_json(dict(type="state", value="ready"))
    except Exception as exc:
        session.cloud_failure(exc)
        await session.send_json(dict(type="state", value="error", reason="Cloud unavailable"))


async def detach_device(session, ws, transport):
    if session.links.get(transport) is ws:
        session.links.pop(transport)
    if session.ws is ws:
        if session.busy:
            await session.record_change(status="interrupted", error="设备连接关闭")
            session.busy = session.recording_active = False
            # Drop cloud state as well: never play a partial old turn on fallback.
            if session.receive_task:
                session.receive_task.cancel()
            if session.live_session_ctx:
                await session.live_session_ctx.__aexit__(None, None, None)
            session.live_session = None
        hub.device_connected = False
    if not session.links:
        await session.close()
        if sessions.get(session.device_id) is session:
            sessions.pop(session.device_id)
        if hub.active_device_session is session:
            hub.active_device_session = None


async def v2_device_handler(request):
    ws = web.WebSocketResponse(max_msg_size=16384, heartbeat=20)
    await ws.prepare(request)
    session = None
    try:
        expected = request.query.get("host", "")
        if expected and expected != settings.data["host_id"]:
            raise ValueError("Selected Mac identity does not match")
        session = await attach_device(ws, request.query.get("id", ""), "wifi", request.remote)
        async for msg in ws:
            try:
                if msg.type == WSMsgType.TEXT:
                    cmd = json.loads(msg.data)
                    if cmd.get("type") == "activate":
                        await activate_device(session, ws, "wifi")
                    elif session.ws is ws and hub.active_device_session is session:
                        await session.handle_text_command(msg.data)
                elif msg.type == WSMsgType.BINARY and session.ws is ws and hub.active_device_session is session:
                    await session.handle_binary_audio(msg.data)
            except ValueError as exc:
                await ws.send_json(dict(type="error", reason=str(exc)))
    except Exception as exc:
        await ws.send_json(dict(type="error", reason=str(exc)))
        await ws.close()
    finally:
        if session:
            await detach_device(session, ws, "wifi")
    return ws


async def ble_bridge_handler(request):
    if request.remote not in ("127.0.0.1", "::1"):
        raise web.HTTPForbidden()
    if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + request.app["bridge_token"]):
        raise web.HTTPForbidden()
    return await ble_broker.handler(request)


async def api_settings_handler(request):
    if request.method == "POST":
        # Mutations require a same-origin JSON request (no simple cross-site form).
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden()
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        data = await request.json()
        try:
            if "downlink_codec" in data:
                codec=data["downlink_codec"]
                if codec not in ("opus","adpcm"):raise ValueError("Unsupported downlink codec")
                if hub.active_device_session and hub.active_device_session.busy:
                    raise ValueError("Finish the current turn before changing audio quality")
                settings.data["downlink_codec"]=codec;settings.save()
                if hub.active_device_session and hasattr(hub.active_device_session.ws, "update_codec"):
                    hub.active_device_session.ws.update_codec()
            else:
                new_voice = data.get("voice")
                old_voice = settings.data["voice"]
                settings.set_voice(new_voice)
                session = hub.active_device_session
                if session and session.is_active and (new_voice != old_voice or session.voice != new_voice):
                    await session.request_voice_switch(new_voice)
        except ValueError as exc:
            return web.json_response(dict(ok=False, error=str(exc)), status=400)
        await hub.broadcast(hub.get_stats_dict())
    return web.json_response(dict(voice=settings.data["voice"], voices=VOICES,
                                  active_voice=(hub.active_device_session.voice or settings.data["voice"]) if hub.active_device_session else settings.data["voice"],
                                  pending=bool(hub.active_device_session and hub.active_device_session.busy)))


async def api_volume_handler(request):
    origin = request.headers.get("Origin")
    if origin and origin != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden()
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType()
    data = await request.json()
    value = data.get("volume")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        return web.json_response(dict(ok=False, error="Volume must be 0..100"), status=400)
    session = hub.active_device_session
    if not session or not hub.device_connected:
        return web.json_response(dict(ok=False, error="Device offline"), status=503)
    await session.send_json(dict(type="volume", value=value))
    return web.json_response(dict(ok=True, volume=value))


async def status_loop():
    while True:
        for session in list(sessions.values()) + ([hub.active_device_session] if hub.active_device_session and hub.active_device_session not in sessions.values() else []):
            if session.is_active:
                await session.send_json(dict(type="metrics", cloud=session.is_gemini_alive(),
                                             latency=hub.gemini_latency, voice=session.voice or settings.data["voice"], model=LIVE_MODEL))
        await hub.broadcast(hub.get_stats_dict())
        await asyncio.sleep(2)


async def start_background(app):
    global ble_broker
    token_path = STATE_DIR / "bridge.token"
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32))
        token_path.chmod(0o600)
    app["bridge_token"] = token_path.read_text().strip()
    ble_broker = BLEBroker(settings, attach_device, activate_device, detach_device, credentials)
    app["status_task"] = asyncio.create_task(status_loop())


async def stop_background(app):
    app["status_task"].cancel()
    await asyncio.gather(app["status_task"], return_exceptions=True)
    for session in list(sessions.values()):
        await session.close()

async def root_handler(request: web.Request):
    """
    根路由：
    - 如果是浏览器普通 HTTP GET 请求，返回可视化监控仪表盘
    - 如果带有 Upgrade: websocket 头部，则视为 AI Passport 硬件建立长连接
    """
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await device_ws_handler(request)

    # 渲染 HTML 页面
    if DASHBOARD_HTML_PATH.exists():
        html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")
    return web.Response(text="<h1>talk2gemi Dashboard HTML not found</h1>", status=404)


async def device_ws_handler(request: web.Request):
    """处理硬件终端 (AI Passport) 的 WebSocket 音频流长连接"""
    if request.path == "/ws/device":
        return await v2_device_handler(request)
    peer_ip = request.remote or "unknown"
    ws = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024, heartbeat=20.0)
    await ws.prepare(request)

    await hub.emit_log("DEVICE", f"AI Passport 设备已上线 (IP: {peer_ip})")
    hub.device_connected = True
    hub.device_ip = peer_ip
    hub.device_last_seen = time.strftime("%H:%M:%S")

    session = DeviceSession(ws, peer_ip)
    hub.active_device_session = session

    try:
        # 1. 尝试连接 Gemini Live
        try:
            await asyncio.wait_for(session.connect_gemini_live(), timeout=8.0)
            await session.send_json({"type": "state", "value": "ready"})
            await hub.set_state("idle", "待机就绪")
        except Exception as e:
            logger.error(f"Gemini Live connection failed on boot: {e}")
            await session.play_timeout_prompt()

        # 2. 持续处理硬件上行音频与按键信令
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await session.handle_binary_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                await session.handle_text_command(msg.data)
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"Device ws connection error: {ws.exception()}")

    finally:
        # A reboot can reconnect before the old TCP heartbeat expires. The
        # old handler must not erase the new session from the dashboard/API.
        owns_hub = hub.active_device_session is session
        if owns_hub:
            hub.device_connected = False
        await session.close()
        if hub.active_device_session is session:
            hub.active_device_session = None
            await hub.emit_log("DEVICE", f"AI Passport 设备已离线 (IP: {peer_ip})")
            await hub.set_state("idle", "等待设备上线")
            await hub.broadcast(hub.get_stats_dict())

    return ws


async def dashboard_ws_handler(request: web.Request):
    """处理 Web 控制台网页的实时状态广播长连接"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    await hub.register_dashboard(ws)
    await hub.broadcast(hub.get_stats_dict())

    try:
        async for msg in ws:
            pass  # 控制台无需发送上行，仅接收广播
    finally:
        hub.unregister_dashboard(ws)
        await hub.broadcast(hub.get_stats_dict())

    return ws


async def api_status_handler(request: web.Request):
    """REST API: 返回当前全量监控状态 JSON"""
    return web.json_response(hub.get_stats_dict())


async def api_test_prompt_handler(request: web.Request):
    """REST API: 发送测试文本至 Gemini Live 并向设备推流"""
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "Empty text"}, status=400)

        session = hub.active_device_session
        if not session:
            return web.json_response({"ok": False, "error": "No active device session connected"}, status=503)

        await session.handle_text_command(json.dumps({"type": "text", "text": text}))
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def api_test_timeout_handler(request: web.Request):
    """REST API: 测试向设备播发网络超时提示音"""
    session = hub.active_device_session
    if not session:
        return web.json_response({"ok": False, "error": "No device connected"}, status=503)

    asyncio.create_task(session.play_timeout_prompt())
    return web.json_response({"ok": True})


# ======================================================================
# 服务启动主入口
# ======================================================================

async def history_page(request):
    if request.remote not in ("127.0.0.1", "::1"):
        raise web.HTTPForbidden(text="History is available on this Mac only")
    return web.FileResponse(STATIC_DIR / "history.html", headers={"Cache-Control":"no-store"})

async def history_api(request):
    if request.remote not in ("127.0.0.1", "::1"):
        raise web.HTTPForbidden()
    session = hub.active_device_session
    store = conversation_store()
    if request.method == "GET":
        cid = request.query.get("id")
        if cid:
            if not any(row['id']==cid for row in store.list()):
                raise web.HTTPNotFound()
            fmt=request.query.get("export")
            if fmt in ("json","md"):
                return web.Response(text=store.export(cid,fmt=="md"),content_type="application/json" if fmt=="json" else "text/markdown",
                    headers={"Content-Disposition":f'attachment; filename="conversation-{cid}.{fmt}"',"Cache-Control":"no-store"})
            return web.json_response(dict(turns=store.turns(cid,request.query.get("q",""))),headers={"Cache-Control":"no-store"})
        return web.json_response(dict(conversations=store.list(),current=session.conversation_id if session else None),headers={"Cache-Control":"no-store"})
    origin=request.headers.get("Origin")
    if origin and origin != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden()
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType()
    data=await request.json()
    action,cid=data.get("action"),data.get("id")
    if action in ("new","select"):
        if not session:raise web.HTTPConflict(text="设备尚未连接")
        if session.busy:raise web.HTTPConflict(text="请先完成或取消当前对话")
        if action=="select" and not any(r['id']==cid and r['device']==session.device_id for r in store.list()):
            raise web.HTTPNotFound()
        cid=await session.switch_conversation(cid if action=="select" else None)
    elif action in ("delete","clear"):
        if action=="delete" and not any(r['id']==cid for r in store.list()):raise web.HTTPNotFound()
        if session:
            await session.delete_history(cid if action=="delete" else None)
        else:
            store.delete(cid if action=="delete" else None)
    else:raise web.HTTPBadRequest(text="未知操作")
    await hub.broadcast(dict(type="history_changed"))
    return web.json_response(dict(ok=True,current=session.conversation_id if session else None))


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/ws/device", device_ws_handler)
    app.router.add_get("/ws/ble-bridge", ble_bridge_handler)
    app.router.add_get("/api/settings", api_settings_handler)
    app.router.add_post("/api/settings", api_settings_handler)
    app.router.add_post("/api/volume", api_volume_handler)
    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    app.router.add_get("/ws/dashboard", dashboard_ws_handler)
    app.router.add_get("/api/status", api_status_handler)
    app.router.add_get("/history", history_page)
    app.router.add_get("/api/conversations", history_api)
    app.router.add_post("/api/conversations", history_api)
    app.router.add_post("/api/test_prompt", api_test_prompt_handler)
    app.router.add_post("/api/test_timeout", api_test_timeout_handler)
    return app


async def main():
    local_ip = get_local_ip()
    logger.info("=" * 65)
    logger.info("🚀 Starting talk2gemi Mac Relay & Live Web Monitor Server...")
    logger.info(f"🌐 Web 监控控制台: http://{local_ip}:{SERVER_PORT} 或 http://localhost:{SERVER_PORT}")
    logger.info(f"🔌 硬件连接地址: ws://{local_ip}:{SERVER_PORT}")
    logger.info(f"🧠 Gemini 模型: {LIVE_MODEL}")
    logger.info("=" * 65)

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SERVER_HOST, SERVER_PORT)
    await site.start()

    # 维持运行
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server terminated by user.")
