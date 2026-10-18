import math
import struct
import tempfile
import unittest
from pathlib import Path
from settings import Settings, VOICES
from ble_protocol import encode, decode, frame, unframe


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.path = Path(self.tmp.name)
        self.now += 61

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_voice_settings_persist(self):
        s = Settings(self.path / "settings.json")
        self.assertEqual(s.data["voice"], "Sulafat")
        for voice in VOICES:
            s.set_voice(voice)
            self.assertEqual(Settings(s.path).data["voice"], voice)
        with self.assertRaises(ValueError):
            s.set_voice("unapproved")

    def test_adpcm_independent_blocks_and_validation(self):
        pcm = struct.pack("<512h", *(int(8000 * math.sin(i/18)) for i in range(512)))
        encoded = encode(pcm)
        result = decode(encoded)
        self.assertEqual(len(result), len(pcm))
        a, b = struct.unpack("<512h", pcm), struct.unpack("<512h", result)
        self.assertLess(sum((x-y)**2 for x,y in zip(a[40:],b[40:])) / 472, 50000)
        self.assertEqual(decode(encode(b"\0\0")), b"\0\0")
        self.assertEqual(unframe(frame(2, 42, 65535, encoded)), (2, 42, 65535, encoded))
        for invalid in (b"", b"\0" * 6, encoded[:-1]):
            with self.assertRaises(ValueError): decode(invalid)


if __name__ == "__main__": unittest.main()
