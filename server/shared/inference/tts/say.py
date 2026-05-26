from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from server.shared.config import BackendSpec
from server.shared.inference.trace import trace_backend_call
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import AudioChunkOut, TTSInput


class SayBackend(TTSBackend):
    name = "say"

    STYLE_TO_RATE = {
        "neutral": 175,
        "happy": 190,
        "surprised": 185,
        "excited": 200,
        "sad": 155,
        "thinking": 165,
        "gentle": 160,
    }

    def __init__(self, voice: str = "Kyoko") -> None:
        self.voice = voice

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> SayBackend:
        return cls(voice=spec.voice or "Kyoko")

    async def synthesize(self, tts_input: TTSInput):
        if not tts_input.text.strip():
            return

        request_id = str(uuid4())
        started_at = time.perf_counter()
        trace_backend_call(
            event="start",
            kind="tts",
            role="tts",
            backend=self.name,
            model="macos_say",
            request_id=request_id,
            queue_key="local_cpu",
        )
        rate = self.STYLE_TO_RATE.get(tts_input.style, self.STYLE_TO_RATE["neutral"])
        voice = tts_input.voice or self.voice
        try:
            with tempfile.TemporaryDirectory(prefix="tomoko-say-") as tmp_dir:
                output_path = Path(tmp_dir) / "speech.wav"
                proc = await asyncio.create_subprocess_exec(
                    "say",
                    "-v",
                    voice,
                    "-r",
                    str(rate),
                    "--data-format=LEI16@16000",
                    "-o",
                    str(output_path),
                    tts_input.text,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    message = stderr.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"say failed: {message}")

                chunk = AudioChunkOut(
                    data=output_path.read_bytes(),
                    sequence=0,
                    is_last=True,
                )
                trace_backend_call(
                    event="first_chunk",
                    kind="tts",
                    role="tts",
                    backend=self.name,
                    model="macos_say",
                    request_id=request_id,
                    queue_key="local_cpu",
                    elapsed_ms=_elapsed_ms(started_at),
                    bytes=len(chunk.data),
                )
                yield chunk
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="tts",
                role="tts",
                backend=self.name,
                model="macos_say",
                request_id=request_id,
                queue_key="local_cpu",
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
                model="macos_say",
                request_id=request_id,
                queue_key="local_cpu",
                total_ms=_elapsed_ms(started_at),
                chunk_count=1,
            )


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
