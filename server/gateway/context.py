from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    ContextCacheTrace,
    LexiconTerm,
    MemoryHit,
    PersonaPromptSlice,
    SessionSummaryHit,
    TomokoContextSnapshot,
)
from server.shared.persona import PersonaSnapshotStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CacheEntry:
    value: Any
    cached_at: float
    ttl_ms: int


class ContextSnapshotBuilder:
    DEFAULT_CACHE_TTL_MS = {
        "same_session_turns": 1000,
        "recent_turns": 3000,
        "session_summaries": 10000,
        "memory_hits": 5000,
        "query_embedding": 5000,
        "lexicon_terms": 30000,
        "persona_slice": 10000,
    }

    def __init__(
        self,
        *,
        conversation_log_reader: ConversationLogWriter | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        memory_store: ConversationMemoryStore | None = None,
        session_summary_store: ConversationSessionSummaryStore | None = None,
        persona_store: PersonaSnapshotStore | None = None,
        cache_ttl_ms: dict[str, int] | None = None,
    ) -> None:
        self.conversation_log_reader = conversation_log_reader
        self.embedding_backend = embedding_backend
        self.memory_store = memory_store
        self.session_summary_store = session_summary_store
        self.persona_store = persona_store
        self._cache_ttl_ms = {
            **self.DEFAULT_CACHE_TTL_MS,
            **(cache_ttl_ms or {}),
        }
        self._cache: dict[tuple[Any, ...], _CacheEntry] = {}

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
        skipped_reasons: dict[str, str] = {}
        cache_entries: dict[str, ContextCacheTrace] = {}
        source_semaphore = asyncio.Semaphore(max(1, policy.max_parallel_sources))

        tasks: dict[str, asyncio.Task[Any]] = {}
        query_embedding_task: asyncio.Task[list[float]] | None = None

        def get_query_embedding_task() -> asyncio.Task[list[float]]:
            nonlocal query_embedding_task
            if query_embedding_task is None:
                assert self.embedding_backend is not None
                query_embedding_task = asyncio.create_task(
                    self._timed_unbounded(
                        "query_embedding",
                        stage_timings_ms,
                        source_errors,
                        lambda: self._cached_source(
                            source="query_embedding",
                            key=("query_embedding", text),
                            cache_entries=cache_entries,
                            loader=lambda: self.embedding_backend.embed_query(text),
                        ),
                    )
                )
            return query_embedding_task

        session_summary_task: asyncio.Task[Any] | None = None
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
                        source_semaphore,
                        lambda: self._cached_source(
                            source="same_session_turns",
                            key=(
                                "same_session_turns",
                                active_session_id,
                                policy.max_same_session_turns + 1,
                            ),
                            cache_entries=cache_entries,
                            loader=lambda: read_session_turns(
                                conversation_session_id=active_session_id,
                                limit=policy.max_same_session_turns + 1,
                            ),
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
                        source_semaphore,
                        lambda: self._cached_source(
                            source="recent_turns",
                            key=("recent_turns", policy.max_recent_turns + 1),
                            cache_entries=cache_entries,
                            loader=lambda: read_recent_turns(
                                limit=policy.max_recent_turns + 1
                            ),
                        ),
                    ),
                )

        if (
            policy.max_session_summaries > 0
            and self.embedding_backend is not None
            and self.session_summary_store is not None
        ):
            session_summary_task = asyncio.create_task(
                self._timed(
                    "session_summaries",
                    stage_timings_ms,
                    source_errors,
                    source_semaphore,
                    lambda: self._cached_source(
                        source="session_summaries",
                        key=("session_summaries", text, policy.max_session_summaries),
                        cache_entries=cache_entries,
                        loader=lambda: self._search_session_summaries(
                            get_query_embedding_task(), policy.max_session_summaries
                        ),
                    ),
                )
            )
            tasks["session_summaries"] = session_summary_task
        elif policy.max_session_summaries > 0:
            skipped_sources.append("session_summaries")
            skipped_reasons["session_summaries"] = "missing_embedding_or_store"

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
                    source_semaphore,
                    lambda: self._cached_source(
                        source="memory_hits",
                        key=("memory_hits", text, policy.max_memory_hits),
                        cache_entries=cache_entries,
                        loader=lambda: self._search_memory_hits(
                            get_query_embedding_task(),
                            policy.max_memory_hits,
                            wait_for=session_summary_task
                            if policy.prioritize_session_summaries
                            else None,
                        ),
                    ),
                )
            )
        elif policy.allow_turn_memory_search and policy.max_memory_hits > 0:
            skipped_sources.append("memory_hits")
            skipped_reasons["memory_hits"] = "missing_embedding_or_store"

        if policy.allow_persona_slice and self.persona_store is not None:
            tasks["lexicon_terms"] = asyncio.create_task(
                self._timed(
                    "lexicon_terms",
                    stage_timings_ms,
                    source_errors,
                    source_semaphore,
                    lambda: self._cached_source(
                        source="lexicon_terms",
                        key=("lexicon_terms", text, policy.max_lexicon_terms),
                        cache_entries=cache_entries,
                        loader=lambda: self._read_lexicon_terms(
                            text, policy.max_lexicon_terms
                        ),
                    ),
                )
            )
            tasks["persona_slice"] = asyncio.create_task(
                self._timed(
                    "persona_slice",
                    stage_timings_ms,
                    source_errors,
                    source_semaphore,
                    lambda: self._cached_source(
                        source="persona_slice",
                        key=("persona_slice",),
                        cache_entries=cache_entries,
                        loader=self._read_persona_slice,
                    ),
                )
            )
        elif policy.allow_persona_slice:
            skipped_sources.append("persona_slice")
            skipped_reasons["persona_slice"] = "missing_persona_store"
            if policy.max_lexicon_terms > 0:
                skipped_sources.append("lexicon_terms")
                skipped_reasons["lexicon_terms"] = "missing_persona_store"

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
                skipped_reasons[source] = "timed_out"
        if query_embedding_task is not None and not query_embedding_task.done():
            query_embedding_task.cancel()
            skipped_sources.append("query_embedding")
            skipped_reasons["query_embedding"] = "timed_out"
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
            cache_hits={
                source: entry.hit for source, entry in cache_entries.items()
            },
            source_errors=source_errors,
            skipped_reasons=skipped_reasons,
            cache_entries=cache_entries,
        )
        logger.info(
            "ContextSnapshotBuilder depth=%s elapsed_ms=%.1f budget_ms=%s "
            "timed_out=%s recent_turns=%s session_summaries=%s memory_hits=%s "
            "lexicon_terms=%s max_parallel_sources=%s cache_hits=%s "
            "stage_timings_ms=%s skipped_reasons=%s source_errors=%s",
            policy.depth,
            elapsed_ms,
            policy.max_build_ms,
            timed_out,
            source_counts["recent_turns"],
            source_counts["session_summaries"],
            source_counts["memory_hits"],
            source_counts["lexicon_terms"],
            policy.max_parallel_sources,
            trace.cache_hits,
            trace.stage_timings_ms,
            trace.skipped_reasons,
            trace.source_errors,
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

    async def _cached_source(
        self,
        *,
        source: str,
        key: tuple[Any, ...],
        cache_entries: dict[str, ContextCacheTrace],
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        ttl_ms = self._cache_ttl_ms[source]
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None:
            age_ms = (now - entry.cached_at) * 1000
            if age_ms <= entry.ttl_ms:
                cache_entries[source] = ContextCacheTrace(
                    hit=True,
                    age_ms=age_ms,
                    ttl_ms=entry.ttl_ms,
                )
                return entry.value

        cache_entries[source] = ContextCacheTrace(
            hit=False,
            age_ms=None,
            ttl_ms=ttl_ms,
        )
        value = await loader()
        self._cache[key] = _CacheEntry(
            value=value,
            cached_at=time.monotonic(),
            ttl_ms=ttl_ms,
        )
        return value

    async def _timed(
        self,
        source: str,
        stage_timings_ms: dict[str, float],
        source_errors: dict[str, str],
        source_semaphore: asyncio.Semaphore,
        awaitable_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        started_at = time.perf_counter()
        try:
            async with source_semaphore:
                awaitable = awaitable_factory()
                return await awaitable
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            source_errors[source] = str(exc)
            return [] if source != "persona_slice" else None
        finally:
            stage_timings_ms[source] = (time.perf_counter() - started_at) * 1000

    async def _timed_unbounded(
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
            return []
        finally:
            stage_timings_ms[source] = (time.perf_counter() - started_at) * 1000

    async def _search_session_summaries(
        self,
        embedding_task: asyncio.Task[list[float]],
        limit: int,
    ) -> list[SessionSummaryHit]:
        assert self.session_summary_store is not None
        embedding = await embedding_task
        return await self.session_summary_store.search_similar_summaries(
            embedding=embedding,
            limit=limit,
        )

    async def _search_memory_hits(
        self,
        embedding_task: asyncio.Task[list[float]],
        limit: int,
        *,
        wait_for: asyncio.Task[Any] | None = None,
    ) -> list[MemoryHit]:
        assert self.memory_store is not None
        if wait_for is not None:
            await asyncio.shield(wait_for)
        embedding = await embedding_task
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
