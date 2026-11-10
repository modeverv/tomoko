"""TTS（VOICEVOX）。同じ機械で動いている VOICEVOX のエンジンに、HTTP で頼む。同梱はしない。

句ごとに /audio_query → /synthesis を呼ぶ。配布版（0.25 系）には逐次に返す口がないので、
句を丸ごと合成してから受け取る。句を短くすれば、そのぶん早く声が出る。

逐次に返す口（/streaming_synthesis。VOICEVOX ENGINE の PR 1823 の版）があるエンジンなら、
stream() で、できた分から受け取れる。返ってくるのは WAV 1 本で、頭の 44 バイトのあとに音が続く。
"""

import io
import struct
import wave
from collections.abc import AsyncIterator

import httpx
import numpy as np

from engine.dto import OUT_SR


class VoicevoxTts:
    def __init__(self, client: httpx.AsyncClient, url: str, speaker: int, speed: float = 1.0,
                 pre_silence_s: float | None = None, post_silence_s: float | None = None,
                 segment_s: float = 0.3) -> None:
        self._client, self._url, self._speaker, self._speed = client, url.rstrip("/"), speaker, speed
        self._pre, self._post, self._segment = pre_silence_s, post_silence_s, segment_s

    async def warm(self) -> None:
        await self._client.post(f"{self._url}/initialize_speaker", params={"speaker": self._speaker})
        await self.synthesize("こんにちは。")

    async def _query(self, text: str) -> dict:
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
        return query

    async def synthesize(self, text: str) -> np.ndarray:
        params, query = {"speaker": self._speaker}, await self._query(text)
        r = await self._client.post(f"{self._url}/synthesis", params=params, json=query)
        r.raise_for_status()
        with wave.open(io.BytesIO(r.content)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return (pcm / 32768).astype(np.float32)

    async def stream(self, text: str) -> AsyncIterator[np.ndarray]:
        """できた分から、声の断片を返す。"""
        params = {"speaker": self._speaker, "segment_length": self._segment}
        query = await self._query(text)
        async with self._client.stream("POST", f"{self._url}/streaming_synthesis",
                                       params=params, json=query) as r:
            r.raise_for_status()
            head, rest = b"", b""
            async for part in r.aiter_bytes():
                if len(head) < _WAV_HEAD:
                    head += part
                    if len(head) < _WAV_HEAD:
                        continue
                    channels, rate = struct.unpack("<HI", head[22:28])
                    if (channels, rate) != (1, OUT_SR):
                        raise ValueError(f"逐次合成の音の形式が違う: {channels} ch {rate} Hz")
                    part = head[_WAV_HEAD:]
                rest += part
                whole = len(rest) - len(rest) % 2  # 16 bit の途中で切れた分は、次に回す
                if whole:
                    yield (np.frombuffer(rest[:whole], dtype="<i2") / 32768).astype(np.float32)
                    rest = rest[whole:]


_WAV_HEAD = 44
