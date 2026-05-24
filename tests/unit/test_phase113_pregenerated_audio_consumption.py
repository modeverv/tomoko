from __future__ import annotations

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


@pytest.mark.unit
async def test_cached_audio_uses_plan_event_order() -> None:
    events: list[dict[str, str]] = []
    audio_chunks: list[bytes] = []
    audio = b"RIFF\x24\x00\x00\x00WAVEfmt cached"
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        send_audio=audio_chunks.append,
    )

    await session.start_precomputed_reply(
        text="今ならすぐ言えるよ。",
        device_id="desk",
        reason="phase113_unit",
        audio_data=audio,
    )

    assert events[-4:] == [
        {"type": "reply_text", "delta": "今ならすぐ言えるよ。"},
        {"type": "audio_start", "turn_id": events[-3]["turn_id"]},
        {"type": "audio_end", "turn_id": events[-3]["turn_id"]},
        {"type": "reply_done"},
    ]
    assert audio_chunks == [audio]
