"""Local, atomic companion settings shared by both transports."""
import json
import os
import socket
import uuid
from pathlib import Path

VOICES = ("Aoede", "Kore", "Laomedeia", "Sulafat")


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"host_id": str(uuid.uuid4()), "name": socket.gethostname(),
                     "voice": "Sulafat", "paired": {}, "downlink_codec": "opus"}
        if path.exists():
            loaded = json.loads(path.read_text())
            self.data.update(loaded)
        if self.data["voice"] not in VOICES:
            raise ValueError("Invalid saved voice")
        if self.data.get("downlink_codec") not in ("opus", "adpcm"):
            self.data["downlink_codec"] = "opus"
        uuid.UUID(self.data["host_id"])
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w") as stream:
            os.chmod(temp, 0o600)
            json.dump(self.data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)

    def set_voice(self, voice):
        if voice not in VOICES:
            raise ValueError("Unsupported voice")
        self.data["voice"] = voice
        self.save()
