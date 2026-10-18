"""Project-local libopus wrapper. Downlink only: 16kHz mono, 60ms, 24kbps CBR."""
import ctypes as c
from pathlib import Path
LIBRARY=Path(__file__).resolve().parents[1]/'vendor/opus-mac/lib/libopus.0.dylib'
FRAME_BYTES=1920
_lib=None
def library():
    global _lib
    if _lib is None:
        lib=c.CDLL(str(LIBRARY))
        lib.opus_encoder_create.argtypes=[c.c_int,c.c_int,c.c_int,c.POINTER(c.c_int)];lib.opus_encoder_create.restype=c.c_void_p
        lib.opus_encoder_ctl.argtypes=[c.c_void_p,c.c_int];lib.opus_encoder_ctl.restype=c.c_int
        lib.opus_encode.argtypes=[c.c_void_p,c.POINTER(c.c_int16),c.c_int,c.POINTER(c.c_ubyte),c.c_int32];lib.opus_encode.restype=c.c_int
        lib.opus_encoder_destroy.argtypes=[c.c_void_p]
        _lib=lib
    return _lib
class OpusEncoder:
    def __init__(self):
        self.lib=library();error=c.c_int()
        self.ptr=self.lib.opus_encoder_create(16000,1,2049,c.byref(error))
        if not self.ptr or error.value:raise RuntimeError('Opus encoder initialization failed')
        try:
            for request,value in [(4002,24000),(4006,0),(4010,7),(4024,3001)]:
                if self.lib.opus_encoder_ctl(self.ptr,request,c.c_int(value))!=0:raise RuntimeError('Opus configuration failed')
            delay=c.c_int()
            if self.lib.opus_encoder_ctl(self.ptr,4027,c.byref(delay))!=0:raise RuntimeError('Opus lookahead unavailable')
            self.lookahead=delay.value
        except BaseException:self.close();raise
    def encode(self,pcm):
        if len(pcm)!=FRAME_BYTES:raise ValueError('Opus requires exactly 60ms PCM')
        samples=(c.c_int16*960).from_buffer_copy(pcm);out=(c.c_ubyte*512)()
        n=self.lib.opus_encode(self.ptr,samples,960,out,512)
        if n<1:raise RuntimeError('Opus encoding failed')
        return bytes(out[:n])
    def close(self):
        if self.ptr:self.lib.opus_encoder_destroy(self.ptr);self.ptr=None
