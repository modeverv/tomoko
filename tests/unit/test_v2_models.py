from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
from uuid import UUID

import pytest

from server.shared.models import (
    AudioChunkOut,
    AudioSpeechSegment,
    CandidateRecord,
    CandidateSeed,
    ContextSnapshot,
    PartialTranscriptObservation,
    SessionSummary,
    SpeechDecision,
    SpeechDecisionKind,
    UserStatusObservation,
    utc_now,
)

pytestmark = pytest.mark.unit


def test_all_v2_boundary_dtos_live_in_shared_models() -> None:
    names = {
        "AudioSpeechSegment",
        "PartialTranscriptObservation",
        "FinalTranscriptEvent",
        "DurableUtterance",
        "PromptRequest",
        "ModelOutputEvent",
        "AudioChunkOut",
        "FloorObservation",
        "SpeechDecision",
        "UserStatusObservation",
        "ContextSnapshot",
        "CandidateSeed",
        "CandidateRecord",
        "SessionSummary",
    }
    import server.shared.models as models

    for name in names:
        assert hasattr(models, name)


def test_dto_round_trip_keeps_uuid_datetime_enum_tuple_and_bytes() -> None:
    now = utc_now()
    observation = PartialTranscriptObservation(
        text="こんにちは",
        is_final=True,
        stability=0.98,
        audio_started_at=now - timedelta(seconds=1),
        audio_ended_at=now,
        p_yielding=0.91,
        recommended_silence_ms=150,
    )
    restored = PartialTranscriptObservation.from_dict(observation.to_dict())
    assert restored == observation
    assert isinstance(restored.id, UUID)

    chunk = AudioChunkOut(request_id=observation.id, chunk=b"RIFFxxxxWAVEdata", sample_rate=16000)
    assert AudioChunkOut.from_dict(chunk.to_dict()) == chunk

    decision = SpeechDecision(
        decision=SpeechDecisionKind.FULL_REPLY,
        should_execute=True,
        reason="test",
        score_breakdown={"full_reply": 1.0},
    )
    assert SpeechDecision.from_dict(decision.to_dict()).decision == SpeechDecisionKind.FULL_REPLY


def test_high_volume_dtos_use_slots_and_unique_default_ids() -> None:
    now = utc_now()
    first = AudioSpeechSegment(samples=(0.0,), sample_rate=16000, started_at=now, ended_at=now)
    second = AudioSpeechSegment(samples=(0.0,), sample_rate=16000, started_at=now, ended_at=now)
    assert first.trace_id != second.trace_id
    assert hasattr(AudioChunkOut, "__slots__")
    assert hasattr(PartialTranscriptObservation, "__slots__")


def test_context_snapshot_keeps_structured_children() -> None:
    summary = SessionSummary(
        session_id=UUID("00000000-0000-0000-0000-000000000001"),
        keyword="DDD",
        conclusion="ユーザーはDDDに懐疑的である",
        embedding=(0.1, 0.2),
    )
    seed = CandidateSeed(
        source="calendar",
        source_key="20260618T120000",
        text="meeting",
        priority=0.8,
        urgency=0.6,
        intrusion=0.1,
        maturity=1.0,
        context_tags=("calendar",),
    )
    candidate = CandidateRecord(
        seed_id=seed.id,
        source=seed.source,
        source_key=seed.source_key,
        text=seed.text,
        priority=seed.priority,
        urgency=seed.urgency,
        intrusion=seed.intrusion,
        maturity=seed.maturity,
        lifecycle="active",
        context_tags=seed.context_tags,
    )
    status = UserStatusObservation(
        present=True,
        activity_label="coding_or_terminal",
        summary="testing",
        source="unit",
    )
    snapshot = ContextSnapshot(
        session_id=summary.session_id,
        recent_utterances=("raw user text",),
        summaries=(summary,),
        calendar_items={"20260618T120000": "meeting"},
        user_status=status,
        candidates=(candidate,),
    )
    field_names = {field.name for field in fields(snapshot)}
    assert "recent_utterances" in field_names
    assert snapshot.to_dict()["summaries"][0]["keyword"] == "DDD"
    assert snapshot.to_dict()["user_status"]["activity_label"] == "coding_or_terminal"
