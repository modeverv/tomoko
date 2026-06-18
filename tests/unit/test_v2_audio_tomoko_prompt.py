from __future__ import annotations

from datetime import timedelta

import pytest

from server.audio.stt import StaticStreamingSttBackend, StreamingSttEvent, observation_events
from server.audio.vad import VADProcessor
from server.hot_path.model_executor import (
    MultipartMixedParser,
    PromptExecutor,
    StaticChatBackend,
    StaticWavTtsBackend,
    parse_openai_sse_content,
)
from server.hot_path.protocol import (
    BrowserJsonEvent,
    encode_server_event,
    is_audio_control,
    parse_browser_message,
)
from server.shared.models import (
    CancelPolicy,
    FloorSignal,
    FloorState,
    PromptRequest,
    PromptScope,
    SessionSummary,
    SpeechDecisionKind,
    utc_now,
)
from server.tomoko.context import ContextSnapshotBuilderV2
from server.tomoko.floor import SpeechDecisionModel
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.session import SessionBoundaryModel

pytestmark = pytest.mark.unit


def test_vad_preroll_is_joined_at_speech_start() -> None:
    processor = VADProcessor(sample_rate=1000, pre_roll_ms=500, silence_ms=200)
    assert processor.process_chunk((0.1,) * 100, speech_probability=0.0, now_ms=0) is None
    assert processor.process_chunk((0.2,) * 100, speech_probability=0.0, now_ms=300) is None
    assert processor.process_chunk((0.9,) * 100, speech_probability=0.8, now_ms=600) is None
    segment = processor.process_chunk((0.0,) * 100, speech_probability=0.0, now_ms=900)
    assert segment is not None
    assert len(segment.samples) == 200
    assert segment.samples[:100] == (0.2,) * 100
    assert segment.samples[100:] == (0.9,) * 100


@pytest.mark.asyncio
async def test_streaming_stt_observation_keeps_vap_fields() -> None:
    now = utc_now()
    segment = VADProcessor(sample_rate=1000).process_chunk(
        (1.0,) * 100,
        speech_probability=1.0,
        now_ms=0,
    )
    assert segment is None
    processor = VADProcessor(sample_rate=1000, silence_ms=100)
    processor.process_chunk((1.0,) * 100, speech_probability=1.0, now_ms=0)
    segment = processor.process_chunk((0.0,) * 100, speech_probability=0.0, now_ms=250)
    assert segment is not None
    segment.started_at = now - timedelta(seconds=1)
    segment.ended_at = now
    observations = await observation_events(
        segment,
        StaticStreamingSttBackend(
            [StreamingSttEvent("途中", False, 0.5, p_yielding=0.91, recommended_silence_ms=150)]
        ),
    )
    assert observations[0].p_yielding == 0.91
    assert observations[0].recommended_silence_ms == 150


def test_browser_protocol_has_single_ws_style_events() -> None:
    parsed = parse_browser_message('{"type":"audio_control","command":"stop"}')
    assert isinstance(parsed, BrowserJsonEvent)
    assert is_audio_control(parsed)
    assert parse_browser_message(b"\x00\x01") == b"\x00\x01"
    assert encode_server_event("transcript", text="hi") == '{"type": "transcript", "text": "hi"}'


def test_openai_sse_and_voicevox_multipart_parsers() -> None:
    assert parse_openai_sse_content('data: {"choices":[{"delta":{"content":"こん"}}]}') == "こん"
    assert parse_openai_sse_content("data: [DONE]") is None

    wav = b"RIFFxxxxWAVEdata"
    body = (
        b"--abc\r\n"
        b"Content-Type: audio/wav\r\n"
        + f"Content-Length: {len(wav)}\r\n".encode("ascii")
        + b"\r\n"
        + wav
        + b"\r\n--abc--\r\n"
    )
    parser = MultipartMixedParser("abc")
    assert parser.feed(body) == [wav]
    assert parser.finish() == []


def test_tomoko_adopts_only_final_stt_observation() -> None:
    now = utc_now()
    core = TomokoProcessCore(SessionBoundaryModel())
    partial = core.adopt_final_observation(
        __import__("server.shared.models").shared.models.PartialTranscriptObservation(
            text="partial",
            is_final=False,
            stability=0.5,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    assert partial is None
    final = core.adopt_final_observation(
        __import__("server.shared.models").shared.models.PartialTranscriptObservation(
            text="final",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    assert final is not None
    assert final.text == "final"


def test_session_boundary_uses_idle_gap() -> None:
    now = utc_now()
    model = SessionBoundaryModel(idle_gap_to_new_session_ms=1000)
    first = model.observe_utterance(now)
    second = model.observe_utterance(now + timedelta(milliseconds=500))
    third = model.observe_utterance(now + timedelta(milliseconds=1600))
    assert first.started_new
    assert second.session_id == first.session_id
    assert third.started_new
    assert third.closed_session_id == first.session_id


def test_speech_decision_representative_cases_and_log_only_initiative() -> None:
    model = SpeechDecisionModel(full_reply_silence_ms=500, initiative_silence_ms=1000)
    assert model.decide(FloorSignal(FloorState.LISTENING, 0, user_speaking=True)).decision == (
        SpeechDecisionKind.YIELD_FLOOR
    )
    full = model.decide(FloorSignal(FloorState.IDLE_GAP, 600, p_yielding=0.9))
    assert full.decision == SpeechDecisionKind.FULL_REPLY
    assert full.should_execute
    initiative = model.decide(
        FloorSignal(
            FloorState.IDLE_GAP,
            1200,
            p_yielding=0.9,
            candidate_pressure=1.0,
        )
    )
    assert initiative.decision in {SpeechDecisionKind.FULL_REPLY, SpeechDecisionKind.INITIATIVE}
    assert model.decide(FloorSignal(FloorState.IDLE_GAP, 1200, stop_requested=True)).decision == (
        SpeechDecisionKind.STOP
    )


def test_prompt_builder_orders_stable_current_volatile_and_skips_calendar_for_clock() -> None:
    session_id = __import__("uuid").uuid4()
    summary = SessionSummary(
        session_id=session_id,
        keyword="clock",
        conclusion="test",
        embedding=(0.1,),
    )
    snapshot = ContextSnapshotBuilderV2().build(
        session_id=session_id,
        recent_utterances=["raw text"],
        summaries=[summary],
        calendar_loader=lambda: {"20260618T120000": "meeting"},
        user_status=None,
        candidates=[],
    )
    prompt = PromptBuilderV2().build_main_reply(snapshot, "いま何時?")
    assert "STABLE_CONTEXT:" in prompt.prompt_text
    assert "CURRENT_USER_UTTERANCE:\nいま何時?" in prompt.prompt_text
    assert "calendar[" not in prompt.prompt_text


@pytest.mark.asyncio
async def test_prompt_executor_requires_complete_wav_chunks() -> None:
    request = PromptRequest(
        prompt_text="hello",
        scope=PromptScope.MAIN,
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=1,
        cancel_policy=CancelPolicy.CANCEL_ON_USER_SPEAKING,
    )
    executor = PromptExecutor(
        StaticChatBackend(["こん", "にちは"]),
        StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
    )
    result = await executor.execute(request)
    assert [event.event_kind for event in result.model_events] == ["delta", "delta", "complete"]
    assert result.audio_chunks[0].is_final
    bad = PromptExecutor(StaticChatBackend(["x"]), StaticWavTtsBackend([b"not wav"]))
    with pytest.raises(ValueError):
        await bad.execute(request)
