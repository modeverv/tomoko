from __future__ import annotations

from uuid import uuid4

import pytest

from server.shared.models import PartialTranscriptObservation, SessionSummary, utc_now
from server.summary.main import embed_text
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.recall import rank_summaries_by_relevance
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel

pytestmark = pytest.mark.unit


def _summary(keyword: str, conclusion: str) -> SessionSummary:
    return SessionSummary(
        session_id=uuid4(),
        keyword=keyword,
        conclusion=conclusion,
        embedding=embed_text(keyword + " " + conclusion),
    )


def test_rank_summaries_puts_related_topic_first() -> None:
    weather = _summary("天気", "明日の天気は雨になりそうという話をした")
    lunch = _summary("昼ごはん", "昼ごはんはラーメンがおすすめという結論になった")
    meeting = _summary("会議", "夕方の定例会議の準備を進めることにした")

    ranked = rank_summaries_by_relevance(
        [meeting, lunch, weather],
        "明日の天気がどうなるか教えて",
        top_k=2,
    )

    assert ranked
    assert ranked[0].keyword == "天気"


def test_rank_summaries_falls_back_to_recency_for_dim_mismatch() -> None:
    stale = SessionSummary(
        session_id=uuid4(),
        keyword="old",
        conclusion="旧embedderの8次元行",
        embedding=(0.1,) * 8,
    )
    ranked = rank_summaries_by_relevance([stale], "関係ない質問", top_k=2)
    assert ranked == [stale]


class CountingChatBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield self.replies.pop(0) if self.replies else "fallback"


@pytest.mark.asyncio
async def test_core_injects_recalled_summaries_into_prompt() -> None:
    now = utc_now()
    chat = CountingChatBackend(["昨日は雨の話をしたね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )
    core.update_summary_records(
        [_summary("天気", "明日の天気は雨になりそうという話をした")]
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、昨日の話の続きをして",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.prompt_request is not None
    assert "summary[天気]=明日の天気は雨になりそうという話をした" in (
        result.prompt_request.prompt_text
    )
