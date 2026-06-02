from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking import fast
from server.gateway.thinking.fast import ThinkFastMode
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import (
    AttentionMode,
    CalendarEvent,
    ContextBuildTrace,
    ConversationTurn,
    ParticipationMode,
    ResearchContextHit,
    SpeechSegment,
    TaskLedgerEntry,
    ThinkingEvent,
    ThinkingInput,
    TomokoContextSnapshot,
    Transcript,
)

ROOT = Path(__file__).resolve().parents[2]
JST = timezone(timedelta(hours=9), "JST")


def fixed_now() -> datetime:
    return datetime(2026, 5, 30, 12, 34, 56, tzinfo=JST)


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        score = self.scores[self.index]
        self.index += 1
        return score


class ConstantTranscriber:
    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        return Transcript(
            text="トモコ、聞こえる？",
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


class InMemoryAmbientLogWriter:
    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None:
        del attention_mode, attended, participation_mode
        self.transcript = transcript
        self.tomoko_participated = tomoko_participated


class InMemoryConversationLogWriter:
    def __init__(self, history: list[ConversationTurn] | None = None) -> None:
        self.history = history or []
        self.user_turns: list[tuple[Transcript, ParticipationMode]] = []
        self.tomoko_turns: list[tuple[str, str, str]] = []

    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
    ) -> None:
        self.user_turns.append((transcript, participation_mode))
        self.history.append(
            ConversationTurn(
                speaker="user",
                text=transcript.text,
                timestamp=transcript.recorded_at,
            )
        )

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: str = "completed",
    ) -> None:
        del device_id
        self.tomoko_turns.append((text, emotion, status))
        self.history.append(
            ConversationTurn(
                speaker="tomoko",
                text=text,
                timestamp=datetime.now(UTC),
                emotion=emotion,
            )
        )

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        return self.history[-limit:]


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.system_prompt: str | None = None
        self.messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        self.system_prompt = system_prompt
        self.messages = messages
        for chunk in self.chunks:
            yield chunk


class FakeRouter:
    def __init__(self, backend: InferenceBackend) -> None:
        self.backend = backend
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        self.selections.append((role, preference))
        return self.backend


@pytest.mark.unit
def test_base_persona_contains_voice_conversation_rules() -> None:
    prompt = (ROOT / "prompts" / "base_persona.md").read_text(encoding="utf-8")

    assert "音声会話" in prompt
    assert "聞き取れなかった" in prompt
    assert "確認して" in prompt
    assert "開発中のTomoko" in prompt
    assert "EMOTION:<emotion>" in prompt
    assert "プログラム側で未定義" in prompt
    assert "playful" in prompt


@pytest.mark.unit
def test_persona_overlay_file_is_readable_when_present() -> None:
    overlay_path = ROOT / "prompts" / "persona_overlay.md"
    if not overlay_path.exists():
        return

    prompt = overlay_path.read_text(encoding="utf-8")

    assert isinstance(prompt, str)


@pytest.mark.unit
async def test_think_fast_includes_persona_overlay_when_sibling_file_exists(
    tmp_path,
) -> None:
    persona = tmp_path / "base_persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    overlay = tmp_path / "persona_overlay.md"
    overlay.write_text(
        "## PERSONA OVERLAY\n少し茶目っ気のある後輩として短く助ける。",
        encoding="utf-8",
    )
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=None,
        now_provider=fixed_now,
    )

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、軽く相談に乗って",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert backend.system_prompt.startswith("あなたはトモコです。")
    assert "## PERSONA OVERLAY" in backend.system_prompt
    assert "少し茶目っ気のある後輩として短く助ける。" in backend.system_prompt
    assert "現在日時: 2026-05-30 12:34:56 JST" in backend.system_prompt


@pytest.mark.unit
async def test_think_fast_omits_persona_overlay_when_sibling_file_is_missing(
    tmp_path,
) -> None:
    persona = tmp_path / "base_persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=None,
        now_provider=fixed_now,
    )

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、聞こえる？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert "PERSONA OVERLAY" not in backend.system_prompt


