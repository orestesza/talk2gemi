import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiohttp import ClientSession, web
import relay_server as relay
from ble_protocol import frame, unframe, encode

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=relay.BASE_DIR/'scratch')
        self.patches = [patch.object(relay,'BASE_DIR',Path(self.tmp.name)),
                        patch.object(relay,'STATE_DIR',Path(self.tmp.name)/'.runtime'),
                        patch.object(relay,'settings',relay.Settings(Path(self.tmp.name)/'settings.json')),
                        patch.object(relay,'hub',relay.MonitorHub()),patch.object(relay,'sessions',{})]
        for p in self.patches:p.start()
        self.fake_live = SimpleNamespace(send_client_content=AsyncMock(),send_realtime_input=AsyncMock())
        async def connect(session, force=False):
            session.voice=relay.settings.data['voice'];session.key_revision=relay.credentials.revision;session.live_session=self.fake_live
        self.connect_patch=patch.object(relay.DeviceSession,'connect_gemini_live',connect)
        self.alive_patch=patch.object(relay.DeviceSession,'is_gemini_alive',lambda s:s.live_session is not None)
        self.connect_patch.start();self.alive_patch.start()
        self.runner=web.AppRunner(relay.create_app());await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
        self.client=ClientSession()
    async def asyncTearDown(self):
        await self.client.close();await self.runner.cleanup()
        self.connect_patch.stop();self.alive_patch.stop()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    async def test_same_device_has_one_session_and_no_transport_steal(self):
        first=await self.client.ws_connect(self.url+'/ws/device?id=device123')
        hello=await first.receive_json();self.assertEqual(hello['type'],'hello')
        await first.send_json(dict(type='activate'))
        while (await first.receive_json()).get('type')!='state':pass
        session=relay.sessions['device123'];session.busy=True
        second=SimpleNamespace(close=AsyncMock(),send_str=AsyncMock())
        await relay.attach_device(second,'device123','ble','BLE')
        self.assertIs(relay.sessions['device123'],session)
        with self.assertRaises(ValueError):await relay.activate_device(session,second,'ble')
        self.assertIs(session.ws,session.links['wifi'])
        session.busy=False
        await relay.activate_device(session,second,'ble')
        self.assertEqual(session.transport,'ble')
        await first.close()
        await relay.detach_device(session,second,'ble')
    async def test_socket_replacement_keeps_logical_session_alive(self):
        old=SimpleNamespace(send_str=AsyncMock(),close=AsyncMock())
        session=await relay.attach_device(old,'device123','wifi','test')
        async def close_old():await relay.detach_device(session,old,'wifi')
        old.close=close_old
        new=SimpleNamespace(send_str=AsyncMock(),close=AsyncMock())
        result=await relay.attach_device(new,'device123','wifi','test')
        self.assertIs(result,session)
        self.assertTrue(session.is_active)
        self.assertIs(session.links['wifi'],new)
        await relay.detach_device(session,new,'wifi')

    async def test_wrong_host_rejected_and_settings_validate(self):
        ws=await self.client.ws_connect(self.url+'/ws/device?id=device123&host=wrong')
        self.assertEqual((await ws.receive_json())['type'],'error');await ws.close()
        for voice in relay.VOICES:
            async with self.client.post(self.url+'/api/settings',json=dict(voice=voice)) as response:
                self.assertEqual(response.status,200);self.assertEqual((await response.json())['voice'],voice)
        async with self.client.post(self.url+'/api/settings',json=dict(voice='bad')) as response:self.assertEqual(response.status,400)
        async with self.client.post(self.url+'/api/volume',json=dict(volume=101)) as response:self.assertEqual(response.status,400)
    async def test_ble_pairing_and_frame_ack_do_not_deadlock(self):
        token=self.runner.app['bridge_token']
        ws=await self.client.ws_connect(self.url+'/ws/ble-bridge',headers={'Authorization':'Bearer '+token})
        self.assertEqual((await ws.receive_json())['event'],'identity')
        peer='virtual-radio';sequence=0;messages=asyncio.Queue();commands=asyncio.Queue()
        async def packet(payload,kind=1,seq=None):
            nonlocal sequence
            if seq is None:sequence+=1;seq=sequence
            data=frame(kind,1,seq,payload)
            await ws.send_json(dict(event='packet',peer=peer,data=base64.b64encode(data).decode()))
        async def receive():
            async for msg in ws:
                event=json.loads(msg.data)
                if event['event']=='pairing':
                    await ws.send_json(dict(event='approve',peer=peer,code=event['code']))
                elif event['event']=='send':
                    kind,turn,seq,payload=unframe(base64.b64decode(event['data']))
                    if kind!=3:
                        await packet(b'',3,seq)
                        if kind==1:
                            command=json.loads(payload);await messages.put(command)
                elif event['event']=='error':await messages.put(event)
        reader=asyncio.create_task(receive())
        await packet(json.dumps(dict(type='hello',device_id='device123',token='')).encode())
        async def until(kind):
            while True:
                event=await asyncio.wait_for(messages.get(),5)
                if event.get('type')==kind or event.get('event')=='error':return event
        self.assertEqual((await until('paired'))['type'],'paired')
        self.assertEqual((await until('hello'))['type'],'hello')
        await packet(b'{"type":"activate"}')
        self.assertEqual((await until('state'))['value'],'ready')
        self.assertEqual(relay.sessions['device123'].transport,'ble')
        await ws.close();await reader

    async def test_history_http_persistence_export_and_origin_guard(self):
        store=relay.conversation_store();cid=store.new('device123')
        store.begin(cid,'first','用户问题');store.update('first',assistant='完整回答',status='completed')
        async with self.client.get(self.url+'/api/conversations') as response:
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['conversations'][0]['id'],cid)
        async with self.client.get(self.url+'/api/conversations?id='+cid+'&q=回答') as response:
            self.assertEqual(len((await response.json())['turns']),1)
        async with self.client.get(self.url+'/api/conversations?id='+cid+'&export=md') as response:
            self.assertIn('完整回答',await response.text())
        async with self.client.post(self.url+'/api/conversations',json=dict(action='delete',id=cid),headers={'Origin':'https://untrusted.example'}) as response:
            self.assertEqual(response.status,403)
        async with self.client.post(self.url+'/api/conversations',json=dict(action='delete',id=cid)) as response:
            self.assertEqual(response.status,200)
        self.assertEqual(store.turns(cid),[])

    async def test_history_new_and_select_keep_device_context_separate(self):
        ws=SimpleNamespace(send_str=AsyncMock(),close=AsyncMock(),abort_audio=AsyncMock())
        session=await relay.attach_device(ws,'device123','ble','test')
        relay.hub.active_device_session=session
        async with self.client.post(self.url+'/api/conversations',json=dict(action='new')) as response:
            self.assertEqual(response.status,200);first=(await response.json())['current']
        async with self.client.post(self.url+'/api/conversations',json=dict(action='new')) as response:
            second=(await response.json())['current'];self.assertNotEqual(first,second)
        async with self.client.post(self.url+'/api/conversations',json=dict(action='select',id=first)) as response:
            self.assertEqual((await response.json())['current'],first)
        session.busy=True
        async with self.client.post(self.url+'/api/conversations',json=dict(action='clear')) as response:
            self.assertEqual(response.status,409)
        session.busy=False
        await relay.detach_device(session,ws,'ble')

    async def test_cloud_timeout_does_not_drop_ble_link(self):
        from ble_bridge import BLELink
        from unittest.mock import Mock
        token=self.runner.app['bridge_token']
        ws=await self.client.ws_connect(self.url+'/ws/ble-bridge',headers={'Authorization':'Bearer '+token})
        await ws.receive_json()
        link=BLELink(relay.ble_broker,'timeout-peer')
        session=SimpleNamespace(ws=link,handle_text_command=AsyncMock(side_effect=TimeoutError()),cloud_failure=Mock(),record_change=AsyncMock(),send_json=AsyncMock())
        link.session=session;relay.ble_broker.links[link.peer]=link
        await ws.send_json(dict(event='packet',peer=link.peer,data=base64.b64encode(frame(1,1,1,b'{"type":"start"}')).decode()))
        await asyncio.sleep(.05)
        self.assertIs(relay.ble_broker.links[link.peer],link)
        session.cloud_failure.assert_called_once()
        session.send_json.assert_awaited()
        link.session=None
        await ws.close()

    async def test_voice_switch_clears_handle_and_reconnects_async_and_rapid_switches_converge(self):
        ws=SimpleNamespace(send_str=AsyncMock(),close=AsyncMock(),abort_audio=AsyncMock())
        session=await relay.attach_device(ws,'device123','ble','test')
        await relay.activate_device(session,ws,'ble')
        session.ensure_memory()
        store=relay.conversation_store()
        store.begin(session.conversation_id,'t1','你好')
        store.update('t1',assistant='你好！',status='completed')
        store.handle(session.conversation_id,'old-voice-handle')
        self.assertEqual(store.handle(session.conversation_id),'old-voice-handle')

        connected_voices=[]
        async def track_connect(s, force=False):
            await asyncio.sleep(0.02)
            s.voice=relay.settings.data['voice']
            s.key_revision=relay.credentials.revision
            s.live_session=self.fake_live
            connected_voices.append(s.voice)
        with patch.object(relay.DeviceSession,'connect_gemini_live',track_connect):
            # Rapid consecutive voice switches while idle
            for v in ('Aoede','Kore','Laomedeia'):
                async with self.client.post(self.url+'/api/settings',json=dict(voice=v)) as resp:
                    self.assertEqual(resp.status,200)
            await asyncio.wait_for(session.voice_switch_task,1.0)
            self.assertEqual(session.voice,'Laomedeia')
            self.assertEqual(connected_voices[-1],'Laomedeia')
            self.assertIsNone(store.handle(session.conversation_id))
        await relay.detach_device(session,ws,'ble')

    async def test_voice_switch_during_playback_ignores_old_handle_and_switches_on_complete(self):
        ws=SimpleNamespace(send_str=AsyncMock(),send_bytes=AsyncMock(),close=AsyncMock(),abort_audio=AsyncMock())
        session=await relay.attach_device(ws,'device123','ble','test')
        await relay.activate_device(session,ws,'ble')
        session.ensure_memory()
        store=relay.conversation_store()
        session.turn_id='turn-play'
        store.begin(session.conversation_id,session.turn_id,'讲个笑话')
        session.busy=True
        session.reply_started=True

        # Switch voice while busy (playing)
        async with self.client.post(self.url+'/api/settings',json=dict(voice='Kore')) as resp:
            data=await resp.json()
            self.assertTrue(data['pending'])

        # Simulate old voice session emitting a resumption handle and completing turn
        q=asyncio.Queue()
        async def receive():
            yield await q.get()
            session.epoch+=1
        self.fake_live.receive=receive
        await q.put(SimpleNamespace(
            session_resumption_update=SimpleNamespace(resumable=True,new_handle='stale-old-voice-handle'),
            go_away=None,
            server_content=SimpleNamespace(
                input_transcription=None,output_transcription=SimpleNamespace(text='哈哈'),
                model_turn=None,turn_complete=True,interrupted=False
            )
        ))
        await session._gemini_receive_loop()
        # Old handle must NOT be saved when voice changed mid-turn
        self.assertIsNone(store.handle(session.conversation_id))
        if session.voice_switch_task:
            await asyncio.wait_for(session.voice_switch_task,1.0)
        self.assertEqual(session.voice,'Kore')
        await relay.detach_device(session,ws,'ble')

    async def test_ble_receipt_ack_not_blocked_by_slow_start_and_failed_connect_cleans_recording(self):
        from ble_bridge import BLELink
        token=self.runner.app['bridge_token']
        ws=await self.client.ws_connect(self.url+'/ws/ble-bridge',headers={'Authorization':'Bearer '+token})
        await ws.receive_json()
        peer='fast-ptt-peer'
        link=BLELink(relay.ble_broker,peer)
        link.opus_supported=True
        link.update_codec()
        self.assertEqual(link.current_codec,'opus')

        device_ws=SimpleNamespace(send_str=AsyncMock(),close=AsyncMock(),abort_audio=AsyncMock())
        session=await relay.attach_device(device_ws,'device123','ble','BLE')
        await relay.activate_device(session,link,'ble')
        link.session=session
        relay.ble_broker.links[peer]=link

        gate=asyncio.Event()
        async def slow_connect(s, force=False):
            await gate.wait()
            raise TimeoutError('Cloud connect failed')

        with patch.object(relay.DeviceSession,'connect_gemini_live',slow_connect):
            session.force_fresh=True
            # Send start (seq=1) and stop (seq=2) while start is blocked in slow_connect
            await ws.send_json(dict(event='packet',peer=peer,data=base64.b64encode(frame(1,1,1,b'{"type":"start"}')).decode()))
            await ws.send_json(dict(event='packet',peer=peer,data=base64.b64encode(frame(1,1,2,b'{"type":"stop"}')).decode()))
            # Both receipt ACKs (kind=3, seq=1 and seq=2) must arrive immediately before gate is released!
            acks=[]
            while len(acks)<2:
                msg=await asyncio.wait_for(ws.receive_json(),0.5)
                if msg.get('event')=='send':
                    k,t,s,_=unframe(base64.b64decode(msg['data']))
                    if k==3:acks.append(s)
            self.assertEqual(acks,[1,2])
            gate.set()
            await asyncio.sleep(0.05)

        # Turn record in DB must NOT be left in 'recording' status
        turns=relay.conversation_store().turns(session.conversation_id)
        self.assertEqual(len(turns),1)
        self.assertEqual(turns[0]['status'],'error')
        link.session=None
        await relay.detach_device(session,device_ws,'ble')
        await ws.close()

    async def test_dashboard_html_layout_and_formal_opus_display(self):
        html=relay.DASHBOARD_HTML_PATH.read_text(encoding='utf-8')
        self.assertNotIn('Opus 语音音质测试',html)
        self.assertNotIn('ADPCM 对照',html)
        self.assertNotIn('position:fixed;right:18px;top:12px',html)
        # History link is placed right next to the live subtitle title
        idx_title=html.index('实时双向对话字幕')
        idx_hist=html.index('href="/history"')
        self.assertLess(abs(idx_hist-idx_title),250)

    async def test_long_audio_playback_decoupled_and_turn_completes(self):
        chunks_sent = []
        async def mock_send_bytes(data):
            chunks_sent.append(data)
            await asyncio.sleep(0.002)

        ws = SimpleNamespace(
            send_str=AsyncMock(),
            send_bytes=mock_send_bytes,
            close=AsyncMock(),
            abort_audio=AsyncMock(),
            drain_audio=AsyncMock()
        )
        session = await relay.attach_device(ws, 'device-long-audio', 'ble', 'test')
        await relay.activate_device(session, ws, 'ble')
        session.ensure_memory()
        store = relay.conversation_store()
        session.turn_id = 'turn-long-audio'
        store.begin(session.conversation_id, session.turn_id, '背诵一段长诗')
        session.busy = True

        q = asyncio.Queue()
        async def receive():
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item

        self.fake_live.receive = receive
        loop_task = asyncio.create_task(session._gemini_receive_loop())

        # Push 3 parts of audio (each 24,000 bytes = 0.5s 24kHz)
        raw_audio = b"\x00\x01" * 12000
        for i in range(3):
            await q.put(SimpleNamespace(
                session_resumption_update=None,
                go_away=None,
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text=f"Part {i}"),
                    model_turn=SimpleNamespace(parts=[
                        SimpleNamespace(inline_data=SimpleNamespace(data=raw_audio))
                    ]),
                    turn_complete=False,
                    interrupted=False
                )
            ))
        # Now send turn complete
        await q.put(SimpleNamespace(
            session_resumption_update=None,
            go_away=None,
            server_content=SimpleNamespace(
                input_transcription=None,
                output_transcription=None,
                model_turn=None,
                turn_complete=True,
                interrupted=False
            )
        ))

        # At this point, playback is draining chunks in the background
        await asyncio.wait_for(session.playback_queue.join(), timeout=3.0)
        await asyncio.sleep(0.05)

        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

        self.assertFalse(session.busy)
        turns = store.turns(session.conversation_id)
        self.assertEqual(turns[-1]['status'], 'completed')
        self.assertGreater(len(chunks_sent), 10)
        await relay.detach_device(session, ws, 'ble')

if __name__=='__main__':unittest.main()
