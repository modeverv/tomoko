from __future__ import annotations

import json
from datetime import timedelta

import pytest

from server.audio.stt import ScriptedStreamingSttBackend, StreamingSttEvent
from server.hot_path.app import _fake_stt_backend
from server.shared.models import AudioSpeechSegment, utc_now

pytestmark = pytest.mark.unit


def _segment() -> AudioSpeechSegment:
    now = utc_now()
    return AudioSpeechSegment(
        samples=(0.2,) * 160,
        sample_rate=16000,
        started_at=now,
        ended_at=now + timedelta(milliseconds=10),
    )


async def _finals(backend: ScriptedStreamingSttBackend) -> list[str]:
    return [
        event.text
        async for event in backend.transcribe_stream(_segment())
        if event.is_final
    ]


@pytest.mark.asyncio
async def test_scripted_backend_pops_partials_within_current_utterance() -> None:
    backend = ScriptedStreamingSttBackend(
        [
            [
                StreamingSttEvent("今日の予定を", False, 0.8),
                StreamingSttEvent("今日の予定を教えて", False, 0.8),
                StreamingSttEvent("今日の予定を教えてください", True, 1.0),
            ],
            [
                StreamingSttEvent("もういい、ストップ", True, 1.0),
            ],
        ]
    )

    first = await backend.process_stream_chunk(
        (0.2,) * 160, sample_rate=16000, started_at_ms=0.0
    )
    second = await backend.process_stream_chunk(
        (0.2,) * 160, sample_rate=16000, started_at_ms=10.0
    )
    third = await backend.process_stream_chunk(
        (0.2,) * 160, sample_rate=16000, started_at_ms=20.0
    )

    assert first is not None and first.text == "今日の予定を"
    assert second is not None and second.text == "今日の予定を教えて"
    assert third is None


@pytest.mark.asyncio
async def test_scripted_backend_advances_utterance_per_segment() -> None:
    backend = ScriptedStreamingSttBackend(
        [
            [StreamingSttEvent("最初の発話です", True, 1.0)],
            [StreamingSttEvent("もういい、ストップ", True, 1.0)],
        ]
    )

    assert await _finals(backend) == ["最初の発話です"]
    assert await _finals(backend) == ["もういい、ストップ"]
    assert await _finals(backend) == []


@pytest.mark.asyncio
async def test_scripted_backend_second_utterance_partials_wait_for_first_segment() -> None:
    backend = ScriptedStreamingSttBackend(
        [
            [StreamingSttEvent("最初の発話です", True, 1.0)],
            [
                StreamingSttEvent("ちょっと", False, 0.8),
                StreamingSttEvent("ちょっと待ってストップ", True, 1.0),
            ],
        ]
    )

    early = await backend.process_stream_chunk(
        (0.2,) * 160, sample_rate=16000, started_at_ms=0.0
    )
    assert early is None

    assert await _finals(backend) == ["最初の発話です"]

    later = await backend.process_stream_chunk(
        (0.2,) * 160, sample_rate=16000, started_at_ms=100.0
    )
    assert later is not None and later.text == "ちょっと"
    assert await _finals(backend) == ["ちょっと待ってストップ"]


@pytest.mark.asyncio
async def test_fake_stt_backend_keeps_scripted_maai_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TOMOKO_V2_FAKE_STT_EVENTS",
        json.dumps(
            [
                {
                    "text": "今日の予定",
                    "is_final": False,
                    "p_yielding": 0.91,
                    "recommended_silence_ms": 180,
                },
                {"text": "今日の予定を教えて", "is_final": True},
            ],
            ensure_ascii=False,
        ),
    )

    backend = _fake_stt_backend()
    event = await backend.process_stream_chunk(
        (0.2,) * 160,
        sample_rate=16000,
        started_at_ms=0.0,
    )

    assert event is not None
    assert event.p_yielding == pytest.approx(0.91)
    assert event.recommended_silence_ms == 180
