"""BLE independent IMA blocks, little endian, low nibble first.

Frames: version:u8=1, kind:u8 (1 JSON,2 ADPCM,3 ACK,4 streaming ADPCM), turn:u32,
sequence:u16, payload_length:u16, payload. Fragment: id:u16, offset:u16,
total:u16 + frame slice. Maximum frame 2048; ordered fragments; bounded audio window.
ADPCM payload: predictor:i16,index:u8=0,reserved:u8=0,samples:u16 + nibbles.
Every block carries its initial index; legacy/uplink starts at zero.
Downlink can optimize that index. Losing one block cannot poison later decoding.
"""
import audioop
import struct

SERVICE = "834f0001-3b4b-4b31-8f29-74cccf3d4430"
IDENTITY = "834f0002-3b4b-4b31-8f29-74cccf3d4430"
UPLINK = "834f0003-3b4b-4b31-8f29-74cccf3d4430"
DOWNLINK = "834f0004-3b4b-4b31-8f29-74cccf3d4430"
HEADER = struct.Struct("<BBIHH")
MAX_FRAME = 2048


def encode(pcm, quality=False):
    if not pcm or len(pcm) % 2 or len(pcm) > 2048:
        raise ValueError("PCM block must contain 1..1024 complete samples")
    predictor = struct.unpack_from("<h", pcm)[0]
    index = 0
    if quality and len(pcm) >= 16:
        # Optimize only the initial ADPCM step state. Existing wire format and
        # firmware already support it; uplink remains the unchanged C encoder.
        prefix = pcm[2:130]
        target = struct.unpack('<'+'h'*(len(prefix)//2), prefix)
        best = None
        for candidate in range(89):
            trial, _ = audioop.lin2adpcm(prefix, 2, (predictor, candidate))
            decoded, _ = audioop.adpcm2lin(trial, 2, (predictor, candidate))
            values = struct.unpack('<'+'h'*(len(decoded)//2), decoded)
            error = sum((a-b)**2 for a,b in zip(target,values))
            if best is None or error < best:
                best, index = error, candidate
    packed, _ = audioop.lin2adpcm(pcm[2:] + (b"\0\0" if (len(pcm)//2-1) % 2 else b""), 2, (predictor, index))
    # audioop uses high nibble first; the wire format uses low first.
    swapped = bytes((b >> 4) | ((b & 15) << 4) for b in packed)
    return struct.pack("<hBBH", predictor, index, 0, len(pcm)//2) + swapped


def decode(data):
    if len(data) < 6:
        raise ValueError("Short ADPCM block")
    predictor, index, reserved, samples = struct.unpack_from("<hBBH", data)
    if index > 88 or reserved or not 1 <= samples <= 1024 or len(data) != 6 + samples//2:
        raise ValueError("Invalid ADPCM block")
    swapped = bytes((b >> 4) | ((b & 15) << 4) for b in data[6:])
    pcm, _ = audioop.adpcm2lin(swapped, 2, (predictor, index))
    return struct.pack("<h", predictor) + pcm[:(samples-1)*2]


def frame(kind, turn, seq, payload):
    if len(payload) + HEADER.size > MAX_FRAME:
        raise ValueError("Frame too long")
    return HEADER.pack(1, kind, turn, seq, len(payload)) + payload


def unframe(data):
    if len(data) < HEADER.size:
        raise ValueError("Short frame")
    version, kind, turn, seq, size = HEADER.unpack_from(data)
    if version != 1 or kind not in (1, 2, 3, 4, 5) or size != len(data)-HEADER.size or len(data) > MAX_FRAME:
        raise ValueError("Malformed frame")
    return kind, turn, seq, data[HEADER.size:]
