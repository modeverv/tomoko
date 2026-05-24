from __future__ import annotations

import logging
from uuid import UUID

from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.inference.router import InferenceRouter
from server.shared.memory import ConversationSessionSummaryStore
from server.shared.models import ConversationTurn

logger = logging.getLogger(__name__)


SUMMARY_SYSTEM_PROMPT = """\
あなたは Tomoko の会話セッションを検索用の索引カードに要約する係です。
会話の原本は別に保存されているので、ここでは後から思い出すための短い要約だけを書いてください。

ルール:
- 日本語で書く
- 2文以内
- 人間が話した要望、約束、好み、重要な固有名詞を優先する
- 推測で事実を足さない
- 挨拶だけ、または内容が薄い場合も「何について話したか」を短く書く
"""


class SessionSummarizer:
    def __init__(
        self,
        *,
        session_summary_store: ConversationSessionSummaryStore,
        router: InferenceRouter,
        embedding_backend: EmbeddingBackend,
    ) -> None:
        self.session_summary_store = session_summary_store
        self.router = router
        self.embedding_backend = embedding_backend

    async def process_pending(self, *, limit: int = 10) -> int:
        session_ids = await self.session_summary_store.claim_pending_sessions(limit=limit)
        for session_id in session_ids:
            await self._process_one(session_id)
        return len(session_ids)

    async def _process_one(self, session_id: UUID) -> None:
        try:
            turns = await self.session_summary_store.read_session_turns(
                session_id=session_id
            )
            summary_text, summary_model = await self._summarize(turns)
            embedding = await self.embedding_backend.embed_passage(summary_text)
            await self.session_summary_store.complete_summary(
                session_id=session_id,
                summary_text=summary_text,
                summary_model=summary_model,
                embedding=embedding,
                embedding_model=self.embedding_backend.model,
            )
            logger.info(
                "SessionSummarizer completed session_id=%s summary_chars=%s model=%s",
                session_id,
                len(summary_text),
                summary_model,
            )
        except Exception as e:
            await self.session_summary_store.mark_summary_error(
                session_id=session_id,
                error=str(e),
            )
            logger.warning(
                "SessionSummarizer failed session_id=%s error=%s",
                session_id,
                e,
            )

    async def _summarize(self, turns: list[ConversationTurn]) -> tuple[str, str]:
        transcript_text = _format_turns_for_summary(turns)
        backend = await self.router.select("session_summary", "privacy")
        chunks: list[str] = []
        async for chunk in backend.chat_stream(
            SUMMARY_SYSTEM_PROMPT,
            [{"role": "user", "content": transcript_text}],
        ):
            chunks.append(chunk)
        summary_text = _clean_summary("".join(chunks))
        if not summary_text:
            return "短い会話があった。", backend.name
        return summary_text, backend.name


def _format_turns_for_summary(turns: list[ConversationTurn]) -> str:
    if not turns:
        return "会話ターンは保存されていません。"
    return "\n".join(_format_turn(turn) for turn in turns if turn.text.strip())


def _format_turn(turn: ConversationTurn) -> str:
    speaker = "ユーザー" if turn.speaker == "user" else "トモコ"
    return f"{speaker}: {turn.text.strip()}"


def _clean_summary(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines).strip()
