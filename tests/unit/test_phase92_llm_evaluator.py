from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from server.shared.candidate import (
    CandidateSeed,
    InMemoryCandidateStore,
    ThinkerEvaluationContext,
)
from server.shared.config import (
    AudioSection,
    BackendSpec,
    DatabaseSection,
    InferenceSection,
    NodeConfig,
    NodeSection,
)
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.router import InferenceRouter
from server.thinker.evaluator.llm import LLMUtteranceEvaluator


class FakeBackend(InferenceBackend):
    def __init__(self, chunks: list[str]) -> None:
        self.name = "fake"
        self.privacy_allowed = True
        self.chunks = chunks
        self.system_prompt = ""
        self.messages: list[dict[str, str]] = []

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        self.system_prompt = system_prompt
        self.messages = messages
        for chunk in self.chunks:
            yield chunk


class RecordingRouter:
    def __init__(self, backend: InferenceBackend | None = None) -> None:
        self.backend = backend or FakeBackend([])
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        self.selections.append((role, preference))
        return self.backend


def _seed() -> CandidateSeed:
    return CandidateSeed(
        seed_text="夕方になったら洗濯物の話をする",
        source="unit",
        priority=0.4,
        urgent=False,
        expires_at=datetime(2026, 5, 24, 18, 0, tzinfo=UTC),
        dedupe_key="unit:laundry:2026-05-24",
        context_tags=("time_of_day:evening",),
    )


def _context() -> ThinkerEvaluationContext:
    return ThinkerEvaluationContext(
        observed_at=datetime(2026, 5, 24, 17, 30, tzinfo=UTC),
        device_id="kitchen",
        recent_summary="今日は洗濯物の話をしていた",
        session_summaries=("昼に洗濯を回した。",),
        lexicon_terms=("洗濯物: 夕方に取り込む必要がある",),
        persona_notes=("短く、押しつけない言い方を好む",),
    )


@pytest.mark.unit
async def test_llm_evaluator_builds_evaluated_utterance_from_json() -> None:
    backend = FakeBackend(
        [
            '{"should_keep": true, "generated_text": "洗濯物、そろそろ見ておく？", ',
            '"priority": 0.82, "urgent": true, "reason": "夕方で関連がある"}',
        ]
    )
    router = RecordingRouter(backend)
    evaluator = LLMUtteranceEvaluator(router=router)  # type: ignore[arg-type]

    evaluated = await evaluator.evaluate(_seed(), _context())

    assert evaluated is not None
    assert evaluated.should_keep is True
    assert evaluated.generated_text == "洗濯物、そろそろ見ておく？"
    assert evaluated.priority == 0.82
    assert evaluated.urgent is True
    assert evaluated.context_tags == (
        "dedupe:unit:laundry:2026-05-24",
        "time_of_day:evening",
        "evaluated_by:llm",
    )
    assert router.selections == [("candidate_gen", "privacy")]
    assert "conversation_logs" not in backend.messages[0]["content"]
    assert "会話開始用の短文" in backend.system_prompt
    assert "別件" in backend.system_prompt


@pytest.mark.unit
async def test_should_keep_false_is_not_saved() -> None:
    router = RecordingRouter(
        FakeBackend(
            [
                '{"should_keep": false, "generated_text": null, "priority": 0.1, ',
                '"urgent": false, "reason": "今は話しかけない方がよい"}',
            ]
        )
    )
    evaluator = LLMUtteranceEvaluator(router=router)  # type: ignore[arg-type]
    store = InMemoryCandidateStore()
    seed = _seed()

    evaluated = await evaluator.evaluate(seed, _context())
    saved = await store.insert_evaluated_utterance_once(seed, evaluated)

    assert evaluated is not None
    assert evaluated.should_keep is False
    assert saved is None
    assert await store.fetch_active_utterance_candidates(
        now=datetime(2026, 5, 24, 17, 30, tzinfo=UTC),
        limit=10,
    ) == []


