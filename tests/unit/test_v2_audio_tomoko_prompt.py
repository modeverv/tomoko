from __future__ import annotations

import json
import wave
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from server.audio import stt as stt_module
from server.audio.stt import (
    AppleSpeechStreamingBackend,
    StaticStreamingSttBackend,
    StreamingSttEvent,
    observation_events,
)
from server.audio.vad import VADProcessor
from server.hot_path.audio_conversation import HotPathAudioConversation, audio_bytes_to_samples
from server.hot_path.model_executor import (
    MultipartMixedParser,
    PromptExecutor,
    StaticChatBackend,
    StaticWavTtsBackend,
    VoicevoxChunkedTtsBackend,
    create_default_real_prompt_executor,
    parse_openai_sse_content,
)
from server.hot_path.model_executor import (
    _messages_for_request as hot_path_messages_for_request,
)
from server.hot_path.protocol import (
    BrowserJsonEvent,
    encode_server_event,
    is_audio_control,
    parse_browser_message,
)
from server.llm.chat import _messages_for_request as tomoko_messages_for_request
from server.shared.models import (
    AudioSpeechSegment,
    CancelPolicy,
    ConversationHistoryItem,
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
from server.tomoko.main import TomokoProcessCore, normalize_stt_block_text
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


def test_vad_reset_drops_in_progress_speech_and_preroll() -> None:
    processor = VADProcessor(sample_rate=1000, pre_roll_ms=500, silence_ms=100)
    processor.process_chunk((0.1,) * 100, speech_probability=0.0, now_ms=0)
    processor.process_chunk((0.9,) * 100, speech_probability=0.8, now_ms=100)
    processor.reset()

    assert processor.process_chunk((0.0,) * 100, speech_probability=0.0, now_ms=400) is None
    assert processor.process_chunk((0.0,) * 100, speech_probability=0.0, now_ms=600) is None


def test_audio_bytes_are_decoded_as_float32_chunks() -> None:
    assert audio_bytes_to_samples(b"\x00\x00\x00\x00\x00\x00\x00?") == (0.0, 0.5)


@pytest.mark.asyncio
async def test_apple_speech_backend_writes_wav_and_yields_final_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = tmp_path / "apple-speech-stt"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    observed_audio_paths: list[Path] = []

    def fake_run(args: list[str], **_kwargs: object) -> object:
        audio_path = Path(args[args.index("--audio") + 1])
        observed_audio_paths.append(audio_path)
        with wave.open(str(audio_path), "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getnframes() == 2

        class Completed:
            stdout = json.dumps({"text": "こんにちは"})

        return Completed()

    monkeypatch.setattr(stt_module.subprocess, "run", fake_run)
    backend = AppleSpeechStreamingBackend(command=str(command))
    now = utc_now()
    segment = AudioSpeechSegment(
        samples=(0.5, -0.5),
        sample_rate=16000,
        started_at=now - timedelta(milliseconds=1),
        ended_at=now,
    )
    events = [event async for event in backend.transcribe_stream(segment)]
    assert events == [StreamingSttEvent("こんにちは", True, 1.0)]
    assert observed_audio_paths
    assert not observed_audio_paths[0].exists()


class _RecordingSttBackend:
    def __init__(self, text: str = "トモコ、返事して") -> None:
        self.text = text
        self.segments: list[AudioSpeechSegment] = []

    async def transcribe_stream(
        self,
        segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        self.segments.append(segment)
        yield StreamingSttEvent(self.text, True, 1.0)


@pytest.mark.asyncio
async def test_hot_path_audio_conversation_runs_vad_stt_tomoko_prompt_with_preroll() -> None:
    stt_backend = _RecordingSttBackend()
    conversation = HotPathAudioConversation(
        vad=VADProcessor(sample_rate=1000, pre_roll_ms=300, silence_ms=100),
        stt_backend=stt_backend,
        tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
        prompt_builder=PromptBuilderV2(),
        prompt_executor=PromptExecutor(
            StaticChatBackend(["うん"]),
            StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
        ),
        speech_rms_threshold=0.02,
    )

    assert await conversation.process_audio_samples((0.002,) * 100) is None
    assert await conversation.process_audio_samples((0.004,) * 100) is None
    assert await conversation.process_audio_samples((0.2,) * 100) is None
    assert await conversation.process_audio_samples((0.0,) * 100) is None
    result = await conversation.process_audio_samples((0.0,) * 100)
    assert result is not None
    assert result.observations[0].text == "トモコ、返事して"
    assert result.durable_utterance is not None
    assert result.prompt_request is not None
    assert result.execution_result.audio_chunks[0].chunk == b"RIFFxxxxWAVEdata"
    assert stt_backend.segments[0].samples[:100] == (0.002,) * 100
    assert stt_backend.segments[0].samples[100:200] == (0.004,) * 100
    assert stt_backend.segments[0].samples[200:300] == (0.2,) * 100


async def test_hot_path_does_not_prompt_for_blank_final_stt() -> None:
    stt_backend = _RecordingSttBackend(text="")
    conversation = HotPathAudioConversation(
        vad=VADProcessor(sample_rate=1000, pre_roll_ms=300, silence_ms=100),
        stt_backend=stt_backend,
        tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
        prompt_builder=PromptBuilderV2(),
        prompt_executor=PromptExecutor(
            StaticChatBackend(["この返答は出てはいけない"]),
            StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
        ),
        speech_rms_threshold=0.02,
    )

    assert await conversation.process_audio_samples((0.2,) * 100) is None
    assert await conversation.process_audio_samples((0.0,) * 100) is None
    result = await conversation.process_audio_samples((0.0,) * 100)
    assert result is not None
    assert result.observations[0].text == ""
    assert result.durable_utterance is None
    assert result.prompt_request is None
    assert result.execution_result.audio_chunks == []


@pytest.mark.asyncio
async def test_hot_path_blocks_rule_based_stt_hallucination(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stt_backend = _RecordingSttBackend(text="はい")
    conversation = HotPathAudioConversation(
        vad=VADProcessor(sample_rate=1000, pre_roll_ms=300, silence_ms=100),
        stt_backend=stt_backend,
        tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
        prompt_builder=PromptBuilderV2(),
        prompt_executor=PromptExecutor(
            StaticChatBackend(["この返答は出てはいけない"]),
            StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
        ),
        speech_rms_threshold=0.02,
    )

    assert await conversation.process_audio_samples((0.2,) * 100) is None
    assert await conversation.process_audio_samples((0.0,) * 100) is None
    result = await conversation.process_audio_samples((0.0,) * 100)

    assert result is not None
    assert result.observations[0].text == "はい"
    assert result.durable_utterance is None
    assert result.prompt_request is None
    assert result.execution_result.audio_chunks == []
    captured = capsys.readouterr()
    assert "stt_hallucination_blocked" in captured.out
    assert "text='はい'" in captured.out


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


@pytest.mark.asyncio
async def test_voicevox_audio_query_uses_configured_fast_speech_speed() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        async def post(self, url: str, *, params: dict[str, object]) -> FakeResponse:
            assert url == "http://voicevox/audio_query"
            assert params == {"text": "こんにちは", "speaker": 8}
            return FakeResponse()

    backend = VoicevoxChunkedTtsBackend(url="http://voicevox", speed=1.5)
    audio_query = await backend._audio_query(FakeClient(), "こんにちは")

    assert audio_query["speedScale"] == 1.5
    assert audio_query["outputSamplingRate"] == 24000
    assert audio_query["outputStereo"] is False


def test_default_real_prompt_executor_uses_voicevox_fast_speech_speed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TOMOKO_V2_VOICEVOX_SPEED", raising=False)
    executor = create_default_real_prompt_executor()

    assert executor._tts_backend.speed == 1.5


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


def test_tomoko_blocks_current_log_based_stt_hallucinations() -> None:
    now = utc_now()
    core = TomokoProcessCore(SessionBoundaryModel())
    for text in ["", "  ", "はい", "い"]:
        observation = __import__("server.shared.models").shared.models.PartialTranscriptObservation(
            text=text,
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
        assert core.adopt_final_observation(observation) is None

    assert normalize_stt_block_text(" は い ") == "はい"


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
    assert "SYSTEM:\nTomoko v2: natural local voice conversation." in prompt.prompt_text
    assert "SESSION_TRANSCRIPT:\nuser: raw text\nuser: いま何時?" in prompt.prompt_text
    assert "INSTRUCTION:\n次のtomoko発話だけ返す。" in prompt.prompt_text
    assert prompt.prompt_text.endswith("SESSION_TRANSCRIPT:\nuser: raw text\nuser: いま何時?")
    assert "calendar[" not in prompt.prompt_text


def test_prompt_builder_uses_append_only_session_transcript() -> None:
    session_id = __import__("uuid").uuid4()
    snapshot = ContextSnapshotBuilderV2().build(
        session_id=session_id,
        recent_utterances=["こんにちは"],
        recent_history=[
            ConversationHistoryItem(speaker="user", text="こんにちは"),
            ConversationHistoryItem(speaker="tomoko", text="聞こえてるよ"),
        ],
        summaries=[],
        calendar_loader=lambda: {},
        user_status=None,
        candidates=[],
    )
    prompt = PromptBuilderV2().build_main_reply(snapshot, "俺って聞こえてる")

    assert "SESSION_TRANSCRIPT:" in prompt.prompt_text
    assert "user: こんにちは\ntomoko: 聞こえてるよ\nuser: 俺って聞こえてる" in (
        prompt.prompt_text
    )
    assert "CURRENT_USER_UTTERANCE" not in prompt.prompt_text
    assert "recent_user_raw" not in prompt.prompt_text


def test_prompt_builder_next_turn_keeps_previous_prompt_as_prefix() -> None:
    session_id = __import__("uuid").uuid4()
    first_snapshot = ContextSnapshotBuilderV2().build(
        session_id=session_id,
        recent_utterances=[],
        summaries=[],
        calendar_loader=lambda: {},
        user_status=None,
        candidates=[],
    )
    first = PromptBuilderV2().build_main_reply(first_snapshot, "最初に短く返事して")
    second_snapshot = ContextSnapshotBuilderV2().build(
        session_id=session_id,
        recent_utterances=[],
        recent_history=[
            ConversationHistoryItem(speaker="user", text="最初に短く返事して"),
            ConversationHistoryItem(speaker="tomoko", text="了解。"),
        ],
        summaries=[],
        calendar_loader=lambda: {},
        user_status=None,
        candidates=[],
    )
    second = PromptBuilderV2().build_main_reply(second_snapshot, "続きも一言で")

    assert second.prompt_text.startswith(first.prompt_text)
    assert second.prompt_text[len(first.prompt_text):].startswith("\ntomoko: 了解。")


def test_session_transcript_prompt_is_sent_as_chat_roles() -> None:
    request = PromptRequest(
        prompt_text=(
            "SYSTEM:\nTomoko v2: natural local voice conversation.\n"
            "INSTRUCTION:\n次のtomoko発話だけ返す。\n"
            "SESSION_TRANSCRIPT:\n"
            "user: 最初に短く返事して\n"
            "tomoko: 了解。\n"
            "user: 続きも一言で"
        ),
        scope=PromptScope.MAIN,
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=50,
        cancel_policy=CancelPolicy.CANCEL_ON_USER_SPEAKING,
    )

    for messages_for_request in (tomoko_messages_for_request, hot_path_messages_for_request):
        messages = messages_for_request(request)
        assert messages == [
            {
                "role": "system",
                "content": (
                    "Tomoko v2: natural local voice conversation.\n"
                    "次のtomoko発話だけ返す。"
                ),
            },
            {"role": "user", "content": "最初に短く返事して"},
            {"role": "assistant", "content": "了解。"},
            {"role": "user", "content": "続きも一言で"},
        ]


@pytest.mark.asyncio
async def test_hot_path_puts_previous_tomoko_reply_in_next_prompt() -> None:
    stt_backend = _RecordingSttBackend(text="こんにちは")
    conversation = HotPathAudioConversation(
        vad=VADProcessor(sample_rate=1000, pre_roll_ms=300, silence_ms=100),
        stt_backend=stt_backend,
        tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
        prompt_builder=PromptBuilderV2(),
        prompt_executor=PromptExecutor(
            StaticChatBackend(["聞こえてるよ"]),
            StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
        ),
        speech_rms_threshold=0.02,
    )

    assert await conversation.process_audio_samples((0.2,) * 100) is None
    assert await conversation.process_audio_samples((0.0,) * 100) is None
    first = await conversation.process_audio_samples((0.0,) * 100)
    assert first is not None

    stt_backend.text = "俺って聞こえてる"
    assert await conversation.process_audio_samples((0.2,) * 100) is None
    assert await conversation.process_audio_samples((0.0,) * 100) is None
    second = await conversation.process_audio_samples((0.0,) * 100)

    assert second is not None
    assert second.prompt_request is not None
    assert "user: こんにちは" in second.prompt_request.prompt_text
    assert "tomoko: 聞こえてるよ" in second.prompt_request.prompt_text
    assert "user: 俺って聞こえてる" in second.prompt_request.prompt_text


@pytest.mark.asyncio
async def test_prompt_executor_requires_complete_wav_chunks(
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    captured = capsys.readouterr()
    assert "prompt_send" in captured.out
    assert "----- TOMOKO LLM PROMPT BEGIN -----" in captured.out
    assert "hello" in captured.out
    bad = PromptExecutor(StaticChatBackend(["x"]), StaticWavTtsBackend([b"not wav"]))
    with pytest.raises(ValueError):
        await bad.execute(request)
