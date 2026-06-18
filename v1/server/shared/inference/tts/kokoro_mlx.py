from __future__ import annotations

import asyncio
import io
import time
import wave
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import numpy as np

from server.shared.config import BackendSpec
from server.shared.inference.trace import trace_backend_call
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import AudioChunkOut, TTSInput

_END = object()


class KokoroMLXBackend(TTSBackend):
    name = "kokoro_mlx"

    STYLE_TO_VOICE = {
        "neutral": "jf_alpha",
        "happy": "jf_alpha",
        "surprised": "jf_alpha",
        "excited": "jf_alpha",
        "sad": "jf_beta",
        "thinking": "jf_beta",
        "gentle": "jf_beta",
    }
    STYLE_TO_SPEED = {
        "neutral": 1.0,
        "happy": 1.05,
        "surprised": 1.05,
        "excited": 1.1,
        "sad": 0.92,
        "thinking": 0.95,
        "gentle": 0.94,
    }

    def __init__(
        self,
        *,
        model: str | None = None,
        voice: str = "jf_alpha",
        sample_rate: int = 24000,
        tts: Any | None = None,
    ) -> None:
        self.default_voice = voice
        self.sample_rate = sample_rate
        if tts is not None:
            self._tts = tts
            self._available_voices = _list_available_voices(tts)
            return

        try:
            from kokoro_mlx import KokoroTTS
        except ImportError as e:
            raise RuntimeError(
                "kokoro-mlx is not installed. Install it with "
                "`uv sync --extra mlx-tts` or `uv add kokoro-mlx misaki[ja]`."
            ) from e

        if model:
            self._tts = KokoroTTS.from_pretrained(model)
        else:
            self._tts = KokoroTTS.from_pretrained()
        _install_pyopenjtalk_japanese_phonemizer(self._tts)
        self._available_voices = _list_available_voices(self._tts)

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> KokoroMLXBackend:
        return cls(
            model=spec.model,
            voice=spec.voice or "jf_alpha",
            sample_rate=spec.sample_rate or 24000,
        )

    async def synthesize(self, tts_input: TTSInput):
        text = tts_input.text.strip()
        if not text:
            return

        request_id = str(uuid4())
        started_at = time.perf_counter()
        trace_backend_call(
            event="start",
            kind="tts",
            role="tts",
            backend=self.name,
            model="kokoro_mlx",
            request_id=request_id,
            queue_key="local_mlx",
        )
        voice = self._resolve_voice(
            tts_input.voice or self.STYLE_TO_VOICE.get(tts_input.style, self.default_voice)
        )
        speed = self.STYLE_TO_SPEED.get(tts_input.style, 1.0)
        sequence = 0
        first_chunk_emitted = False
        try:
            iterator = self._tts.generate_stream(
                text,
                voice=voice,
                speed=speed,
                sample_rate=self.sample_rate,
                language="ja" if voice.startswith(("jf_", "jm_")) else None,
            )
            while True:
                chunk = await asyncio.to_thread(_next_or_end, iterator)
                if chunk is _END:
                    break
                audio_chunk = AudioChunkOut(
                    data=_wav_bytes_from_audio(np.asarray(chunk), sample_rate=self.sample_rate),
                    sequence=sequence,
                    is_last=False,
                )
                if not first_chunk_emitted:
                    first_chunk_emitted = True
                    trace_backend_call(
                        event="first_chunk",
                        kind="tts",
                        role="tts",
                        backend=self.name,
                        model="kokoro_mlx",
                        request_id=request_id,
                        queue_key="local_mlx",
                        elapsed_ms=_elapsed_ms(started_at),
                        bytes=len(audio_chunk.data),
                    )
                yield audio_chunk
                sequence += 1
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="tts",
                role="tts",
                backend=self.name,
                model="kokoro_mlx",
                request_id=request_id,
                queue_key="local_mlx",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        else:
            trace_backend_call(
                event="done",
                kind="tts",
                role="tts",
                backend=self.name,
                model="kokoro_mlx",
                request_id=request_id,
                queue_key="local_mlx",
                total_ms=_elapsed_ms(started_at),
                chunk_count=sequence,
            )

    def _resolve_voice(self, preferred: str) -> str:
        if not self._available_voices or preferred in self._available_voices:
            return preferred
        if self.default_voice in self._available_voices:
            return self.default_voice
        prefix = preferred.split("_", 1)[0]
        for voice in self._available_voices:
            if voice.startswith(f"{prefix}_"):
                return voice
        return self._available_voices[0]


def _next_or_end(iterator: Iterator[np.ndarray]) -> np.ndarray | object:
    return next(iterator, _END)


def _list_available_voices(tts: Any) -> list[str]:
    list_voices = getattr(tts, "list_voices", None)
    if not callable(list_voices):
        return []
    try:
        return list(list_voices())
    except Exception:
        return []


def _install_pyopenjtalk_japanese_phonemizer(tts: Any) -> None:
    phonemizers = getattr(tts, "_phonemizers", None)
    config = getattr(tts, "_config", None)
    vocab = getattr(config, "vocab", None)
    if not isinstance(phonemizers, dict) or vocab is None:
        return

    try:
        from kokoro_mlx.phonemize import Phonemizer
        from misaki import ja
    except ImportError:
        return

    phonemizer = object.__new__(Phonemizer)
    phonemizer._vocab = vocab
    phonemizer._language = "ja"
    phonemizer._g2p = ja.JAG2P(version="pyopenjtalk")
    phonemizers["ja"] = phonemizer


def _wav_bytes_from_audio(audio: np.ndarray, *, sample_rate: int) -> bytes:
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