@pytest.mark.unit
async def test_think_fast_wraps_streamed_tokens_in_thinking_events(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん", "、聞こえるよ"])
    mode = ThinkFastMode(persona_path=persona, now_provider=fixed_now)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、聞こえる？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="text_delta", value="うん"),
        ThinkingEvent(type="text_delta", value="、聞こえるよ"),
        ThinkingEvent(type="done", value=""),
    ]
    assert backend.system_prompt is not None
    assert backend.system_prompt.startswith("あなたはトモコです。")
    assert "現在日時: 2026-05-30 12:34:56 JST" in backend.system_prompt
    assert "曜日: 土曜日" in backend.system_prompt
    assert backend.messages == [{"role": "user", "content": "トモコ、聞こえる？"}]


@pytest.mark.unit
async def test_think_fast_includes_recent_conversation_context(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(persona_path=persona, now_provider=fixed_now)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="さっき言ったカレーの続きだけど",
                speaker=None,
                context=[
                    ConversationTurn(
                        speaker="user",
                        text="昨日カレーを作ったよ",
                        timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
                    ),
                    ConversationTurn(
                        speaker="tomoko",
                        text="いいね、少し寝かせるとおいしいよ。",
                        timestamp=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
                        emotion="happy",
                    ),
                ],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events[-1] == ThinkingEvent(type="done", value="")
    assert backend.messages == [
        {"role": "user", "content": "昨日カレーを作ったよ"},
        {"role": "assistant", "content": "いいね、少し寝かせるとおいしいよ。"},
        {"role": "user", "content": "さっき言ったカレーの続きだけど"},
    ]


@pytest.mark.unit
async def test_think_fast_logs_llm_prompt_payload(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    prompt_log_path = tmp_path / "conversation-prompts.jsonl"
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=prompt_log_path,
        now_provider=fixed_now,
    )
    log_calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_info(message: str, *args: object) -> None:
        log_calls.append((message, args))

    monkeypatch.setattr(fast.logger, "info", fake_info)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、今のプロンプト見せて",
                speaker=None,
                context=[
                    ConversationTurn(
                        speaker="tomoko",
                        text="うん、準備できてるよ。",
                        timestamp=datetime(2026, 5, 25, 10, 0, tzinfo=UTC),
                        emotion="happy",
                    )
                ],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events[-1] == ThinkingEvent(type="done", value="")
    message, args = log_calls[0]
    assert message == "ThinkFastMode llm_prompt backend=%s payload=%s"
    assert args[0] == "fake"
    payload = str(args[1])
    assert '"system_prompt": "あなたはトモコです。\\n\\n## CURRENT LOCAL TIME' in payload
    assert "現在日時: 2026-05-30 12:34:56 JST" in payload
    assert "曜日: 土曜日" in payload
    assert '"role": "assistant", "content": "うん、準備できてるよ。"' in payload
    assert '"role": "user", "content": "トモコ、今のプロンプト見せて"' in payload
    assert '"device_id": "browser"' in payload

    prompt_log_lines = prompt_log_path.read_text(encoding="utf-8").splitlines()
    assert len(prompt_log_lines) == 1
    prompt_log_payload = prompt_log_lines[0]
    assert '"backend": "fake"' in prompt_log_payload
    assert '"system_prompt": "あなたはトモコです。\\n\\n## CURRENT LOCAL TIME' in prompt_log_payload
    assert '"role": "user", "content": "トモコ、今のプロンプト見せて"' in prompt_log_payload


@pytest.mark.unit
async def test_think_fast_includes_calendar_context_from_snapshot(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=None,
        now_provider=fixed_now,
    )
    trace = ContextBuildTrace(
        budget_ms=100,
        elapsed_ms=1.0,
        timed_out=False,
        depth="deep",
        included_counts={"calendar_events": 1},
        skipped_sources=[],
        stage_timings_ms={},
        cache_hits={},
        source_errors={},
    )
    snapshot = TomokoContextSnapshot(
        depth="deep",
        recent_turns=[],
        session_summaries=[],
        memory_hits=[],
        lexicon_terms=[],
        persona_slice=None,
        token_budget_hint=2600,
        build_elapsed_ms=1.0,
        source_counts={"calendar_events": 1},
        trace=trace,
        calendar_events=[
            CalendarEvent(
                source_id="gcal",
                uid="meeting@example.com",
                summary="家族の予定",
                start_time=datetime(2026, 5, 30, 4, 0, tzinfo=UTC),
                end_time=datetime(2026, 5, 30, 5, 0, tzinfo=UTC),
                all_day=False,
                location="Kitchen",
            )
        ],
    )

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="今日の予定ある？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                context_snapshot=snapshot,
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert "CALENDAR CONTEXT" in backend.system_prompt
    assert "2026-05-30 13:00-14:00: 家族の予定 @ Kitchen" in backend.system_prompt


@pytest.mark.unit
async def test_think_fast_includes_research_summary_context_from_snapshot(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=None,
        now_provider=fixed_now,
    )
    trace = ContextBuildTrace(
        budget_ms=100,
        elapsed_ms=1.0,
        timed_out=False,
        depth="deep",
        included_counts={"research_results": 1},
        skipped_sources=[],
        stage_timings_ms={},
        cache_hits={},
        source_errors={},
    )
    snapshot = TomokoContextSnapshot(
        depth="deep",
        recent_turns=[],
        session_summaries=[],
        memory_hits=[],
        lexicon_terms=[],
        persona_slice=None,
        token_budget_hint=2600,
        build_elapsed_ms=1.0,
        source_counts={"research_results": 1},
        trace=trace,
        research_results=[
            ResearchContextHit(
                result_id="research-openai",
                query="今日のOpenAI関連ニュースを短く",
                summary_text="OpenAIに関する外部調査の要約。",
                provider="perplexity",
                fetched_at=datetime(2026, 5, 31, 10, 0, tzinfo=UTC),
                similarity=0.95,
                citation_urls=("https://example.com/openai",),
            )
        ],
    )

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="OpenAIについて知ってることある？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                context_snapshot=snapshot,
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert "RESEARCH CONTEXT" in backend.system_prompt
    assert "summary=OpenAIに関する外部調査の要約。" in backend.system_prompt


@pytest.mark.unit
async def test_think_fast_includes_task_context_from_snapshot(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=None,
        now_provider=fixed_now,
    )
    trace = ContextBuildTrace(
        budget_ms=20,
        elapsed_ms=1.0,
        timed_out=False,
        depth="fast",
        included_counts={"task_ledger": 1},
        skipped_sources=[],
        stage_timings_ms={},
        cache_hits={},
        source_errors={},
    )
    snapshot = TomokoContextSnapshot(
        depth="fast",
        recent_turns=[],
        session_summaries=[],
        memory_hits=[],
        lexicon_terms=[],
        persona_slice=None,
        token_budget_hint=1200,
        build_elapsed_ms=1.0,
        source_counts={"task_ledger": 1},
        trace=trace,
        task_ledger_entries=[
            TaskLedgerEntry(
                task_id="task-1",
                title="server-debug の起動確認",
                status="active",
                priority=80,
                created_at=datetime(2026, 6, 2, 9, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 2, 9, 5, tzinfo=UTC),
                due_at=datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
                source="voice",
            )
        ],
    )

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="今残ってるタスクは？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                context_snapshot=snapshot,
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert "TASK CONTEXT" in backend.system_prompt
    assert "server-debug の起動確認" in backend.system_prompt
    assert "status=active" in backend.system_prompt


@pytest.mark.unit
async def test_think_fast_includes_response_directive(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(
        persona_path=persona,
        prompt_log_path=None,
        now_provider=fixed_now,
    )

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="OpenAIについて調べて",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                response_directive="調査結果を答えず、調べ始めたことだけを伝える。",
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert "RESPONSE DIRECTIVE" in backend.system_prompt
    assert "調査結果を答えず、調べ始めたことだけを伝える。" in backend.system_prompt


@pytest.mark.unit
async def test_think_fast_extracts_emotion_line_before_text(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["EMO", "TION:happy\nうん", "、聞こえるよ。"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、聞こえる？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="happy"),
        ThinkingEvent(type="text_delta", value="うん"),
        ThinkingEvent(type="text_delta", value="、聞こえるよ。"),
        ThinkingEvent(type="done", value=""),
    ]


@pytest.mark.unit
async def test_think_fast_extracts_emotion_prefix_without_newline(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["EMOTION:happy 今日は元気いっぱいだよ！"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、聞こえる？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="happy"),
        ThinkingEvent(type="text_delta", value="今日は元気いっぱいだよ！"),
        ThinkingEvent(type="done", value=""),
    ]


@pytest.mark.unit
async def test_think_fast_suppresses_unknown_emotion_line_before_text(
    tmp_path,
) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["EMOTION:playful\nふふ", "、了解。"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、全部あとでいいって言って",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="neutral"),
        ThinkingEvent(type="text_delta", value="ふふ"),
        ThinkingEvent(type="text_delta", value="、了解。"),
        ThinkingEvent(type="done", value=""),
    ]


@pytest.mark.unit
async def test_think_fast_suppresses_unknown_inline_emotion_before_text(
    tmp_path,
) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["EMOTION:playful ふふ、了解。"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、全部あとでいいって言って",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="neutral"),
        ThinkingEvent(type="text_delta", value="ふふ、了解。"),
        ThinkingEvent(type="done", value=""),
    ]


@pytest.mark.unit
async def test_session_streams_reply_text_after_wake_word() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["うん", "、聞こえるよ"])
    router = FakeRouter(backend)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())
    await session._wait_for_reply_task()

    assert router.selections == [("conversation", "privacy")]
    assert {"type": "participation", "mode": "called"} in events
    assert {"type": "reply_text", "delta": "うん"} in events
    assert {"type": "reply_text", "delta": "、聞こえるよ"} in events
    assert {"type": "reply_done"} in events
    assert {"type": "state", "state": "idle"} in events


