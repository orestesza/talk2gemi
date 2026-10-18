import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from ble_bridge import BLELink
from ble_protocol import encode, frame, unframe

class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent=[]
        async def send(event):
            self.sent.append(unframe(base64.b64decode(event['data'])) if event['event']=='send' else event)
        self.link=BLELink(SimpleNamespace(send=send),'peer')
    async def asyncTearDown(self):
        await self.link.close()
    async def ack(self, packet):
        await self.link.incoming(frame(3,packet[1],packet[2],b''))
        await asyncio.sleep(0)
    async def test_window_across_chunks_and_control_barrier(self):
        for _ in range(3):
            await self.link.send_bytes(bytes(1024))
        self.assertEqual(len(self.sent),3)
        fourth=asyncio.create_task(self.link.send_bytes(bytes(1024)))
        await asyncio.sleep(.01)
        self.assertEqual(len(self.sent),3)
        await self.ack(self.sent[0]);await fourth
        self.assertEqual(len(self.sent),4)
        control=asyncio.create_task(self.link.send_str('{"type":"state","value":"ready"}'))
        await asyncio.sleep(.01)
        self.assertEqual(len(self.sent),4)
        for packet in list(self.sent[1:]):await self.ack(packet)
        await asyncio.sleep(.01)
        self.assertEqual(self.sent[-1][0],1)
        await self.ack(self.sent[-1]);await control
        self.assertFalse(self.link.ack)
    async def test_stream_uplink_no_receipt_legacy_still_receipted(self):
        audio=AsyncMock()
        self.link.session=SimpleNamespace(ws=self.link,handle_binary_audio=audio)
        payload=encode(bytes(1024))
        await self.link.incoming(frame(4,1,65535,payload))
        self.assertEqual(self.sent,[])
        audio.assert_awaited_once_with(bytes(1024))
        await self.link.incoming(frame(2,1,0,payload))
        self.assertEqual(self.sent[0][0],3)
    async def test_sequence_gap_is_not_silently_accepted(self):
        await self.link.incoming(frame(4,1,1,encode(bytes(1024))))
        with self.assertRaises(ValueError):
            await self.link.incoming(frame(4,1,3,encode(bytes(1024))))
    async def test_disconnect_cancels_pending_and_clears_receipts(self):
        for _ in range(3):await self.link.send_bytes(bytes(1024))
        await self.link.close()
        self.assertFalse(self.link.audio_pending)
        self.assertFalse(self.link.ack)

if __name__=='__main__':unittest.main()
