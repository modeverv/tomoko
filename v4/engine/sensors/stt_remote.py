"""STT（whisper.cpp のサーバー）。別の機械（例: RTX 3060）に置いた whisper-server に、HTTP で頼む。

発話 1 個ぶんの音を WAV にして /inference に送る。マイクの音が LAN を流れるので、既定では使わない。
"""

import io
import wave

import httpx
import numpy as np

from engine.dto import IN_SR


def to_wav(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(IN_SR)
        w.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


class WhisperCppStt:
    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client, self._url = client, url.rstrip("/")

    async def warm(self) -> None:
        await self.transcribe(np.zeros(IN_SR, dtype=np.float32))

    async def transcribe(self, audio: np.ndarray) -> str:
        r = await self._client.post(
            f"{self._url}/inference",
            files={"file": ("utterance.wav", to_wav(audio), "audio/wav")},
            data={"response_format": "json", "language": "ja", "temperature": "0.0"},
        )
        r.raise_for_status()
        return r.json().get("text", "").strip()
