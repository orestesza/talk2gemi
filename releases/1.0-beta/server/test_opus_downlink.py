import asyncio,base64,ctypes as c,math,struct,unittest
from types import SimpleNamespace
from ble_bridge import BLELink
from ble_protocol import frame,unframe
from opus_downlink import OpusEncoder,FRAME_BYTES,LIBRARY,library

@unittest.skipUnless(LIBRARY.exists(),'Project Opus library not built')
class OpusTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_packet_decode_and_fixed_frame_duration(self):
        enc=OpusEncoder();lib=library()
        lib.opus_decoder_create.argtypes=[c.c_int,c.c_int,c.POINTER(c.c_int)];lib.opus_decoder_create.restype=c.c_void_p
        lib.opus_decode.argtypes=[c.c_void_p,c.POINTER(c.c_ubyte),c.c_int32,c.POINTER(c.c_int16),c.c_int,c.c_int];lib.opus_decode.restype=c.c_int
        lib.opus_decoder_destroy.argtypes=[c.c_void_p]
        error=c.c_int();decoder=lib.opus_decoder_create(16000,1,c.byref(error))
        self.assertEqual(error.value,0)
        try:
            for k in range(4):
                pcm=struct.pack('<960h',*(int(7000*math.sin((i+k*960)*.1)) for i in range(960)))
                packet=enc.encode(pcm);self.assertEqual(len(packet),180)
                data=(c.c_ubyte*len(packet)).from_buffer_copy(packet);out=(c.c_int16*960)()
                self.assertEqual(lib.opus_decode(decoder,data,len(packet),out,960,0),960)
                self.assertGreater(max(abs(x) for x in out),100)
            with self.assertRaises(ValueError):enc.encode(bytes(20))
        finally:enc.close();lib.opus_decoder_destroy(decoder)
    async def test_cross_chunk_tail_and_legacy_fallback(self):
        sent=[];link=None
        async def send(event):
            if event['event']!='send':return
            packet=unframe(base64.b64decode(event['data']));sent.append(packet)
            await link.incoming(frame(3,packet[1],packet[2],b''))
        broker=SimpleNamespace(send=send,settings=SimpleNamespace(data={'downlink_codec':'opus'}))
        link=BLELink(broker,'test');link.opus_supported=True
        for _ in range(5):await link.send_bytes(bytes(1024))
        await link.send_str('{"type":"state","value":"ready"}')
        self.assertTrue(all(x[0]==5 for x in sent[:-1]));self.assertEqual(sent[-1][0],1)
        self.assertGreaterEqual(len(sent[:-1])*960,2560)
        self.assertFalse(link.opus_pending);self.assertIsNone(link.opus_encoder)
        link.opus_supported=False;sent.clear()
        await link.send_bytes(bytes(1024));await link.send_str('{"type":"state","value":"ready"}')
        self.assertEqual(sent[0][0],2)
        await link.close()
    async def test_abort_discards_partial_frame(self):
        async def send(event):pass
        link=BLELink(SimpleNamespace(send=send,settings=SimpleNamespace(data={'downlink_codec':'opus'})),'test')
        link.opus_supported=True
        await link.send_bytes(bytes(1000));self.assertEqual(len(link.opus_pending),1000)
        await link.abort_audio();self.assertFalse(link.opus_pending);self.assertIsNone(link.opus_encoder)
        await link.close()
