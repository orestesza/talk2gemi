"""Local IPC to native CoreBluetooth helper. All audio uses the common relay."""
import asyncio
import base64
import json
import secrets
import logging
from aiohttp import web, WSMsgType
from opus_downlink import OpusEncoder, FRAME_BYTES, LIBRARY
from ble_protocol import encode, decode, frame, unframe


class BLELink:
    def __init__(self, broker, peer):
        self.broker, self.peer = broker, peer
        self.seq, self.turn = 0, 0
        self.session = None
        self.ack = {}
        self.expected = None
        self.device_id = None
        self.closed = False
        self.send_lock = asyncio.Lock()
        self.audio_pending = []
        self.audio_lock = asyncio.Lock()
        self.opus_supported = False
        self.current_codec = "adpcm"
        self.opus_encoder = None
        self.opus_pending = bytearray()

    async def send_frame(self, kind, payload):
        # Serialize frame submission, not the receipt wait. send_bytes limits
        # audio to three outstanding frames; control still waits for delivery.
        future = asyncio.get_running_loop().create_future()
        async with self.send_lock:
            if self.closed:
                raise ConnectionError("BLE link closed")
            self.seq = (self.seq + 1) & 65535
            number = self.seq
            packet = frame(kind, self.turn, number, payload)
            self.ack[number] = future
            try:
                await self.broker.send(dict(event="send", peer=self.peer, data=base64.b64encode(packet).decode()))
            except BaseException:
                self.ack.pop(number, None)
                raise
        try:
            await asyncio.wait_for(future, 4)
        finally:
            self.ack.pop(number, None)

    async def drain_audio(self):
        pending, self.audio_pending = self.audio_pending, []
        if pending:
            try:
                await asyncio.gather(*pending)
            finally:
                for task in pending:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

    async def send_str(self, text):
        async with self.audio_lock:
            command = json.loads(text)
            if command.get("type")=="state" and command.get("value") in ("ready","idle","error"):
                await self.finish_opus()
            await self.drain_audio()
            await self.send_frame(1, text.encode())

    async def queue_audio(self, kind, payload):
        if len(self.audio_pending) >= 3:
            await self.audio_pending.pop(0)
        self.audio_pending.append(asyncio.create_task(self.send_frame(kind,payload)))
        await asyncio.sleep(0)

    def reset_opus(self):
        if self.opus_encoder:
            self.opus_encoder.close()
        self.opus_encoder=None
        self.opus_pending.clear()

    async def finish_opus(self):
        if self.opus_encoder:
            # Preserve encoder lookahead at the tail; do not cut the last word.
            self.opus_pending.extend(bytes(self.opus_encoder.lookahead*2))
            while self.opus_pending:
                block=bytes(self.opus_pending[:FRAME_BYTES]);del self.opus_pending[:FRAME_BYTES]
                await self.queue_audio(5,self.opus_encoder.encode(block.ljust(FRAME_BYTES,b'\0')))
            await self.drain_audio()
            self.reset_opus()

    def update_codec(self):
        preference=self.broker.settings.data.get("downlink_codec","opus") if hasattr(self.broker,"settings") else "adpcm"
        self.current_codec="opus" if self.opus_supported and preference=="opus" and LIBRARY.is_file() else "adpcm"
        return self.current_codec

    async def send_bytes(self, pcm):
        async with self.audio_lock:
            self.update_codec()
            if self.current_codec=="opus":
                if self.opus_encoder is None:self.opus_encoder=OpusEncoder()
                self.opus_pending.extend(pcm)
                while len(self.opus_pending)>=FRAME_BYTES:
                    block=bytes(self.opus_pending[:FRAME_BYTES]);del self.opus_pending[:FRAME_BYTES]
                    await self.queue_audio(5,self.opus_encoder.encode(block))
            else:
                for offset in range(0,len(pcm),1024):
                    await self.queue_audio(2,encode(pcm[offset:offset+1024],quality=True))

    async def abort_audio(self):
        async with self.audio_lock:
            self.reset_opus()
            pending, self.audio_pending = self.audio_pending, []
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def close(self):
        self.closed = True
        self.reset_opus()
        pending, self.audio_pending = self.audio_pending, []
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for future in self.ack.values():
            if not future.done():
                future.set_exception(ConnectionError("BLE link closed"))
        await self.broker.send(dict(event="disconnect", peer=self.peer))

    async def receive_frame(self, raw):
        kind, turn, seq, payload = unframe(raw)
        if kind == 3:
            future = self.ack.get(seq)
            if future and not future.done():
                future.set_result(None)
            return None
        if self.expected is not None and seq != self.expected:
            raise ValueError("BLE frame missing or duplicated; repeat the question")
        self.expected = (seq + 1) & 65535
        # Receipt ACK is separate from start/stop semantic responses.
        if kind != 4:
            await self.broker.send(dict(event="send", peer=self.peer,
                                       data=base64.b64encode(frame(3, turn, seq, b"")).decode()))
        return (kind, turn, seq, payload)

    async def dispatch_frame(self, kind, turn, seq, payload):
        self.turn = turn
        if kind == 1:
            cmd = json.loads(payload)
            if cmd.get("type") == "hello":
                self.opus_supported = cmd.get("downlink_opus") is True
                self.update_codec()
                self.device_id = cmd["device_id"]
                saved = self.broker.settings.data["paired"].get(self.device_id)
                if saved and secrets.compare_digest(saved, cmd.get("token", "")):
                    await self.authorize(saved)
                else:
                    code = f"{secrets.randbelow(1000000):06d}"
                    self.broker.pending[self.peer] = (self, code)
                    # Send without awaiting ACK in this receive task, to avoid deadlock.
                    asyncio.create_task(self.send_str(json.dumps(dict(type="pair_code", code=code))))
                    await self.broker.send(dict(event="pairing", peer=self.peer, code=code, device=self.device_id))
            elif self.session:
                if cmd.get("type") == "activate":
                    await self.broker.activate(self.session, self, "ble")
                elif cmd.get("type") == "forget":
                    self.broker.settings.data["paired"].pop(self.device_id, None)
                    self.broker.settings.save()
                    await self.close()
                elif self.session.ws is self:
                    await self.session.handle_text_command(payload.decode())
                    if cmd.get("type") in ("start", "stop"):
                        await self.send_str(json.dumps(dict(type=cmd["type"] + "_ack", turn=turn)))
        elif kind in (2, 4) and self.session and self.session.ws is self:
            await self.session.handle_binary_audio(decode(payload))

    async def incoming(self, raw):
        parsed = await self.receive_frame(raw)
        if parsed is not None:
            await self.dispatch_frame(*parsed)

    async def authorize(self, token):
        await self.send_str(json.dumps(dict(type="paired", token=token,
                                           host_id=self.broker.settings.data["host_id"],
                                           name=self.broker.settings.data["name"])))
        self.session = await self.broker.attach(self, self.device_id, "ble", "Bluetooth")


