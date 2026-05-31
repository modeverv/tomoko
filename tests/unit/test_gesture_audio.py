from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest

from server.gateway.gesture_audio import (
    GESTURE_BACKCHANNEL_REACT_THRESHOLD,
    GestureAudioEmitter,
)
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import (
    AudioChunkOut,
    BackchannelSuggestion,
    ConnectedOutputState,
    TomoroRuntimeState,
    TTSInput,
)


class RecordingTTSBackend(TTSBackend):
    name = "recording_tts"

    def __init__(self) -> None:
        self.inputs: list[TTSInput] = []

    async def synthesize(
        self,
        tts_input: TTSInput,
    ) -> AsyncGenerator[AudioChunkOut, None]:
        self.inputs.append(tts_input)
        yield AudioChunkOut(
            data=f"audio:{tts_input.text}".encode(),
            sequence=0,
            is_last=True,
        )


class RecordingObserver:
    def __init__(self) -> None:
        self.tomoko_audio: list[bytes] = []

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del observed_at
        self.tomoko_audio.append(chunk)


def _state(
    *,
    attention_mode="engaged",
    vad_state="listening",
    playback_state="idle",
) -> TomoroRuntimeState:
    return TomoroRuntimeState(
        attention_mode=attention_mode,
        vad_state=vad_state,
        playback_state=playback_state,
        active_session_id=None,
        active_turn_id=None,
        speaking_turn_id=None,
        context_build_id=None,
        output_state=ConnectedOutputState.single_client(device_id="test-device"),
    )


def _suggestion(*, observed_at: datetime | None = None) -> BackchannelSuggestion:
    return BackchannelSuggestion(
        kind="react",
        score=0.8,
        source="maai",
        observed_at=observed_at or datetime.now(UTC),
    )


@pytest.mark.unit
async def test_gesture_audio_emits_backchannel_without_session_mutation() -> None:
    events: list[dict[str, object]] = []
    audio: list[bytes] = []
    observer = RecordingObserver()
    tts = RecordingTTSBackend()
    emitter = GestureAudioEmitter(
        state_provider=_state,
        send_audio=audio.append,
        send_event=events.append,
        tts_backend=tts,
        audio_observer=observer,
        react_utterances=("うん",),
    )

    result = await emitter.release_backchannel(_suggestion())

    assert result.released is True
    assert result.text == "うん"
    assert tts.inputs == [TTSInput(text="うん", style="gentle")]
    assert audio == [b"audio:\xe3\x81\x86\xe3\x82\x93"]
    assert observer.tomoko_audio == audio
    assert events[0]["type"] == "backchannel_released"
    assert events[0]["lane"] == "gesture_audio"
    assert events[-1] == {"type": "reply_done", "control": "backchannel"}
    assert not any(event.get("type") == "audio_start" for event in events)
    assert not any(event.get("type") == "audio_end" for event in events)


@pytest.mark.unit
async def test_gesture_audio_reads_state_snapshot_for_release_gates() -> None:
    events: list[dict[str, object]] = []
    audio: list[bytes] = []
    tts = RecordingTTSBackend()
    emitter = GestureAudioEmitter(
        state_provider=lambda: _state(attention_mode="ambient"),
        send_audio=audio.append,
        send_event=events.append,
        tts_backend=tts,
    )

    result = await emitter.release_backchannel(_suggestion())

    assert result.released is False
    assert result.reason == "attention_not_engaged"
    assert audio == []
    assert tts.inputs == []
    assert events[0]["type"] == "backchannel_skipped"
    assert events[0]["reason"] == "attention_not_engaged"


@pytest.mark.unit
async def test_gesture_audio_applies_cooldown_without_session_state() -> None:
    events: list[dict[str, object]] = []
    audio: list[bytes] = []
    tts = RecordingTTSBackend()
    emitter = GestureAudioEmitter(
        state_provider=_state,
        send_audio=audio.append,
        send_event=events.append,
        tts_backend=tts,
        react_utterances=("うん",),
    )
    first_at = datetime.now(UTC)

    first = await emitter.release_backchannel(_suggestion(observed_at=first_at))
    second = await emitter.release_backchannel(
        _suggestion(observed_at=first_at + timedelta(milliseconds=500))
    )
    third = await emitter.release_backchannel(
        _suggestion(observed_at=first_at + timedelta(milliseconds=1600))
    )

    assert first.released is True
    assert second.released is False
    assert second.reason == "cooldown_active"
    assert third.released is True
    assert len(audio) == 2
    assert len(tts.inputs) == 2


@pytest.mark.unit
async def test_gesture_audio_uses_production_react_threshold() -> None:
    assert GESTURE_BACKCHANNEL_REACT_THRESHOLD == pytest.approx(0.50)

    events: list[dict[str, object]] = []
    audio: list[bytes] = []
    tts = RecordingTTSBackend()
    emitter = GestureAudioEmitter(
        state_provider=_state,
        send_audio=audio.append,
        send_event=events.append,
        tts_backend=tts,
        react_utterances=("うん",),
    )
    now = datetime.now(UTC)

    below = await emitter.release_backchannel(
        BackchannelSuggestion(
            kind="react",
            score=0.49,
            source="maai",
            observed_at=now,
        )
    )
    at_threshold = await emitter.release_backchannel(
        BackchannelSuggestion(
            kind="react",
            score=0.50,
            source="maai",
            observed_at=now + timedelta(milliseconds=1600),
        )
    )

    assert below.released is False
    assert below.reason == "below_threshold"
    assert at_threshold.released is True
    assert audio == [b"audio:\xe3\x81\x86\xe3\x82\x93"]
