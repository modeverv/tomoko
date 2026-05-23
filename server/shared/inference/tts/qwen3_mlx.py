from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.irodori_mlx import (
    _audio_to_numpy,
    _load_mlx_audio_model,
    _voice_to_ref_audio,
    _wav_bytes_from_audio,
)
from server.shared.models import AudioChunkOut, TTSInput

ModelFactory = Callable[[str], Any]


class Qwen3MLXTTSBackend(TTSBackend):
    name = "qwen3_mlx"

    STYLE_TO_INSTRUCT = {
        "neutral": "自然な日本語で話す。",
        "happy": "明るく、自然な日本語で話す。",
        "surprised": "少し驚いた感じで、自然な日本語で話す。",
        "excited": "少し弾んだ感じで、自然な日本語で話す。",
        "sad": "落ち着いて、やさしい日本語で話す。",
        "thinking": "少し考えながら、自然な日本語で話す。",
        "gentle": "やわらかく、やさしい日本語で話す。",
    }
    STYLE_TO_SPEED = {
        "neutral": 1.0,
        "happy": 0.95,
        "surprised": 0.98,
        "excited": 0.94,
        "sad": 0.92,
        "thinking": 0.9,
        "gentle": 0.92,
    }

    def __init__(
        self,
        *,
        model: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        voice: str = "none",
        lang_code: str = "Japanese",
        streaming_interval: float = 0.32,
        model_factory: ModelFactory | None = None,
        loaded_model: Any | None = None,
    ) -> None:
        self.model_name = model
        self.voice = voice
        self.lang_code = lang_code
        self.streaming_interval = streaming_interval
        self._model_factory = model_factory or _load_mlx_audio_model
        self._model = loaded_model

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> Qwen3MLXTTSBackend:
        return cls(
            model=spec.model or "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
            voice=spec.voice or "none",
        )

    async def synthesize(self, tts_input: TTSInput):
        text = tts_input.text.strip()
        if not text:
            return

        queue: asyncio.Queue[_GeneratedChunk | BaseException | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        worker = asyncio.create_task(
            asyncio.to_thread(self._stream_chunks_to_queue, text, tts_input, queue, loop)
        )

        sequence = 0
        pending: _GeneratedChunk | None = None
        try:
            while True:
                item = await queue.get()
                if isinstance(item, BaseException):
                    raise item
                if item is None:
                    if pending is not None:
                        yield AudioChunkOut(
                            data=_wav_bytes_from_audio(
                                pending.audio,
                                sample_rate=pending.sample_rate,
                            ),
                            sequence=sequence,
                            is_last=True,
                        )
                    break
                if pending is not None:
                    yield AudioChunkOut(
                        data=_wav_bytes_from_audio(
                            pending.audio,
                            sample_rate=pending.sample_rate,
                        ),
                        sequence=sequence,
                        is_last=False,
                    )
                    sequence += 1
                pending = item
        finally:
            await worker

    def _stream_chunks_to_queue(
        self,
        text: str,
        tts_input: TTSInput,
        queue: asyncio.Queue[_GeneratedChunk | BaseException | None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            for audio, sample_rate in self._generate_chunks(text, tts_input):
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    _GeneratedChunk(audio=audio, sample_rate=sample_rate),
                )
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except BaseException as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)

    def _generate_chunks(
        self,
        text: str,
        tts_input: TTSInput,
    ):
        model = self._load_model()
        results = model.generate(
            text=text,
            voice=_voice_to_ref_audio(tts_input.voice or self.voice),
            instruct=self.STYLE_TO_INSTRUCT.get(
                tts_input.style,
                self.STYLE_TO_INSTRUCT["neutral"],
            ),
            lang_code=self.lang_code,
            speed=self.STYLE_TO_SPEED.get(tts_input.style, 1.0),
            stream=True,
            streaming_interval=self.streaming_interval,
            split_pattern="\n",
        )
        for result in results:
            sample_rate = int(getattr(result, "sample_rate", self._model_sample_rate()))
            yield _audio_to_numpy(result.audio), sample_rate

    async def warm_up(self) -> None:
        async for _ in self.synthesize(TTSInput(text="あ。", style="neutral")):
            return

    def _load_model(self) -> Any:
        if self._model is None:
            self._model = self._model_factory(self.model_name)
        return self._model

    def _model_sample_rate(self) -> int:
        model = self._load_model()
        return int(getattr(model, "sample_rate", 24000))


@dataclass(slots=True)
class _GeneratedChunk:
    audio: np.ndarray
    sample_rate: int
