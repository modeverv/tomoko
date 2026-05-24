from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.shared.config import BackendSpec  # noqa: E402
from server.shared.inference.tts import create_tts_backend  # noqa: E402
from server.shared.models import TTSInput  # noqa: E402


@dataclass(slots=True)
class BenchTarget:
    name: str
    spec: BackendSpec


TARGETS = [
    BenchTarget(
        name="irodori_mlx",
        spec=BackendSpec(
            name="irodori_mlx",
            type="irodori_mlx",
            model="mlx-community/Irodori-TTS-500M-v3-8bit",
            voice="none",
        ),
    ),
    BenchTarget(
        name="irodori_mlx_stream",
        spec=BackendSpec(
            name="irodori_mlx_stream",
            type="irodori_mlx_stream",
            model="mlx-community/Irodori-TTS-500M-v3-8bit",
            voice="none",
        ),
    ),
    BenchTarget(
        name="qwen3_tts_mlx_small",
        spec=BackendSpec(
            name="qwen3_tts_mlx_small",
            type="qwen3_mlx",
            model="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
            voice="none",
        ),
    ),
    BenchTarget(
        name="qwen3_tts_mlx_large",
        spec=BackendSpec(
            name="qwen3_tts_mlx_large",
            type="qwen3_mlx",
            model="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
            voice="none",
        ),
    ),
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--text",
        default="うん、わかった。少し待ってね。",
    )
    parser.add_argument("--style", default="neutral")
    parser.add_argument("--voice", default=None)
    parser.add_argument(
        "--output-dir",
        default="logs/tts-bench",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("| backend | warmup_ms | first_chunk_ms | total_ms | chunks | bytes | audio_ms |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for target in TARGETS:
        result = await bench_target(target, args.text, args.style, args.voice, output_dir)
        print(
            f"| {result['backend']} | {result['warmup_ms']:.1f} | "
            f"{result['first_chunk_ms']:.1f} | {result['total_ms']:.1f} | "
            f"{result['chunks']} | {result['bytes']} | {result['audio_ms']:.1f} |"
        )


async def bench_target(
    target: BenchTarget,
    text: str,
    style: str,
    voice: str | None,
    output_dir: Path,
) -> dict[str, float | int | str]:
    spec = target.spec
    if voice and target.spec.type == "qwen3_mlx":
        spec = BackendSpec(
            name=target.spec.name,
            type=target.spec.type,
            model=target.spec.model,
            voice=voice,
            max_latency_ms=target.spec.max_latency_ms,
            privacy_allowed=target.spec.privacy_allowed,
        )
    backend = create_tts_backend(spec)

    warm_start = time.perf_counter()
    await backend.warm_up()
    warmup_ms = (time.perf_counter() - warm_start) * 1000

    start = time.perf_counter()
    first_chunk_ms: float | None = None
    chunks: list[bytes] = []
    async for chunk in backend.synthesize(TTSInput(text=text, style=style)):
        now = time.perf_counter()
        if first_chunk_ms is None:
            first_chunk_ms = (now - start) * 1000
        chunks.append(chunk.data)
    total_ms = (time.perf_counter() - start) * 1000

    combined_path = output_dir / f"{target.name}.wav"
    audio_ms = _write_joined_wav(combined_path, chunks)
    return {
        "backend": target.name,
        "warmup_ms": warmup_ms,
        "first_chunk_ms": first_chunk_ms or 0.0,
        "total_ms": total_ms,
        "chunks": len(chunks),
        "bytes": sum(len(chunk) for chunk in chunks),
        "audio_ms": audio_ms,
    }


def _write_joined_wav(path: Path, chunks: list[bytes]) -> float:
    if not chunks:
        return 0.0

    sample_rate: int | None = None
    pcm_parts: list[bytes] = []
    frame_count = 0
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError("expected mono 16-bit WAV chunk")
            if sample_rate is None:
                sample_rate = wav.getframerate()
            elif sample_rate != wav.getframerate():
                raise ValueError("cannot join chunks with different sample rates")
            frames = wav.readframes(wav.getnframes())
            pcm_parts.append(frames)
            frame_count += wav.getnframes()

    assert sample_rate is not None
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"".join(pcm_parts))
    return frame_count / sample_rate * 1000


if __name__ == "__main__":
    asyncio.run(main())
