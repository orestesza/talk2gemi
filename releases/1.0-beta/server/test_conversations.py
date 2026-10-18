import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from conversations import Conversations
import relay_server as relay
from ble_protocol import encode,decode
import math,struct

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Conversations(Path(self.tmp.name)/'history.sqlite3')
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def test_restart_current_and_full_text_excludes_failed_context(self):
        cid=self.store.current('device');self.store.begin(cid,'a','前一个问题')
        self.store.update('a',assistant='前一个回答',status='completed')
        self.store.begin(cid,'b','未回答的问题');self.store.update('b',status='timeout')
        reopened=Conversations(self.store.path)
        self.assertEqual(reopened.current('device'),cid)
        self.assertEqual(len(reopened.turns(cid)),2)
        self.assertNotIn('未回答的问题',str(reopened.context(cid)))
        self.assertEqual(len(reopened.context(cid)),2);reopened.db.close()
    def test_switch_isolation_delete_and_handle_not_exported(self):
        a=self.store.new('d');b=self.store.new('d')
        self.store.begin(a,'a','hello');self.store.handle(a,'secret-handle')
        self.store.select('d',a);self.assertEqual(self.store.current('d'),a)
        self.assertNotIn('secret-handle',self.store.export(a))
        with self.assertRaises(ValueError):self.store.select('other',a)
        self.store.delete(a);self.assertEqual(self.store.turns(a),[])
        self.assertIsNone(self.store.handle(a));self.assertEqual(self.store.context(b),[])
    def test_single_record_retry_and_search(self):
        a=self.store.new('d');self.store.begin(a,'t','测试问题')
        self.store.update('t',status='retrying',retries=1)
        self.store.update('t',status='completed',assistant='答案')
        self.assertEqual(len(self.store.turns(a)),1)
        self.assertEqual(len(self.store.turns(a,'答案')),1)
        self.assertIn('重试：1',self.store.export(a,True))
    def test_downlink_index_optimization_reduces_attack_error(self):
        pcm=struct.pack('<512h',*(int(9000*math.sin(i*.25)) for i in range(512)))
        plain=struct.unpack('<512h',decode(encode(pcm)))
        improved=struct.unpack('<512h',decode(encode(pcm,quality=True)))
        target=struct.unpack('<512h',pcm)
        self.assertLess(sum((a-b)**2 for a,b in zip(target,improved)),sum((a-b)**2 for a,b in zip(target,plain)))

class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=patch.object(relay,'STATE_DIR',Path(self.tmp.name));self.path.start()
        self.hub=patch.object(relay,'hub',relay.MonitorHub());self.hub.start()
        self.session=relay.DeviceSession(SimpleNamespace(send_str=AsyncMock(),send_bytes=AsyncMock(),abort_audio=AsyncMock()),'virtual')
        self.session.ensure_memory();self.session.turn_id='t';relay.conversation_store().begin(self.session.conversation_id,'t','问题')
        self.session.busy=True;self.session.current_recording.extend(bytes(2048))
        self.live=SimpleNamespace(send_realtime_input=AsyncMock(),send_client_content=AsyncMock())
        self.session.live_session=self.live
        self.session.connect_gemini_live=AsyncMock()
    async def asyncTearDown(self):
        await self.session.close();self.hub.stop();self.path.stop();self.tmp.cleanup()
    async def test_timeout_replays_once_then_reports_failure(self):
        await self.session._thinking_timeout_watchdog(.01)
        self.session.thinking_timer.cancel();await asyncio.gather(self.session.thinking_timer,return_exceptions=True)
        self.assertEqual(self.session.retry_count,1)
        self.assertEqual(self.live.send_realtime_input.await_count,4) # start, two PCM, end
        await self.session._thinking_timeout_watchdog(.01)
        self.session.connect_gemini_live.assert_awaited_once()
        row=relay.conversation_store().turns(self.session.conversation_id)[0]
        self.assertEqual(row['status'],'timeout');self.assertEqual(row['retries'],1)
        self.assertFalse(self.session.current_recording)
    async def test_first_audio_prevents_retry(self):
        self.session.reply_started=True
        await self.session._thinking_timeout_watchdog(.01)
        self.session.connect_gemini_live.assert_not_awaited()
    async def test_cancel_prevents_late_retry_and_marks_record(self):
        task=asyncio.create_task(self.session._thinking_timeout_watchdog(.03))
        self.session.thinking_timer=task
        await self.session.cancel_turn()
        await asyncio.sleep(.04)
        self.session.connect_gemini_live.assert_not_awaited()
        self.assertEqual(relay.conversation_store().turns(self.session.conversation_id)[0]['status'],'cancelled')
        self.session.ws.abort_audio.assert_awaited_once()

    async def test_cancel_during_reconnect_never_replays_old_audio(self):
        entered=asyncio.Event()
        async def blocked_connect(**kwargs):
            entered.set()
            await asyncio.sleep(3600)
        self.session.connect_gemini_live=blocked_connect
        task=asyncio.create_task(self.session._thinking_timeout_watchdog(.001))
        self.session.thinking_timer=task
        await asyncio.wait_for(entered.wait(),1)
        await self.session.cancel_turn()
        self.live.send_realtime_input.assert_not_awaited()
        self.assertTrue(task.cancelled())

    async def test_stale_receiver_cannot_publish_new_turn_transcript(self):
        messages=asyncio.Queue()
        async def receive():
            yield await messages.get()
        self.live.receive=receive
        self.session.is_gemini_alive=lambda:True
        task=asyncio.create_task(self.session._gemini_receive_loop())
        await asyncio.sleep(0)
        self.session.epoch+=1
        sc=SimpleNamespace(input_transcription=SimpleNamespace(text='旧轮次'),output_transcription=None,model_turn=None,turn_complete=False,interrupted=False)
        await messages.put(SimpleNamespace(server_content=sc))
        await asyncio.wait_for(task,1)
        self.assertEqual(self.session.user_text,'')
