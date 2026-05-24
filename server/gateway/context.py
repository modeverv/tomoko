from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from server.gateway.context_assembly import (
    assemble_recent_turns,
    filter_current_memory_hits,
)
from server.shared.db import ConversationLogWriter
from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.memory import ConversationMemoryStore, ConversationSessionSummaryStore
from server.shared.models import (
    ContextBuildPolicy,
    ContextBuildTrace,
    LexiconTerm,
    MemoryHit,
    PersonaPromptSlice,
    SessionSummaryHit,
    TomokoContextSnapshot,
)
from server.shared.persona import PersonaSnapshotStore

logger = logging.getLogger(__name__)


class ContextSnapshotBuilder:
    def __init__(
        self,
        *,
        conversation_log_reader: ConversationLogWriter | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        memory_store: ConversationMemoryStore | None = None,
        session_summary_store: ConversationSessionSummaryStore | None = None,
        persona_store: PersonaSnapshotStore | None = None,
    ) -> None:
        self.conversation_log_reader = conversation_log_reader
        self.embedding_backend = embedding_backend
        self.memory_store = memory_store
        self.session_summary_store = session_summary_store
        self.persona_store = persona_store

    async def build(
        self,
        *,
        text: str,
        speaker: str | None,
        device_id: str,
        active_session_id: UUID | None,
        policy: ContextBuildPolicy | None = None,
    ) -> TomokoContextSnapshot:
        del speaker, device_id
        policy = policy or ContextBuildPolicy.for_depth("fast")
        started_at = time.perf_counter()
        stage_timings_ms: dict[str, float] = {}
        source_errors: dict[str, str] = {}
        skipped_sources: list[str] = []

        tasks: dict[str, asyncio.Task[Any]] = {}
        if self.conversation_log_reader is not None:
            read_session_turns = getattr(
                self.conversation_log_reader,
                "read_recent_turns_for_session",
                None,
            )
            if active_session_id is not None and read_session_turns is not None:
                tasks["same_session_turns"] = asyncio.create_task(
                    self._timed(
                        "same_session_turns",
                        stage_timings_ms,
                        source_errors,
                        lambda: read_session_turns(
                            conversation_session_id=active_session_id,
                            limit=policy.max_same_session_turns + 1,
                        ),
                    )
                )
            read_recent_turns = getattr(
                self.conversation_log_reader,
                "read_recent_turns",
                None,
            )
            if read_recent_turns is not None:
                tasks["recent_turns"] = asyncio.create_task(
                    self._timed(
                        "recent_turns",
                        stage_timings_ms,
                        source_errors,
                        lambda: read_recent_turns(limit=policy.max_recent_turns + 1),
                    ),
                )

        if (
            policy.max_session_summaries > 0
            and self.embedding_backend is not None
            and self.session_summary_store is not None
        ):
            tasks["session_summaries"] = asyncio.create_task(
                self._timed(
                    "session_summaries",
                    stage_timings_ms,
                    source_errors,
                    lambda: self._search_session_summaries(
                        text, policy.max_session_summaries
                    ),
                )
            )
        elif policy.max_session_summaries > 0:
            skipped_sources.append("session_summaries")

        if (
            policy.allow_turn_memory_search
            and policy.max_memory_hits > 0
            and self.embedding_backend is not None
            and self.memory_store is not None
        ):
            tasks["memory_hits"] = asyncio.create_task(
                self._timed(
                    "memory_hits",
                    stage_timings_ms,
                    source_errors,
                    lambda: self._search_memory_hits(text, policy.max_memory_hits),
                )
            )
        elif policy.allow_turn_memory_search and policy.max_memory_hits > 0:
            skipped_sources.append("memory_hits")

        if policy.allow_persona_slice and self.persona_store is not None:
            tasks["lexicon_terms"] = asyncio.create_task(
                self._timed(
                    "lexicon_terms",
                    stage_timings_ms,
                    source_errors,
                    lambda: self._read_lexicon_terms(
                        text, policy.max_lexicon_terms
                    ),
                )
            )
            tasks["persona_slice"] = asyncio.create_task(
                self._timed(
                    "persona_slice",
                    stage_timings_ms,
                    source_errors,
                    self._read_persona_slice,
                )
            )
        elif policy.allow_persona_slice:
            skipped_sources.append("persona_slice")
            if policy.max_lexicon_terms > 0:
                skipped_sources.append("lexicon_terms")

        if tasks:
            done, pending = await asyncio.wait(
                set(tasks.values()),
                timeout=policy.max_build_ms / 1000,
            )
        else:
            done, pending = set(), set()
        timed_out = bool(pending)
        for task in pending:
            task.cancel()
        for source, task in tasks.items():
            if task in pending:
                skipped_sources.append(source)
        results = {
            source: task.result()
            for source, task in tasks.items()
            if task in done and not task.cancelled() and task.exception() is None
        }

        recent_turns = assemble_recent_turns(
            same_session_turns=results.get("same_session_turns", []),
            recent_turns=results.get("recent_turns", []),
            current_user_text=text,
            limit=policy.max_recent_turns,
        )
        session_summaries = results.get("session_summaries", [])
        memory_hits = filter_current_memory_hits(
            results.get("memory_hits", []),
            current_user_text=text,
        )
        lexicon_terms = results.get("lexicon_terms", [])
        persona_slice = results.get("persona_slice")
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        source_counts = {
            "recent_turns": len(recent_turns),
            "session_summaries": len(session_summaries),
            "memory_hits": len(memory_hits),
            "lexicon_terms": len(lexicon_terms),
            "persona_slice": 1 if persona_slice is not None else 0,
        }
        trace = ContextBuildTrace(
            budget_ms=policy.max_build_ms,
            elapsed_ms=elapsed_ms,
            timed_out=timed_out,
            depth=policy.depth,
            included_counts=source_counts,
            skipped_sources=sorted(set(skipped_sources)),
            stage_timings_ms=stage_timings_ms,
            cache_hits={},
            source_errors=source_errors,
        )
        logger.info(
            "ContextSnapshotBuilder depth=%s elapsed_ms=%.1f budget_ms=%s "
            "timed_out=%s recent_turns=%s session_summaries=%s memory_hits=%s "
            "lexicon_terms=%s",
            policy.depth,
            elapsed_ms,
            policy.max_build_ms,
            timed_out,
            source_counts["recent_turns"],
            source_counts["session_summaries"],
            source_counts["memory_hits"],
            source_counts["lexicon_terms"],
        )
        return TomokoContextSnapshot(
            depth=policy.depth,
            recent_turns=recent_turns,
            session_summaries=session_summaries,
            memory_hits=memory_hits,
            lexicon_terms=lexicon_terms,
            persona_slice=persona_slice,
            token_budget_hint=policy.max_prompt_tokens,
            build_elapsed_ms=elapsed_ms,
            source_counts=source_counts,
            trace=trace,
        )

    async def _timed(
        self,
        source: str,
        stage_timings_ms: dict[str, float],
        source_errors: dict[str, str],
        awaitable_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        started_at = time.perf_counter()
        try:
            awaitable = awaitable_factory()
            return await awaitable
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            source_errors[source] = str(exc)
            return [] if source != "persona_slice" else None
        finally:
            stage_timings_ms[source] = (time.perf_counter() - started_at) * 1000

    async def _search_session_summaries(
        self,
        text: str,
        limit: int,
    ) -> list[SessionSummaryHit]:
        assert self.embedding_backend is not None
        assert self.session_summary_store is not None
        embedding = await self.embedding_backend.embed_query(text)
        return await self.session_summary_store.search_similar_summaries(
            embedding=embedding,
            limit=limit,
        )

    async def _search_memory_hits(self, text: str, limit: int) -> list[MemoryHit]:
        assert self.embedding_backend is not None
        assert self.memory_store is not None
        embedding = await self.embedding_backend.embed_query(text)
        return await self.memory_store.search_similar(embedding=embedding, limit=limit)

    async def _read_lexicon_terms(self, text: str, limit: int) -> list[LexiconTerm]:
        assert self.persona_store is not None
        if limit <= 0:
            return []
        lexicon = await self.persona_store.read_latest_lexicon()
        if lexicon is None:
            return []
        return lexicon.select_terms_for_prompt(query=text, limit=limit)

    async def _read_persona_slice(self) -> PersonaPromptSlice | None:
        assert self.persona_store is not None
        state = await self.persona_store.read_latest_state()
        if state is None:
            return None
        return state.to_prompt_slice()
