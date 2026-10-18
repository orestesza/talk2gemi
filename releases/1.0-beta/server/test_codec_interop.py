"""Compile the actual firmware codec and compare with Python's reference codec."""
import ctypes
import random
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from ble_protocol import encode, decode
ROOT = Path(__file__).resolve().parents[1]
class CodecInterop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'scratch')
        output = Path(cls.tmp.name) / 'ima.dylib'
        subprocess.run(['cc', '-dynamiclib', '-Wall', '-Wextra', '-Werror', str(ROOT/'firmware/main/ima_codec.c'), '-o', str(output)], check=True)
        cls.lib = ctypes.CDLL(str(output))
        cls.lib.ima_encode.argtypes = [ctypes.POINTER(ctypes.c_int16), ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
        cls.lib.ima_decode.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.POINTER(ctypes.c_int16), ctypes.c_size_t]
        cls.lib.ima_encode.restype = cls.lib.ima_decode.restype = ctypes.c_size_t
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def test_both_directions_random_silence_clipping(self):
        rng = random.Random(91)
        for n in [1,2,3,17,511,512,1024]:
            for values in [[rng.randrange(-32768,32768) for _ in range(n)], [0]*n, [-32768,32767]*(n//2)+([0] if n%2 else [])]:
                pcm = struct.pack('<'+'h'*n, *values)
                wire = encode(pcm)
                source = (ctypes.c_uint8*len(wire)).from_buffer_copy(wire)
                decoded = (ctypes.c_int16*n)()
                self.assertEqual(self.lib.ima_decode(source,len(wire),decoded,n),n)
                self.assertEqual(bytes(decoded),decode(wire))
                encoded = (ctypes.c_uint8*600)()
                samples = (ctypes.c_int16*n)(*values)
                count = self.lib.ima_encode(samples,n,encoded,600)
                self.assertEqual(decode(bytes(encoded[:count])),decode(wire))
    def test_downlink_adaptive_index_matches_actual_firmware(self):
        import math
        for n in [17,511,512]:
            pcm=struct.pack('<'+'h'*n,*(int(9000*math.sin(i*.25)) for i in range(n)))
            wire=encode(pcm,quality=True)
            source=(ctypes.c_uint8*len(wire)).from_buffer_copy(wire)
            output=(ctypes.c_int16*n)()
            self.assertEqual(self.lib.ima_decode(source,len(wire),output,n),n)
            self.assertEqual(bytes(output),decode(wire))

    def test_rejects_truncation_and_small_destination(self):
        samples = (ctypes.c_int16*4)(0,1,2,3);out=(ctypes.c_uint8*8)()
        self.assertEqual(self.lib.ima_encode(samples,4,out,7),0)
        self.assertEqual(self.lib.ima_decode(out,5,samples,4),0)
if __name__ == '__main__': unittest.main()
