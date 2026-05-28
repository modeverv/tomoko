from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    ContextSourceScoreTrace,
    ConversationTurn,
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


@dataclass(frozen=True)
class _SourceRankingRule:
    max_items: int
    source_weight: float
    role_weight: float = 1.0
    base_score: float = 0.5


@dataclass(frozen=True)
class _ScoredItem:
    source: str
    item: Any
    trace: ContextSourceScoreTrace


class ContextSnapshotBuilder:
    SOURCE_RANKING_RULES = {
        "session_summary": _SourceRankingRule(max_items=2, source_weight=1.1),
        "user_turn_snippet": _SourceRankingRule(max_items=4, source_weight=1.0),
        "tomoko_turn_snippet": _SourceRankingRule(
            max_items=1,
            source_weight=0.7,
            role_weight=0.25,
        ),
        "memory_hit_user": _SourceRankingRule(max_items=4, source_weight=1.0),
        "memory_hit_tomoko": _SourceRankingRule(
            max_items=1,
            source_weight=0.7,
            role_weight=0.25,
        ),
        "lexicon_term": _SourceRankingRule(max_items=4, source_weight=0.6),
    }
    CUE_SOURCE_WEIGHT_MULTIPLIERS = {
        "recall": {
            "session_summary": 1.25,
            "user_turn_snippet": 1.0,
            "tomoko_turn_snippet": 0.8,
            "memory_hit_user": 1.0,
            "memory_hit_tomoko": 0.8,
            "lexicon_term": 1.0,
        },
        "detail": {
            "session_summary": 0.95,
            "user_turn_snippet": 1.35,
            "tomoko_turn_snippet": 0.9,
            "memory_hit_user": 1.2,
            "memory_hit_tomoko": 0.85,
            "lexicon_term": 1.0,
        },
        "stance": {
            "session_summary": 1.0,
            "user_turn_snippet": 1.25,
            "tomoko_turn_snippet": 0.75,
            "memory_hit_user": 1.15,
            "memory_hit_tomoko": 0.75,
            "lexicon_term": 1.3,
        },
        "normal": {
            "session_summary": 0.85,
            "user_turn_snippet": 0.8,
            "tomoko_turn_snippet": 0.6,
            "memory_hit_user": 0.8,
            "memory_hit_tomoko": 0.6,
            "lexicon_term": 0.8,
        },
    }
    DEFAULT_CACHE_TTL_MS = {
        "same_session_turns": 1000,
        "recent_turns": 3000,
        "session_summaries": 10000,
        "memory_hits": 5000,
        "restored_turn_snippets": 5000,
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
        cue_type = _classify_memory_cue(text)

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
        restored_turns: list[MemoryHit] = []
        if (
            results.get("session_summaries")
            and self.session_summary_store is not None
            and policy.depth in {"deep", "reflective"}
        ):
            remaining_ms = policy.max_build_ms - (
                (time.perf_counter() - started_at) * 1000
            )
            if remaining_ms > 1:
                restored_turns = await self._restore_turn_snippets_with_deadline(
                    session_summaries=results["session_summaries"],
                    cue_type=cue_type,
                    max_summary_sessions=2 if cue_type in {"detail", "stance"} else 1,
                    stage_timings_ms=stage_timings_ms,
                    source_errors=source_errors,
                    cache_entries=cache_entries,
                    skipped_sources=skipped_sources,
                    skipped_reasons=skipped_reasons,
                    timeout_ms=remaining_ms,
                )
            else:
                skipped_sources.append("restored_turn_snippets")
                skipped_reasons["restored_turn_snippets"] = "deadline_exhausted"

        recent_turns = assemble_recent_turns(
            same_session_turns=results.get("same_session_turns", []),
            recent_turns=results.get("recent_turns", []),
            current_user_text=text,
            limit=policy.max_recent_turns,
        )
        source_score_traces: list[ContextSourceScoreTrace] = []
        session_summaries = self._rank_session_summaries(
            results.get("session_summaries", []),
            cue_type=cue_type,
            traces=source_score_traces,
        )
        raw_memory_hits = filter_current_memory_hits(
            results.get("memory_hits", []),
            current_user_text=text,
        )
        memory_hits = self._rank_memory_hits(
            [*raw_memory_hits, *restored_turns],
            cue_type=cue_type,
            traces=source_score_traces,
        )
        lexicon_terms = self._rank_lexicon_terms(
            results.get("lexicon_terms", []),
            cue_type=cue_type,
            traces=source_score_traces,
        )
        persona_slice = results.get("persona_slice")
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        source_counts = {
            "recent_turns": len(recent_turns),
            "session_summaries": len(session_summaries),
            "memory_hits": len(memory_hits),
            "restored_turn_snippets": len(restored_turns),
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
            cue_type=cue_type,
            source_score_traces=source_score_traces,
        )
        logger.info(
            "ContextSnapshotBuilder depth=%s elapsed_ms=%.1f budget_ms=%s "
            "timed_out=%s recent_turns=%s session_summaries=%s memory_hits=%s "
            "restored_turn_snippets=%s lexicon_terms=%s cue_type=%s "
            "max_parallel_sources=%s cache_hits=%s stage_timings_ms=%s "
            "skipped_reasons=%s source_errors=%s source_scores=%s",
            policy.depth,
            elapsed_ms,
            policy.max_build_ms,
            timed_out,
            source_counts["recent_turns"],
            source_counts["session_summaries"],
            source_counts["memory_hits"],
            source_counts["restored_turn_snippets"],
            source_counts["lexicon_terms"],
            cue_type,
            policy.max_parallel_sources,
            trace.cache_hits,
            trace.stage_timings_ms,
            trace.skipped_reasons,
            trace.source_errors,
            [
                {
                    "source": item.source,
                    "speaker": item.speaker,
                    "selected": item.selected,
                    "final_score": round(item.final_score, 4),
                    "dropped_reason": item.dropped_reason,
                    "quota_hit": item.quota_hit,
                }
                for item in source_score_traces
            ],
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

    async def _restore_turn_snippets_with_deadline(
        self,
        *,
        session_summaries: list[SessionSummaryHit],
        cue_type: str,
        max_summary_sessions: int,
        stage_timings_ms: dict[str, float],
        source_errors: dict[str, str],
        cache_entries: dict[str, ContextCacheTrace],
        skipped_sources: list[str],
        skipped_reasons: dict[str, str],
        timeout_ms: float,
    ) -> list[MemoryHit]:
        try:
            return await asyncio.wait_for(
                self._timed_unbounded(
                    "restored_turn_snippets",
                    stage_timings_ms,
                    source_errors,
                    lambda: self._restore_turn_snippets(
                        session_summaries=session_summaries,
                        cue_type=cue_type,
                        max_summary_sessions=max_summary_sessions,
                        cache_entries=cache_entries,
                    ),
                ),
                timeout=max(0.001, timeout_ms / 1000),
            )
        except TimeoutError:
            skipped_sources.append("restored_turn_snippets")
            skipped_reasons["restored_turn_snippets"] = "timed_out"
            return []

    async def _restore_turn_snippets(
        self,
        *,
        session_summaries: list[SessionSummaryHit],
        cue_type: str,
        max_summary_sessions: int,
        cache_entries: dict[str, ContextCacheTrace],
    ) -> list[MemoryHit]:
        assert self.session_summary_store is not None
        read_session_turns = getattr(self.session_summary_store, "read_session_turns", None)
        if read_session_turns is None:
            return []
        snippets: list[MemoryHit] = []
        top_summaries = sorted(
            session_summaries,
            key=lambda item: item.similarity,
            reverse=True,
        )[:max_summary_sessions]
        for summary in top_summaries:
            turns = await self._cached_source(
                source="restored_turn_snippets",
                key=("restored_turn_snippets", summary.session_id),
                cache_entries=cache_entries,
                loader=lambda summary_id=summary.session_id: read_session_turns(
                    session_id=summary_id
                ),
            )
            snippets.extend(
                self._turns_to_memory_hits(
                    turns,
                    session_id=summary.session_id,
                    summary_similarity=summary.similarity,
                    cue_type=cue_type,
                )
            )
        return snippets

    def _turns_to_memory_hits(
        self,
        turns: list[ConversationTurn],
        *,
        session_id: UUID,
        summary_similarity: float,
        cue_type: str,
    ) -> list[MemoryHit]:
        hits: list[MemoryHit] = []
        for index, turn in enumerate(turns):
            if not turn.text.strip():
                continue
            if turn.speaker == "tomoko" and not _looks_like_tomoko_summary_turn(
                turn.text, cue_type=cue_type
            ):
                continue
            hits.append(
                MemoryHit(
                    speaker=turn.speaker,
                    text=turn.text,
                    timestamp=turn.timestamp,
                    emotion=turn.emotion,
                    similarity=summary_similarity,
                    source_id=f"restored_turn:{session_id}:{index}",
                )
            )
        return hits

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

    def _rank_session_summaries(
        self,
        summaries: list[SessionSummaryHit],
        *,
        cue_type: str,
        traces: list[ContextSourceScoreTrace],
    ) -> list[SessionSummaryHit]:
        scored = [
            self._score_item(
                source="session_summary",
                item=summary,
                cue_type=cue_type,
                raw_similarity=summary.similarity,
                source_id=str(summary.session_id),
                speaker=None,
                timestamp=summary.ended_at or summary.started_at,
            )
            for summary in summaries
        ]
        selected = self._select_by_quota(scored, traces=traces)
        return [item.item for item in selected]

    def _rank_memory_hits(
        self,
        hits: list[MemoryHit],
        *,
        cue_type: str,
        traces: list[ContextSourceScoreTrace],
    ) -> list[MemoryHit]:
        scored: list[_ScoredItem] = []
        for hit in hits:
            source = _memory_source_name(hit)
            scored.append(
                self._score_item(
                    source=source,
                    item=hit,
                    cue_type=cue_type,
                    raw_similarity=hit.similarity,
                    source_id=hit.source_id,
                    speaker=hit.speaker,
                    timestamp=hit.timestamp,
                )
            )
        selected = self._select_by_quota(scored, traces=traces)
        selected.sort(key=lambda item: item.trace.final_score, reverse=True)
        return [item.item for item in selected]

    def _rank_lexicon_terms(
        self,
        terms: list[LexiconTerm],
        *,
        cue_type: str,
        traces: list[ContextSourceScoreTrace],
    ) -> list[LexiconTerm]:
        scored = [
            self._score_item(
                source="lexicon_term",
                item=term,
                cue_type=cue_type,
                raw_similarity=None,
                source_id=term.term,
                speaker=None,
                timestamp=None,
                salience_weight=max(0.1, term.salience),
            )
            for term in terms
        ]
        selected = self._select_by_quota(scored, traces=traces)
        return [item.item for item in selected]

    def _select_by_quota(
        self,
        scored: list[_ScoredItem],
        *,
        traces: list[ContextSourceScoreTrace],
    ) -> list[_ScoredItem]:
        selected: list[_ScoredItem] = []
        selected_counts: dict[str, int] = {}
        for item in sorted(
            scored,
            key=lambda scored_item: scored_item.trace.final_score,
            reverse=True,
        ):
            rule = self.SOURCE_RANKING_RULES[item.source]
            count = selected_counts.get(item.source, 0)
            if count >= rule.max_items:
                traces.append(
                    _replace_score_trace(
                        item.trace,
                        selected=False,
                        dropped_reason="quota_hit",
                        quota_hit=True,
                    )
                )
                continue
            selected_counts[item.source] = count + 1
            selected.append(item)
            traces.append(item.trace)
        return selected

    def _score_item(
        self,
        *,
        source: str,
        item: Any,
        cue_type: str,
        raw_similarity: float | None,
        source_id: str | None,
        speaker: str | None,
        timestamp: datetime | None,
        salience_weight: float = 1.0,
    ) -> _ScoredItem:
        rule = self.SOURCE_RANKING_RULES[source]
        cue_multiplier = self.CUE_SOURCE_WEIGHT_MULTIPLIERS[cue_type][source]
        source_weight = rule.source_weight * cue_multiplier
        recency_weight = _recency_weight(timestamp)
        score_base = raw_similarity if raw_similarity is not None else rule.base_score
        final_score = (
            score_base
            * source_weight
            * rule.role_weight
            * recency_weight
            * salience_weight
        )
        return _ScoredItem(
            source=source,
            item=item,
            trace=ContextSourceScoreTrace(
                source=source,
                source_id=source_id,
                speaker=speaker,
                selected=True,
                dropped_reason=None,
                raw_similarity=raw_similarity,
                base_score=None if raw_similarity is not None else rule.base_score,
                source_weight=source_weight,
                role_weight=rule.role_weight,
                recency_weight=recency_weight,
                salience_weight=salience_weight,
                final_score=final_score,
                quota_hit=False,
            ),
        )


def _replace_score_trace(
    trace: ContextSourceScoreTrace,
    *,
    selected: bool,
    dropped_reason: str | None,
    quota_hit: bool,
) -> ContextSourceScoreTrace:
    return ContextSourceScoreTrace(
        source=trace.source,
        source_id=trace.source_id,
        speaker=trace.speaker,
        selected=selected,
        dropped_reason=dropped_reason,
        raw_similarity=trace.raw_similarity,
        base_score=trace.base_score,
        source_weight=trace.source_weight,
        role_weight=trace.role_weight,
        recency_weight=trace.recency_weight,
        salience_weight=trace.salience_weight,
        final_score=trace.final_score,
        quota_hit=quota_hit,
    )


def _classify_memory_cue(text: str) -> str:
    normalized = text.strip()
    if any(word in normalized for word in ("詳しく", "どんな話", "何の話")):
        return "detail"
    if any(
        word in normalized
        for word in ("どう考えて", "どういう風", "どう捉えて", "結論", "スタンス")
    ):
        return "stance"
    if any(
        word in normalized
        for word in ("覚えて", "この前", "前に話", "話してた", "話した")
    ):
        return "recall"
    return "normal"


def _memory_source_name(hit: MemoryHit) -> str:
    if hit.source_id and hit.source_id.startswith("restored_turn:"):
        return "user_turn_snippet" if hit.speaker == "user" else "tomoko_turn_snippet"
    return "memory_hit_user" if hit.speaker == "user" else "memory_hit_tomoko"


def _recency_weight(timestamp: datetime | None) -> float:
    if timestamp is None:
        return 1.0
    now = datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    age_days = max(0.0, (now - timestamp).total_seconds() / 86400)
    if age_days <= 1:
        return 1.1
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.9
    return 0.8


def _looks_like_tomoko_summary_turn(text: str, *, cue_type: str) -> bool:
    if cue_type == "detail":
        return False
    return any(
        word in text
        for word in (
            "つまり",
            "結論",
            "まとめると",
            "要するに",
            "思う",
            "考え",
            "覚えて",
        )
    )
