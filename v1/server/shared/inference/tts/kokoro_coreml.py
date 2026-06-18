from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.kokoro_mlx import _wav_bytes_from_audio
from server.shared.models import AudioChunkOut, TTSInput

_END = object()


class KokoroCoreMLBackend(TTSBackend):
    name = "kokoro_coreml"

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
        model_path: str | None = None,
        command: str | None = None,
        voice: str = "jf_alpha",
        sample_rate: int = 24000,
        tts: Any | None = None,
        streaming: bool = True,
    ) -> None:
        self.model_path = model_path
        self.command = command
        self.voice = voice
        self.sample_rate = sample_rate
        self.streaming = streaming
        self._tts = tts

        if self._tts is None and self.command is None:
            self._tts = _load_python_kokoro_coreml(model_path=model_path, sample_rate=sample_rate)

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> KokoroCoreMLBackend:
        return cls(
            model_path=spec.model_path or spec.model,
            command=spec.command,
            voice=spec.voice or "jf_alpha",
            sample_rate=spec.sample_rate or 24000,
            streaming=spec.streaming,
        )

    async def warm_up(self) -> None:
        async for _chunk in self.synthesize(
            TTSInput(text="こんにちは、トモコです。", style="neutral")
        ):
            break

    async def synthesize(self, tts_input: TTSInput):
        text = tts_input.text.strip()
        if not text:
            return

        voice = tts_input.voice or self.voice
        speed = self.STYLE_TO_SPEED.get(tts_input.style, 1.0)
        if self._tts is not None:
            async for chunk in self._synthesize_python(text=text, voice=voice, speed=speed):
                yield chunk
            return

        async for chunk in self._synthesize_command(text=text, voice=voice, speed=speed):
            yield chunk

    async def _synthesize_python(self, *, text: str, voice: str, speed: float):
        generate_stream = getattr(self._tts, "generate_stream", None)
        if callable(generate_stream) and self.streaming:
            iterator = generate_stream(
                text,
                voice=voice,
                speed=speed,
                sample_rate=self.sample_rate,
                language="ja" if voice.startswith(("jf_", "jm_")) else None,
            )
            sequence = 0
            while True:
                chunk = await asyncio.to_thread(_next_or_end, iterator)
                if chunk is _END:
                    return
                yield AudioChunkOut(
                    data=_wav_bytes_from_audio(np.asarray(chunk), sample_rate=self.sample_rate),
                    sequence=sequence,
                    is_last=False,
                )
                sequence += 1
            return

        synthesize = getattr(self._tts, "synthesize", None) or getattr(self._tts, "generate", None)
        if not callable(synthesize):
            raise RuntimeError(
                "kokoro_coreml Python object must provide generate_stream or synthesize"
            )
        audio = await asyncio.to_thread(
            synthesize,
            text,
            voice=voice,
            speed=speed,
            sample_rate=self.sample_rate,
        )
        yield AudioChunkOut(
            data=_wav_bytes_from_audio(np.asarray(audio), sample_rate=self.sample_rate),
            sequence=0,
            is_last=True,
        )

    async def _synthesize_command(self, *, text: str, voice: str, speed: float):
        if self.command is None:
            raise RuntimeError(
                "kokoro_coreml backend requires command when Python package is unavailable"
            )
        if shutil.which(self.command) is None:
            raise RuntimeError(
                f"{self.command!r} is not available. Install a Kokoro CoreML CLI or set "
                "backends.<name>.command to its path."
            )

        command_text, is_ipa = _prepare_cli_text(text, voice)
        if self.streaming:
            chunks = await self._run_streaming_command(
                text=command_text,
                voice=voice,
                speed=speed,
                is_ipa=is_ipa,
            )
            if chunks:
                for sequence, chunk in enumerate(chunks):
                    yield AudioChunkOut(data=chunk, sequence=sequence, is_last=False)
                return

        output_path = await asyncio.to_thread(
            self._run_file_command,
            command_text,
            voice,
            speed,
            is_ipa,
        )
        try:
            yield AudioChunkOut(data=output_path.read_bytes(), sequence=0, is_last=True)
        finally:
            output_path.unlink(missing_ok=True)

    async def _run_streaming_command(
        self,
        *,
        text: str,
        voice: str,
        speed: float,
        is_ipa: bool,
    ) -> list[bytes]:
        assert self.command is not None
        args = [
            self.command,
            "say",
            "--stream",
            "-v",
            voice,
            "-s",
            str(speed),
        ]
        if is_ipa:
            args.append("--ipa")
        args.append(text)
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        chunks: list[bytes] = []
        while True:
            data = await process.stdout.read(65536)
            if not data:
                break
            chunks.extend(_split_wav_stream(data))
        stderr = await process.stderr.read() if process.stderr is not None else b""
        return_code = await process.wait()
        if return_code != 0:
            if _is_unknown_stream_option(stderr):
                return []
            raise RuntimeError(stderr.decode(errors="replace").strip())
        return chunks

    def _run_file_command(self, text: str, voice: str, speed: float, is_ipa: bool) -> Path:
        assert self.command is not None
        fd, path_name = tempfile.mkstemp(prefix="tomoko-kokoro-coreml-", suffix=".wav")
        os.close(fd)
        output_path = Path(path_name)
        args = [
            self.command,
            "say",
            "-v",
            voice,
            "-s",
            str(speed),
            "-o",
            str(output_path),
        ]
        if is_ipa:
            args.append("--ipa")
        args.append(text)
        completed = subprocess.run(args, capture_output=True, text=True)
        if completed.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(completed.stderr.strip())
        return output_path


def _load_python_kokoro_coreml(*, model_path: str | None, sample_rate: int) -> Any:
    try:
        from kokoro_coreml import KokoroTTS
    except ImportError as e:
        raise RuntimeError(
            "kokoro-coreml is not installed. Install a Python package that exposes "
            "`kokoro_coreml.KokoroTTS`, or configure a CoreML CLI command."
        ) from e

    from_pretrained = getattr(KokoroTTS, "from_pretrained", None)
    if callable(from_pretrained):
        if model_path:
            return from_pretrained(model_path)
        return from_pretrained()
    return KokoroTTS(model_path=model_path, sample_rate=sample_rate)


def _prepare_cli_text(text: str, voice: str) -> tuple[str, bool]:
    if not voice.startswith(("jf_", "jm_")):
        return text, False
    return _japanese_to_ipa(text), True


def _japanese_to_ipa(text: str) -> str:
    try:
        from misaki import ja
    except ImportError as e:
        raise RuntimeError("misaki[ja] is required for Kokoro CoreML Japanese voices") from e

    _phonemes, tokens = ja.JAG2P(version="pyopenjtalk")(text)
    parts: list[str] = []
    for token in tokens:
        phonemes = getattr(token, "phonemes", None)
        if not phonemes:
            continue
        parts.append(str(phonemes))
        if phonemes in {",", ".", "?", "!"}:
            parts.append(" ")
    return "".join(parts).strip()


def _next_or_end(iterator: Iterator[np.ndarray]) -> np.ndarray | object:
    return next(iterator, _END)


def _split_wav_stream(data: bytes) -> list[bytes]:
    if not data.startswith(b"RIFF"):
        return []
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            wav.getnframes()
        return [data]
    except wave.Error:
        return []


def _is_unknown_stream_option(stderr: bytes) -> bool:
    text = stderr.decode(errors="replace").lower()
    return "stream" in text and (
        "unknown" in text
        or "unrecognized" in text
        or "cannot be used together" in text
    )
