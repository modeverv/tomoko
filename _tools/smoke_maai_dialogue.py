from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.gateway.maai_backchannel import (  # noqa: E402
    MAAI_FRAME_SIZE,
    MAAI_SAMPLE_RATE,
    MaaiBackchannelConfig,
    MaaiBackchannelTap,
    wav_bytes_to_float32_mono_16k,
)
from server.shared.inference.tts.say import SayBackend  # noqa: E402
from server.shared.models import BackchannelSuggestion, TTSInput  # noqa: E402


@dataclass(frozen=True)
class DialogueTurn:
    role: str
    text: str
    voice: str
    style: str = "neutral"
    start_sec: float | None = None
    gap_after_sec: float = 0.2


@dataclass(frozen=True)
class DialogueTimeline:
    turns: list[DialogueTurn]
    user_audio: np.ndarray
    tomoko_audio: np.ndarray
    duration_sec: float


class RawScoreMaaiTap(MaaiBackchannelTap):
    def __init__(
        self,
        *,
        config: MaaiBackchannelConfig,
        raw_scores: list[dict[str, Any]],
        suggestions: list[BackchannelSuggestion],
        maai_module: Any | None = None,
    ) -> None:
        super().__init__(
            config=config,
            suggestion_callback=suggestions.append,
            maai_module=maai_module,
        )
        self._raw_scores = raw_scores
        self._started_at = datetime.now(UTC)

    def handle_result(
        self,
        result: dict[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> BackchannelSuggestion | None:
        observed = observed_at or datetime.now(UTC)
        raw_payload, omitted_keys = _compact_raw_payload(result)
        self._raw_scores.append(
            {
                "index": len(self._raw_scores),
                "observed_at": observed.isoformat(),
                "observed_sec": (observed - self._started_at).total_seconds(),
                "p_bc_react": _float_or_none(result.get("p_bc_react")),
                "p_bc_emo": _float_or_none(result.get("p_bc_emo")),
                "raw": raw_payload,
                "raw_omitted_keys": omitted_keys,
            }
        )
        return super().handle_result(result, observed_at=observed)


DEFAULT_DIALOGUE = [
    DialogueTurn(
        role="user",
        text="昨日さ、娘とおばあちゃんが話してて、相手が言い終わる前に普通に返事してたんだよね。",
        voice="Kyoko",
        style="neutral",
        gap_after_sec=-0.25,
    ),
    DialogueTurn(
        role="tomoko",
        text="うん。",
        voice="Otoya",
        style="gentle",
        gap_after_sec=0.15,
    ),
    DialogueTurn(
        role="user",
        text="それで、次に言う反応が途中でもう決まってる感じがしたの。",
        voice="Kyoko",
        style="neutral",
        gap_after_sec=-0.2,
    ),
    DialogueTurn(
        role="tomoko",
        text="なるほど。",
        voice="Otoya",
        style="gentle",
        gap_after_sec=0.15,
    ),
    DialogueTurn(
        role="user",
        text="相槌って理解したサインでもあるし、そのままターンを取ることもあるよね。",
        voice="Kyoko",
        style="neutral",
        gap_after_sec=-0.1,
    ),
    DialogueTurn(
        role="tomoko",
        text="え、ちょっと待って、そこ詳しく。",
        voice="Otoya",
        style="surprised",
        gap_after_sec=0.3,
    ),
]


async def run_dialogue_smoke(
    *,
    turns: list[DialogueTurn] | None = None,
    synthesize_turn: Callable[[DialogueTurn], Awaitable[np.ndarray]] | None = None,
    maai_module: Any | None = None,
    realtime_scale: float = 1.0,
    wait_after_sec: float = 1.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    dialogue = turns or DEFAULT_DIALOGUE
    synthesizer = synthesize_turn or synthesize_say_turn
    scheduled_turns, rendered = await render_dialogue_turns(dialogue, synthesizer)
    timeline = compose_dialogue_timeline(scheduled_turns, rendered)
    raw_scores: list[dict[str, Any]] = []
    suggestions: list[BackchannelSuggestion] = []
    tap = RawScoreMaaiTap(
        config=MaaiBackchannelConfig(),
        raw_scores=raw_scores,
        suggestions=suggestions,
        maai_module=maai_module,
    )

    await tap.start()
    try:
        frames_sent = await feed_dialogue_timeline(
            tap,
            timeline,
            realtime_scale=realtime_scale,
        )
        if wait_after_sec > 0:
            await asyncio.sleep(wait_after_sec)
    finally:
        await tap.stop()

    summary = {
        "maai_enabled": True,
        "sample_rate": MAAI_SAMPLE_RATE,
        "frame_size": MAAI_FRAME_SIZE,
        "frame_sec": MAAI_FRAME_SIZE / MAAI_SAMPLE_RATE,
        "duration_sec": timeline.duration_sec,
        "frames_sent": frames_sent,
        "raw_score_count": len(raw_scores),
        "max_p_bc_react": max(
            (score["p_bc_react"] or 0.0 for score in raw_scores),
            default=0.0,
        ),
        "max_p_bc_emo": max(
            (score["p_bc_emo"] or 0.0 for score in raw_scores),
            default=0.0,
        ),
        "turns": [
            {
                "role": turn.role,
                "text": turn.text,
                "voice": turn.voice,
                "style": turn.style,
                "start_sec": turn.start_sec,
                "duration_sec": rendered[index].size / MAAI_SAMPLE_RATE,
            }
            for index, turn in enumerate(scheduled_turns)
        ],
        "raw_scores": raw_scores,
        "suggestions": [suggestion.to_json() for suggestion in suggestions],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


async def synthesize_say_turn(turn: DialogueTurn) -> np.ndarray:
    backend = SayBackend(voice=turn.voice)
    chunks: list[bytes] = []
    async for chunk in backend.synthesize(
        TTSInput(text=turn.text, style=turn.style, voice=turn.voice)
    ):
        chunks.append(chunk.data)
    if not chunks:
        return np.empty(0, dtype=np.float32)
    return wav_bytes_to_float32_mono_16k(b"".join(chunks))


async def render_dialogue_turns(
    turns: list[DialogueTurn],
    synthesize_turn: Callable[[DialogueTurn], Awaitable[np.ndarray]],
) -> tuple[list[DialogueTurn], dict[int, np.ndarray]]:
    scheduled: list[DialogueTurn] = []
    rendered: dict[int, np.ndarray] = {}
    cursor = 0.0
    for index, turn in enumerate(turns):
        audio = await synthesize_turn(turn)
        start_sec = cursor if turn.start_sec is None else turn.start_sec
        scheduled_turn = replace(turn, start_sec=max(0.0, start_sec))
        scheduled.append(scheduled_turn)
        rendered[index] = audio
        duration_sec = audio.size / MAAI_SAMPLE_RATE
        cursor = max(cursor, scheduled_turn.start_sec + duration_sec + turn.gap_after_sec)
    return scheduled, rendered


def compose_dialogue_timeline(
    turns: list[DialogueTurn],
    rendered: dict[int, np.ndarray],
    *,
    sample_rate: int = MAAI_SAMPLE_RATE,
) -> DialogueTimeline:
    total_samples = 0
    for index, turn in enumerate(turns):
        if turn.start_sec is None:
            raise ValueError("DialogueTurn.start_sec must be scheduled before compose")
        audio = rendered[index]
        start = int(round(turn.start_sec * sample_rate))
        total_samples = max(total_samples, start + int(audio.size))
    total_samples = max(MAAI_FRAME_SIZE, total_samples)
    user_audio = np.zeros(total_samples, dtype=np.float32)
    tomoko_audio = np.zeros(total_samples, dtype=np.float32)
    for index, turn in enumerate(turns):
        audio = rendered[index]
        if audio.size == 0:
            continue
        start = int(round((turn.start_sec or 0.0) * sample_rate))
        end = min(total_samples, start + int(audio.size))
        target = user_audio if turn.role == "user" else tomoko_audio
        target[start:end] = np.clip(target[start:end] + audio[: end - start], -1.0, 1.0)
    return DialogueTimeline(
        turns=turns,
        user_audio=user_audio,
        tomoko_audio=tomoko_audio,
        duration_sec=total_samples / sample_rate,
    )


async def feed_dialogue_timeline(
    tap: MaaiBackchannelTap,
    timeline: DialogueTimeline,
    *,
    realtime_scale: float,
) -> int:
    frames_sent = 0
    frame_sec = MAAI_FRAME_SIZE / MAAI_SAMPLE_RATE
    total = max(timeline.user_audio.size, timeline.tomoko_audio.size)
    for start in range(0, total, MAAI_FRAME_SIZE):
        tap.observe_duplex_audio(
            user_chunk=_frame(timeline.user_audio, start),
            tomoko_chunk=_frame(timeline.tomoko_audio, start),
            observed_at=datetime.now(UTC),
        )
        frames_sent += 1
        if realtime_scale > 0:
            await asyncio.sleep(frame_sec * realtime_scale)
    return frames_sent


def _frame(audio: np.ndarray, start: int) -> np.ndarray:
    frame = np.zeros(MAAI_FRAME_SIZE, dtype=np.float32)
    chunk = audio[start : start + MAAI_FRAME_SIZE]
    frame[: chunk.size] = chunk
    return frame


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _compact_raw_payload(result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    payload: dict[str, Any] = {}
    omitted: list[str] = []
    for key, value in result.items():
        if _should_omit_raw_value(key, value):
            omitted.append(str(key))
            continue
        payload[str(key)] = _json_safe(value)
    return payload, omitted


def _should_omit_raw_value(key: object, value: Any) -> bool:
    key_text = str(key)
    if key_text in {"x", "x1", "x2", "audio", "audio_ch1", "audio_ch2"}:
        return True
    if isinstance(value, np.ndarray):
        return value.size > 16
    if isinstance(value, list | tuple):
        return len(value) > 16 and all(isinstance(item, int | float) for item in value)
    return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a say-based two-speaker dialogue, feed it to MaAI bc_2type, "
            "and dump raw p_bc_react/p_bc_emo scores as JSON."
        )
    )
    parser.add_argument("--realtime-scale", type=float, default=1.0)
    parser.add_argument("--wait-after-sec", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/maai-dialogue-smoke.json"),
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    summary = await run_dialogue_smoke(
        realtime_scale=args.realtime_scale,
        wait_after_sec=args.wait_after_sec,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