@pytest.mark.unit
async def test_session_passes_recent_conversation_context_to_thinking_mode() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["うん、覚えてるよ。"])
    router = FakeRouter(backend)
    history = [
        ConversationTurn(
            speaker="user",
            text="昨日カレーを作ったよ",
            timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
        ),
        ConversationTurn(
            speaker="tomoko",
            text="明日は少し味がなじむかも。",
            timestamp=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
            emotion="happy",
        ),
    ]
    conversation_logs = InMemoryConversationLogWriter(history=history)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=conversation_logs,
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())
    await session._wait_for_reply_task()

    assert backend.messages == [
        {"role": "user", "content": "昨日カレーを作ったよ"},
        {"role": "assistant", "content": "明日は少し味がなじむかも。"},
        {"role": "user", "content": "トモコ、聞こえる？"},
    ]


@pytest.mark.unit
async def test_session_includes_last_initiative_text_when_user_asks_followup() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["それはね、端末側で動く専用チップの話だよ。"])
    router = FakeRouter(backend)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=InMemoryConversationLogWriter(),
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )
    await session.start_precomputed_reply(
        text="さっきの話とは別で、ハードウェアの進化が少し気になってるんだ。",
        device_id="desk",
        reason="initiative",
        candidate_source="world_observation:abc",
        candidate_id="candidate-1",
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())
    await session._wait_for_reply_task()

    assert backend.messages is not None
    assert backend.messages[0] == {
        "role": "assistant",
        "content": "さっきの話とは別で、ハードウェアの進化が少し気になってるんだ。",
    }
    assert backend.messages[-1] == {"role": "user", "content": "トモコ、聞こえる？"}


@pytest.mark.unit
async def test_session_sends_emotion_event_after_wake_word() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["EMOTION:surprised\n", "え、そうなんだ。"])
    router = FakeRouter(backend)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())
    await session._wait_for_reply_task()

    assert {
        "type": "emotion",
        "value": "surprised",
        "image": "/assets/images/tomoko-surprised.svg",
    } in events
    assert {"type": "reply_text", "delta": "え、そうなんだ。"} in events