@pytest.mark.unit
async def test_malformed_json_is_discarded_without_raising() -> None:
    router = RecordingRouter(FakeBackend(["not json"]))
    evaluator = LLMUtteranceEvaluator(router=router)  # type: ignore[arg-type]

    assert await evaluator.evaluate(_seed(), _context()) is None


@pytest.mark.unit
async def test_evaluated_utterance_is_saved_as_maturity_one_candidate() -> None:
    seed = _seed()
    evaluated = await LLMUtteranceEvaluator(
        router=RecordingRouter(
            FakeBackend(
                [
                    '{"should_keep": true, "generated_text": "洗濯物、そろそろ見ておく？", ',
                    '"priority": 0.7, "urgent": false, "reason": "生活文脈に合う"}',
                ]
            )
        )  # type: ignore[arg-type]
    ).evaluate(seed, _context())
    store = InMemoryCandidateStore()

    saved = await store.insert_evaluated_utterance_once(
        seed,
        evaluated,
        created_at=datetime(2026, 5, 24, 17, 30, tzinfo=UTC),
    )

    assert saved is not None
    assert saved.maturity == 1
    assert saved.seed == seed.seed_text
    assert saved.generated_text == "洗濯物、そろそろ見ておく？"
    assert saved.priority == 0.7


@pytest.mark.unit
async def test_llm_evaluator_discards_fragmentary_generated_text() -> None:
    router = RecordingRouter(
        FakeBackend(
            [
                '{"should_keep": true, "generated_text": "を動かすための専用チップ", ',
                '"priority": 0.7, "urgent": false, "reason": "主語がない"}',
            ]
        )
    )
    evaluator = LLMUtteranceEvaluator(router=router)  # type: ignore[arg-type]

    assert await evaluator.evaluate(_seed(), _context()) is None


@pytest.mark.unit
async def test_llm_evaluator_requires_prompt_side_bridge_for_world_observation() -> None:
    seed = CandidateSeed(
        seed_text="ハードウェアの進化について気になっている",
        source="world_observation:abc",
        priority=0.7,
        urgent=False,
        expires_at=datetime(2026, 5, 24, 18, 0, tzinfo=UTC),
        dedupe_key="world:hardware",
        context_tags=("topic:hardware",),
    )
    backend = FakeBackend(
        [
            '{"should_keep": true, '
            '"generated_text": "ハードウェアの進化、少し気になってるんだよね。", ',
            '"priority": 0.7, "urgent": false, "reason": "別件話題"}',
        ]
    )
    router = RecordingRouter(backend)
    evaluator = LLMUtteranceEvaluator(router=router)  # type: ignore[arg-type]

    evaluated = await evaluator.evaluate(seed, _context())

    assert evaluated is not None
    assert evaluated.generated_text is not None
    assert evaluated.generated_text == "ハードウェアの進化、少し気になってるんだよね。"
    assert "さっきの話とは別で" in backend.system_prompt


@pytest.mark.unit
async def test_router_supports_candidate_gen_privacy_role() -> None:
    config = NodeConfig(
        node=NodeSection(role="central_background"),
        inference=InferenceSection(
            conversation_backend="cloud",
            conversation_fallback=None,
            candidate_gen_backend="cloud",
            candidate_gen_fallback="local",
            session_summary_backend=None,
            session_summary_fallback=None,
            stt_backend=None,
            vad_backend=None,
            tts_backend="say",
        ),
        backends={
            "cloud": BackendSpec(
                name="cloud",
                type="ollama",
                url="http://localhost:11434",
                model="cloud-model",
                max_latency_ms=1,
                privacy_allowed=False,
            ),
            "local": BackendSpec(
                name="local",
                type="ollama",
                url="http://localhost:11434",
                model="local-model",
                max_latency_ms=300,
                privacy_allowed=True,
            ),
        },
        audio=AudioSection(sample_rate=16000, chunk_ms=32, vad_silence_ms=400),
        database=DatabaseSection(dsn="postgresql://tomoko:tomoko@localhost:5432/tomoko"),
    )

    backend = await InferenceRouter(config).select("candidate_gen", "privacy")

    assert backend.name == "local"
