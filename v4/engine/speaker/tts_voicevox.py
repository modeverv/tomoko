"""TTS（VOICEVOX）。同じ機械で動いている VOICEVOX のエンジンに、HTTP で頼む。同梱はしない。

句ごとに /audio_query → /synthesis を呼ぶ。配布版（0.25 系）には逐次に返す口がないので、
句を丸ごと合成してから受け取る。句を短くすれば、そのぶん早く声が出る。
"""

import io
import wave

import httpx
import numpy as np

from engine.dto import OUT_SR


class VoicevoxTts:
    def __init__(self, client: httpx.AsyncClient, url: str, speaker: int, speed: float = 1.0,
                 pre_silence_s: float | None = None, post_silence_s: float | None = None) -> None:
        self._client, self._url, self._speaker, self._speed = client, url.rstrip("/"), speaker, speed
        self._pre, self._post = pre_silence_s, post_silence_s

    async def warm(self) -> None:
        await self._client.post(f"{self._url}/initialize_speaker", params={"speaker": self._speaker})
        await self.synthesize("こんにちは。")

    async def synthesize(self, text: str) -> np.ndarray:
        params = {"speaker": self._speaker}
        r = await self._client.post(f"{self._url}/audio_query", params={"text": text, **params})
        r.raise_for_status()
        query = r.json()
        query["outputSamplingRate"] = OUT_SR
        query["outputStereo"] = False
        query["speedScale"] = self._speed
        if self._pre is not None:
            query["prePhonemeLength"] = self._pre
        if self._post is not None:
            query["postPhonemeLength"] = self._post
        r = await self._client.post(f"{self._url}/synthesis", params=params, json=query)
        r.raise_for_status()
        with wave.open(io.BytesIO(r.content)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return (pcm / 32768).astype(np.float32)
