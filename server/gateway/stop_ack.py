from __future__ import annotations

from pathlib import Path

from server.shared.models import AudioChunkOut


class StopAckAudioProvider:
    """Loads the fixed control response WAV used when advisory stop is adopted."""

    def __init__(
        self,
        path: str | Path = "assets/audio/stop_ack.wav",
        *,
        text: str = "はい、止めます",
    ) -> None:
        self.path = Path(path)
        self.text = text
        self._cached_audio: bytes | None = None

    def chunk(self) -> AudioChunkOut:
        if self._cached_audio is None:
            self._cached_audio = self.path.read_bytes()
        return AudioChunkOut(data=self._cached_audio, sequence=0, is_last=True)
