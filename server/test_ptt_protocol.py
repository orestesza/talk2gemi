"""Regression checks for PTT boundaries and missing-reply timeout behavior."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import relay_server as relay


class FakeLive:
    def __init__(self):
        self.calls = []
        self.messages = asyncio.Queue()

    async def send_realtime_input(self, **kwargs):
        self.calls.append(kwargs)

    async def send_client_content(self, **kwargs):
        self.calls.append({'client_content': kwargs})

    async def receive(self):
        while True:
            yield await self.messages.get()


class PttProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=relay.BASE_DIR / 'scratch')
        self.path_patch = patch.object(relay, 'BASE_DIR', Path(self.tmp.name))
        self.path_patch.start()
        self.state_patch = patch.object(relay, 'STATE_DIR', Path(self.tmp.name)/'.runtime')
        self.state_patch.start()
        self.hub_patch = patch.object(relay, 'hub', relay.MonitorHub())
        self.hub_patch.start()
        self.ws = SimpleNamespace(send_str=AsyncMock(), send_bytes=AsyncMock())
        self.session = relay.DeviceSession(self.ws, 'test')
        self.session.voice = relay.settings.data["voice"]
        self.session.key_revision = relay.credentials.revision
        self.live = FakeLive()
        self.session.live_session = self.live
        self.session.is_gemini_alive = lambda: True
        self.session.receive_task = asyncio.create_task(asyncio.sleep(3600))

    async def asyncTearDown(self):
        await self.session.close()
        self.hub_patch.stop()
        self.state_patch.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    async def command(self, kind, **kwargs):
        await self.session.handle_text_command(json.dumps(dict(type=kind, **kwargs)))

    async def test_ptt_boundaries_and_late_dma_tail(self):
        await self.session.handle_binary_audio(b'\x01\x00' * 512)
        for _ in range(2):
            await self.command('start')
            await self.command('start')  # switch bounce must not nest activities
            await self.session.handle_binary_audio(b'\x01\x01' * 512)
            await self.command('stop')
            await self.command('stop')
            await self.session.handle_binary_audio(b'\0' * 512)
            self.session.busy = False  # Fake server turn_complete between conversations.
        self.assertEqual([next(iter(x)) for x in self.live.calls],
                         ['activity_start', 'audio', 'activity_end'] * 2)
        self.assertEqual(len(self.session.current_recording), 1024)

    async def test_text_uses_complete_user_content(self):
        await self.command('text', text='测试')
        self.assertEqual([next(iter(x)) for x in self.live.calls],
                         ['client_content'])
        content = self.live.calls[0]['client_content']
        self.assertTrue(content['turn_complete'])
        self.assertEqual(content['turns'].role, 'user')

    async def test_new_turn_cancels_thinking(self):
        await self.command('text', text='first')
        with patch.object(self.session, 'connect_gemini_live', AsyncMock()):
            await self.command('start')
        self.assertTrue(self.session.recording_active)
        self.assertEqual(self.session.retry_count, 0)

    async def test_text_cannot_overlap_recording(self):
        await self.command('start')
        with self.assertRaises(ValueError):
            await self.command('text', text='测试')

    async def start_receive(self):
        self.session.receive_task.cancel()
        await asyncio.gather(self.session.receive_task, return_exceptions=True)
        self.session.receive_task = asyncio.create_task(self.session._gemini_receive_loop())
        self.session.thinking_timer = asyncio.create_task(asyncio.sleep(3600))

    async def message(self, **kwargs):
        sc = dict(input_transcription=None, output_transcription=None,
                  model_turn=None, turn_complete=False, interrupted=False)
        sc.update(kwargs)
        await self.live.messages.put(SimpleNamespace(server_content=SimpleNamespace(**sc)))
        await asyncio.sleep(.03)

    async def test_input_transcript_does_not_disable_reply_watchdog(self):
        await self.start_receive()
        await self.message(input_transcription=SimpleNamespace(text='你好'))
        self.assertFalse(self.session.thinking_timer.done())
        part = SimpleNamespace(inline_data=SimpleNamespace(data=b'\0' * 480))
        await self.message(model_turn=SimpleNamespace(parts=[part]))
        self.assertTrue(self.session.thinking_timer.cancelled())
        self.ws.send_bytes.assert_awaited()

    async def test_interrupted_previous_turn_keeps_new_recording_state(self):
        await self.start_receive()
        self.session.recording_active = True
        await relay.hub.set_state('listening')
        await self.message(interrupted=True, turn_complete=True)
        self.assertEqual(relay.hub.current_state, 'listening')

    async def test_old_session_close_keeps_reconnected_device_online(self):
        relay.hub.active_device_session = object()
        relay.hub.gemini_connected = True
        await self.session.close()
        self.assertTrue(relay.hub.gemini_connected)


if __name__ == '__main__':
    unittest.main()