class BLEBroker:
    def __init__(self, settings, attach, activate, detach, credentials=None):
        self.settings, self.attach, self.activate, self.detach = settings, attach, activate, detach
        self.credentials = credentials
        self.ws = None
        self.links, self.pending = {}, {}
        self.tasks = set()

    async def send(self, event):
        if not self.ws or self.ws.closed:
            raise ConnectionError("Mac Bluetooth helper is not running")
        await self.ws.send_json(event)

    async def notify(self, title, message):
        if self.ws and not self.ws.closed:
            await self.send(dict(event="notification", title=title, message=message))

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def handler(self, request):
        if self.ws and not self.ws.closed:
            raise web.HTTPConflict(text="A Bluetooth helper is already connected")
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=8192)
        await ws.prepare(request)
        self.ws = ws
        await self.send(dict(event="identity", host_id=self.settings.data["host_id"], name=self.settings.data["name"],
                             api_configured=bool(self.credentials and self.credentials.configured)))
        # Separate GATT receipt/ACK dispatch from ordered command processing.
        queue = asyncio.Queue(maxsize=1024)  # bounded: full 30s PTT while reconnecting

        async def process():
            while True:
                link, parsed = await queue.get()
                try:
                    await link.dispatch_frame(*parsed)
                except ValueError as exc:
                    # Invalid framing/sequence is a transport error.
                    logging.warning("BLE protocol rejected: %s", exc)
                    await self.send(dict(event="error", message=str(exc), peer=link.peer))
                    await self.drop(link.peer)
                except Exception as exc:
                    # A cloud timeout is not a radio failure. Keep the paired
                    # device connected and make the failed request visible.
                    logging.warning("BLE command/cloud failure (%s): %s", type(exc).__name__, exc)
                    if link.session:
                        link.session.cloud_failure(exc)
                        await link.session.record_change(status="error",error="云端请求失败："+type(exc).__name__)
                        await link.session.send_json(dict(type="state",value="error",reason="Cloud unavailable. Please repeat."))

        worker = asyncio.create_task(process())
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                event = json.loads(msg.data)
                peer = event.get("peer")
                if event["event"] == "credentials" and self.credentials:
                    self.credentials.from_keychain(event.get("key"))
                    await self.send(dict(event="credentials_saved"))
                elif event["event"] == "packet":
                    link = self.links.setdefault(peer, BLELink(self, peer))
                    try:
                        raw = base64.b64decode(event["data"], validate=True)
                        parsed = await link.receive_frame(raw)
                    except ValueError as exc:
                        logging.warning("BLE protocol rejected: %s", exc)
                        await self.send(dict(event="error", message=str(exc), peer=link.peer))
                        await self.drop(link.peer)
                        continue
                    if parsed is not None:
                        queue.put_nowait((link, parsed))
                elif event["event"] == "approve" and peer in self.pending:
                    link, code = self.pending.pop(peer)
                    if event.get("code") == code:
                        token = secrets.token_urlsafe(32)
                        self.settings.data["paired"][link.device_id] = token
                        self.settings.save()
                        self.spawn(link.authorize(token))
                elif event["event"] == "disconnected":
                    await self.drop(peer)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            for peer in list(self.links):
                await self.drop(peer)
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.ws = None
        return ws

    async def drop(self, peer):
        self.pending.pop(peer, None)
        link = self.links.pop(peer, None)
        if link:
            if link.session:
                await self.detach(link.session, link, "ble")
            try:
                await link.close()
            except ConnectionError:
                pass
