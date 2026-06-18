from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

CandidateMaturity = Literal[0, 1, 2]
ArrivalBehavior = Literal["speak_first", "wait_silent", "subtle_react"]

_VALID_MATURITIES = {0, 1, 2}
_VALID_ARRIVAL_BEHAVIORS = {"speak_first", "wait_silent", "subtle_react"}
_DEDUPE_TAG_PREFIX = "dedupe:"


@dataclass(frozen=True)
class ArrivalContextSnapshot:
    schema_version: int = 1
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    device_id: str | None = None
    local_time: str = ""
    time_since_last_session_sec: int | None = None
    session_count_today: int = 0
    urgent_candidate_count: int = 0
    top_urgent_seeds: tuple[str, ...] = field(default_factory=tuple)
    persona_hint: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ArrivalContextSnapshot:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported arrival context schema_version: {schema_version}"
            )

        computed_at = _parse_datetime_value(
            payload.get("computed_at") or payload.get("observed_at")
        )

        return cls(
            schema_version=schema_version,
            computed_at=computed_at,
            device_id=_optional_str(payload.get("device_id")),
            local_time=_optional_str(payload.get("local_time"))
            or _format_local_time(computed_at),
            time_since_last_session_sec=_optional_int(
                payload.get("time_since_last_session_sec")
            ),
            session_count_today=_int_or_zero(payload.get("session_count_today")),
            urgent_candidate_count=_int_or_zero(
                payload.get("urgent_candidate_count")
            ),
            top_urgent_seeds=tuple(
                str(item) for item in _as_sequence(payload.get("top_urgent_seeds"))
            ),
            persona_hint=_optional_str(payload.get("persona_hint")),
            notes=tuple(str(item) for item in _as_sequence(payload.get("notes"))),
        )

    @property
    def observed_at(self) -> datetime:
        return self.computed_at

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "computed_at": self.computed_at.isoformat(),
            "local_time": self.local_time or _format_local_time(self.computed_at),
            "session_count_today": self.session_count_today,
            "urgent_candidate_count": self.urgent_candidate_count,
            "top_urgent_seeds": list(self.top_urgent_seeds),
            "notes": list(self.notes),
        }
        if self.device_id is not None:
            payload["device_id"] = self.device_id
        if self.time_since_last_session_sec is not None:
            payload["time_since_last_session_sec"] = self.time_since_last_session_sec
        if self.persona_hint is not None:
            payload["persona_hint"] = self.persona_hint
        return payload


@dataclass(frozen=True)
class ThinkerSourceContext:
    observed_at: datetime
    device_id: str | None = None
    attention_mode: str | None = None
    recent_summary: str | None = None


@dataclass(frozen=True)
class ThinkerEvaluationContext:
    observed_at: datetime
    device_id: str | None = None
    attention_mode: str | None = None
    recent_summary: str | None = None
    session_summaries: tuple[str, ...] = field(default_factory=tuple)
    lexicon_terms: tuple[str, ...] = field(default_factory=tuple)
    persona_notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CandidateSeed:
    seed_text: str
    source: str
    priority: float
    expires_at: datetime
    dedupe_key: str
    urgent: bool = False
    context_tags: tuple[str, ...] = field(default_factory=tuple)
    metadata_json: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.seed_text:
            raise ValueError("CandidateSeed.seed_text must not be empty")
        if not self.source:
            raise ValueError("CandidateSeed.source must not be empty")
        if not self.dedupe_key:
            raise ValueError("CandidateSeed.dedupe_key must not be empty")

        dedupe_tag = dedupe_key_to_tag(self.dedupe_key)
        if dedupe_tag not in self.context_tags:
            object.__setattr__(
                self,
                "context_tags",
                (dedupe_tag, *self.context_tags),
            )


@dataclass(frozen=True)
class EvaluatedUtterance:
    should_keep: bool
    generated_text: str | None
    priority: float
    urgent: bool
    reason: str
    context_tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.should_keep and not self.generated_text:
            raise ValueError("generated_text is required when should_keep is true")
        if self.priority < 0.0 or self.priority > 1.0:
            raise ValueError("priority must be between 0.0 and 1.0")


@dataclass(frozen=True)
class UtteranceCandidate:
    id: UUID
    seed: str
    generated_text: str | None
    generated_audio: bytes | None
    priority: float
    urgent: bool
    created_at: datetime
    expires_at: datetime
    spoken_at: datetime | None
    dismissed_at: datetime | None
    maturity: CandidateMaturity
    source: str
    context_tags: tuple[str, ...] = field(default_factory=tuple)
    metadata_json: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.maturity not in _VALID_MATURITIES:
            raise ValueError(f"Unsupported candidate maturity: {self.maturity}")
        if self.maturity == 2 and (
            self.generated_text is None or self.generated_audio is None
        ):
            raise ValueError(
                "maturity=2 requires generated_text and generated_audio"
            )

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> UtteranceCandidate:
        (
            candidate_id,
            seed,
            generated_text,
            generated_audio,
            priority,
            urgent,
            created_at,
            expires_at,
            spoken_at,
            dismissed_at,
            maturity,
            source,
            context_tags,
            metadata_json,
        ) = row
        return cls(
            id=_as_uuid(candidate_id),
            seed=str(seed),
            generated_text=_optional_str(generated_text),
            generated_audio=bytes(generated_audio) if generated_audio is not None else None,
            priority=float(priority),
            urgent=bool(urgent),
            created_at=_as_datetime(created_at),
            expires_at=_as_datetime(expires_at),
            spoken_at=_optional_datetime(spoken_at),
            dismissed_at=_optional_datetime(dismissed_at),
            maturity=_as_maturity(maturity),
            source=str(source),
            context_tags=tuple(str(tag) for tag in context_tags or ()),
            metadata_json=dict(metadata_json or {}),
        )


@dataclass(frozen=True)
class ArrivalCandidate:
    id: UUID
    computed_at: datetime
    valid_until: datetime
    context_snapshot: ArrivalContextSnapshot
    behavior: ArrivalBehavior
    utterance_text: str | None
    utterance_audio: bytes | None
    used_at: datetime | None

    def __post_init__(self) -> None:
        if self.behavior not in _VALID_ARRIVAL_BEHAVIORS:
            raise ValueError(f"Unsupported arrival behavior: {self.behavior}")

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> ArrivalCandidate:
        (
            candidate_id,
            computed_at,
            valid_until,
            context_snapshot,
            behavior,
            utterance_text,
            utterance_audio,
            used_at,
        ) = row
        return cls(
            id=_as_uuid(candidate_id),
            computed_at=_as_datetime(computed_at),
            valid_until=_as_datetime(valid_until),
            context_snapshot=ArrivalContextSnapshot.from_json(
                dict(context_snapshot or {})
            ),
            behavior=_as_arrival_behavior(behavior),
            utterance_text=_optional_str(utterance_text),
            utterance_audio=bytes(utterance_audio) if utterance_audio is not None else None,
            used_at=_optional_datetime(used_at),
        )


@dataclass(frozen=True)
class PregeneratedAudioChunk:
    id: UUID
    utterance_candidate_id: UUID
    chunk_index: int
    audio_data: bytes
    audio_format: str
    is_last: bool
    created_at: datetime

    def __post_init__(self) -> None:
        if self.chunk_index < 0:
            raise ValueError("chunk_index must be non-negative")
        if not self.audio_data:
            raise ValueError("audio_data must not be empty")
        if not self.audio_format:
            raise ValueError("audio_format must not be empty")

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> PregeneratedAudioChunk:
        (
            chunk_id,
            utterance_candidate_id,
            chunk_index,
            audio_data,
            audio_format,
            is_last,
            created_at,
        ) = row
        return cls(
            id=_as_uuid(chunk_id),
            utterance_candidate_id=_as_uuid(utterance_candidate_id),
            chunk_index=int(chunk_index),
            audio_data=bytes(audio_data),
            audio_format=str(audio_format),
            is_last=bool(is_last),
            created_at=_as_datetime(created_at),
        )


class CandidateStore(Protocol):
    async def insert_utterance_candidate(
        self,
        *,
        seed: str,
        source: str,
        expires_at: datetime,
        priority: float = 0.5,
        urgent: bool = False,
        maturity: CandidateMaturity = 0,
        generated_text: str | None = None,
        generated_audio: bytes | None = None,
        context_tags: tuple[str, ...] = (),
        metadata_json: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate: ...

    async def insert_seed_candidate_once(
        self,
        seed: CandidateSeed,
        *,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate | None: ...

    async def insert_evaluated_utterance_once(
        self,
        seed: CandidateSeed,
        evaluated: EvaluatedUtterance | None,
        *,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate | None: ...

    async def fetch_active_utterance_candidates(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UtteranceCandidate]: ...

    async def mark_utterance_spoken(
        self,
        candidate_id: UUID,
        *,
        spoken_at: datetime,
    ) -> None: ...

    async def mark_utterance_pregenerated(
        self,
        candidate_id: UUID,
        *,
        generated_audio: bytes,
    ) -> None: ...

    async def dismiss_utterance_candidate(
        self,
        candidate_id: UUID,
        *,
        dismissed_at: datetime,
    ) -> None: ...

    async def mark_expired_utterance_candidates(self, now: datetime) -> int: ...

    async def insert_arrival_candidate(
        self,
        *,
        context_snapshot: ArrivalContextSnapshot,
        behavior: ArrivalBehavior,
        valid_until: datetime,
        computed_at: datetime | None = None,
        utterance_text: str | None = None,
        utterance_audio: bytes | None = None,
    ) -> ArrivalCandidate: ...

    async def fetch_latest_fresh_arrival_candidate(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalCandidate | None: ...

    async def mark_arrival_used(
        self,
        candidate_id: UUID,
        *,
        used_at: datetime,
    ) -> None: ...

    async def delete_expired_arrival_candidates(
        self,
        *,
        older_than: datetime,
    ) -> int: ...


class PregeneratedAudioChunkStore(Protocol):
    async def replace_chunks(
        self,
        candidate_id: UUID,
        chunks: tuple[bytes, ...],
        *,
        audio_format: str = "riff_wave",
        created_at: datetime | None = None,
    ) -> tuple[PregeneratedAudioChunk, ...]: ...

    async def fetch_chunks(
        self,
        candidate_id: UUID,
    ) -> tuple[PregeneratedAudioChunk, ...]: ...


class InMemoryCandidateStore:
    def __init__(self) -> None:
        self.utterance_candidates: list[UtteranceCandidate] = []
        self.arrival_candidates: list[ArrivalCandidate] = []

    async def insert_utterance_candidate(
        self,
        *,
        seed: str,
        source: str,
        expires_at: datetime,
        priority: float = 0.5,
        urgent: bool = False,
        maturity: CandidateMaturity = 0,
        generated_text: str | None = None,
        generated_audio: bytes | None = None,
        context_tags: tuple[str, ...] = (),
        metadata_json: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate:
        candidate = UtteranceCandidate(
            id=uuid4(),
            seed=seed,
            generated_text=generated_text,
            generated_audio=generated_audio,
            priority=priority,
            urgent=urgent,
            created_at=created_at or datetime.now(UTC),
            expires_at=expires_at,
            spoken_at=None,
            dismissed_at=None,
            maturity=maturity,
            source=source,
            context_tags=tuple(context_tags),
            metadata_json=dict(metadata_json or {}),
        )
        self.utterance_candidates.append(candidate)
        return candidate

    async def insert_seed_candidate_once(
        self,
        seed: CandidateSeed,
        *,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate | None:
        now = created_at or datetime.now(UTC)
        dedupe_tag = dedupe_key_to_tag(seed.dedupe_key)
        if any(
            candidate.spoken_at is None
            and candidate.dismissed_at is None
            and candidate.expires_at > now
            and dedupe_tag in candidate.context_tags
            for candidate in self.utterance_candidates
        ):
            return None

        return await self.insert_utterance_candidate(
            seed=seed.seed_text,
            source=seed.source,
            expires_at=seed.expires_at,
            priority=seed.priority,
            urgent=seed.urgent,
            maturity=0,
            context_tags=seed.context_tags,
            metadata_json=seed.metadata_json,
            created_at=created_at,
        )

    async def insert_evaluated_utterance_once(
        self,
        seed: CandidateSeed,
        evaluated: EvaluatedUtterance | None,
        *,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate | None:
        if evaluated is None or not evaluated.should_keep:
            return None

        now = created_at or datetime.now(UTC)
        dedupe_tag = dedupe_key_to_tag(seed.dedupe_key)
        if any(
            candidate.spoken_at is None
            and candidate.dismissed_at is None
            and candidate.expires_at > now
            and candidate.maturity >= 1
            and dedupe_tag in candidate.context_tags
            for candidate in self.utterance_candidates
        ):
            return None

        return await self.insert_utterance_candidate(
            seed=seed.seed_text,
            source=seed.source,
            expires_at=seed.expires_at,
            priority=evaluated.priority,
            urgent=evaluated.urgent,
            maturity=1,
            generated_text=evaluated.generated_text,
            context_tags=evaluated.context_tags,
            metadata_json=seed.metadata_json,
            created_at=created_at,
        )

    async def fetch_active_utterance_candidates(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UtteranceCandidate]:
        return sorted(
            [
                candidate
                for candidate in self.utterance_candidates
                if candidate.spoken_at is None
                and candidate.dismissed_at is None
                and candidate.expires_at > now
            ],
            key=lambda candidate: (-candidate.priority, candidate.created_at),
        )[:limit]

    async def mark_utterance_spoken(
        self,
        candidate_id: UUID,
        *,
        spoken_at: datetime,
    ) -> None:
        self._replace_utterance(
            candidate_id,
            spoken_at=spoken_at,
        )

    async def mark_utterance_pregenerated(
        self,
        candidate_id: UUID,
        *,
        generated_audio: bytes,
    ) -> None:
        self._replace_utterance(
            candidate_id,
            generated_audio=generated_audio,
            maturity=2,
        )

    async def dismiss_utterance_candidate(
        self,
        candidate_id: UUID,
        *,
        dismissed_at: datetime,
    ) -> None:
        self._replace_utterance(
            candidate_id,
            dismissed_at=dismissed_at,
        )

    async def mark_expired_utterance_candidates(self, now: datetime) -> int:
        count = 0
        for candidate in list(self.utterance_candidates):
            if (
                candidate.spoken_at is None
                and candidate.dismissed_at is None
                and candidate.expires_at <= now
            ):
                self._replace_utterance(candidate.id, dismissed_at=now)
                count += 1
        return count

    async def insert_arrival_candidate(
        self,
        *,
        context_snapshot: ArrivalContextSnapshot,
        behavior: ArrivalBehavior,
        valid_until: datetime,
        computed_at: datetime | None = None,
        utterance_text: str | None = None,
        utterance_audio: bytes | None = None,
    ) -> ArrivalCandidate:
        candidate = ArrivalCandidate(
            id=uuid4(),
            computed_at=computed_at or datetime.now(UTC),
            valid_until=valid_until,
            context_snapshot=context_snapshot,
            behavior=behavior,
            utterance_text=utterance_text,
            utterance_audio=utterance_audio,
            used_at=None,
        )
        self.arrival_candidates.append(candidate)
        return candidate

    async def fetch_latest_fresh_arrival_candidate(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalCandidate | None:
        candidates = [
            candidate
            for candidate in self.arrival_candidates
            if candidate.used_at is None
            and candidate.valid_until > now
            and (
                device_id is None
                or candidate.context_snapshot.device_id is None
                or candidate.context_snapshot.device_id == device_id
            )
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.computed_at)

    async def mark_arrival_used(
        self,
        candidate_id: UUID,
        *,
        used_at: datetime,
    ) -> None:
        for index, candidate in enumerate(self.arrival_candidates):
            if candidate.id == candidate_id:
                self.arrival_candidates[index] = ArrivalCandidate(
                    id=candidate.id,
                    computed_at=candidate.computed_at,
                    valid_until=candidate.valid_until,
                    context_snapshot=candidate.context_snapshot,
                    behavior=candidate.behavior,
                    utterance_text=candidate.utterance_text,
                    utterance_audio=candidate.utterance_audio,
                    used_at=used_at,
                )
                return

    async def delete_expired_arrival_candidates(
        self,
        *,
        older_than: datetime,
    ) -> int:
        before_count = len(self.arrival_candidates)
        self.arrival_candidates = [
            candidate
            for candidate in self.arrival_candidates
            if candidate.valid_until >= older_than
        ]
        return before_count - len(self.arrival_candidates)

    def _replace_utterance(
        self,
        candidate_id: UUID,
        *,
        spoken_at: datetime | None = None,
        dismissed_at: datetime | None = None,
        generated_audio: bytes | None = None,
        maturity: CandidateMaturity | None = None,
    ) -> None:
        for index, candidate in enumerate(self.utterance_candidates):
            if candidate.id == candidate_id:
                self.utterance_candidates[index] = UtteranceCandidate(
                    id=candidate.id,
                    seed=candidate.seed,
                    generated_text=candidate.generated_text,
                    generated_audio=generated_audio or candidate.generated_audio,
                    priority=candidate.priority,
                    urgent=candidate.urgent,
                    created_at=candidate.created_at,
                    expires_at=candidate.expires_at,
                    spoken_at=spoken_at or candidate.spoken_at,
                    dismissed_at=dismissed_at or candidate.dismissed_at,
                    maturity=maturity or candidate.maturity,
                    source=candidate.source,
                    context_tags=candidate.context_tags,
                    metadata_json=candidate.metadata_json,
                )
                return


class InMemoryPregeneratedAudioChunkStore:
    def __init__(self) -> None:
        self.chunks: list[PregeneratedAudioChunk] = []

    async def replace_chunks(
        self,
        candidate_id: UUID,
        chunks: tuple[bytes, ...],
        *,
        audio_format: str = "riff_wave",
        created_at: datetime | None = None,
    ) -> tuple[PregeneratedAudioChunk, ...]:
        now = created_at or datetime.now(UTC)
        self.chunks = [
            chunk
            for chunk in self.chunks
            if chunk.utterance_candidate_id != candidate_id
        ]
        inserted = tuple(
            PregeneratedAudioChunk(
                id=uuid4(),
                utterance_candidate_id=candidate_id,
                chunk_index=index,
                audio_data=audio_data,
                audio_format=audio_format,
                is_last=index == len(chunks) - 1,
                created_at=now,
            )
            for index, audio_data in enumerate(chunks)
        )
        self.chunks.extend(inserted)
        return inserted

    async def fetch_chunks(
        self,
        candidate_id: UUID,
    ) -> tuple[PregeneratedAudioChunk, ...]:
        return tuple(
            sorted(
                (
                    chunk
                    for chunk in self.chunks
                    if chunk.utterance_candidate_id == candidate_id
                ),
                key=lambda chunk: chunk.chunk_index,
            )
        )


class PostgresCandidateStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def insert_utterance_candidate(
        self,
        *,
        seed: str,
        source: str,
        expires_at: datetime,
        priority: float = 0.5,
        urgent: bool = False,
        maturity: CandidateMaturity = 0,
        generated_text: str | None = None,
        generated_audio: bytes | None = None,
        context_tags: tuple[str, ...] = (),
        metadata_json: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate:
        _as_maturity(maturity)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO utterance_candidates (
                        seed,
                        generated_text,
                        generated_audio,
                        priority,
                        urgent,
                        created_at,
                        expires_at,
                        maturity,
                        source,
                        context_tags,
                        metadata_json
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now()),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    RETURNING
                        id,
                        seed,
                        generated_text,
                        generated_audio,
                        priority,
                        urgent,
                        created_at,
                        expires_at,
                        spoken_at,
                        dismissed_at,
                        maturity,
                        source,
                        context_tags,
                        metadata_json
                    """,
                    (
                        seed,
                        generated_text,
                        generated_audio,
                        priority,
                        urgent,
                        created_at,
                        expires_at,
                        maturity,
                        source,
                        list(context_tags),
                        Jsonb(metadata_json or {}),
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("utterance candidate insert returned no row")
        return UtteranceCandidate.from_db_row(row)

    async def insert_seed_candidate_once(
        self,
        seed: CandidateSeed,
        *,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate | None:
        now = created_at or datetime.now(UTC)
        dedupe_tag = dedupe_key_to_tag(seed.dedupe_key)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM utterance_candidates
                    WHERE spoken_at IS NULL
                      AND dismissed_at IS NULL
                      AND expires_at > %s
                      AND %s = ANY(context_tags)
                    LIMIT 1
                    """,
                    (now, dedupe_tag),
                )
                if await cur.fetchone() is not None:
                    return None

                await cur.execute(
                    """
                    INSERT INTO utterance_candidates (
                        seed,
                        priority,
                        urgent,
                        created_at,
                        expires_at,
                        maturity,
                        source,
                        context_tags,
                        metadata_json
                    )
                    VALUES (%s, %s, %s, COALESCE(%s, now()), %s, 0, %s, %s, %s)
                    RETURNING
                        id,
                        seed,
                        generated_text,
                        generated_audio,
                        priority,
                        urgent,
                        created_at,
                        expires_at,
                        spoken_at,
                        dismissed_at,
                        maturity,
                        source,
                        context_tags,
                        metadata_json
                    """,
                    (
                        seed.seed_text,
                        seed.priority,
                        seed.urgent,
                        created_at,
                        seed.expires_at,
                        seed.source,
                        list(seed.context_tags),
                        Jsonb(seed.metadata_json),
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("seed candidate insert returned no row")
        return UtteranceCandidate.from_db_row(row)

    async def insert_evaluated_utterance_once(
        self,
        seed: CandidateSeed,
        evaluated: EvaluatedUtterance | None,
        *,
        created_at: datetime | None = None,
    ) -> UtteranceCandidate | None:
        if evaluated is None or not evaluated.should_keep:
            return None

        now = created_at or datetime.now(UTC)
        dedupe_tag = dedupe_key_to_tag(seed.dedupe_key)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM utterance_candidates
                    WHERE spoken_at IS NULL
                      AND dismissed_at IS NULL
                      AND expires_at > %s
                      AND maturity >= 1
                      AND %s = ANY(context_tags)
                    LIMIT 1
                    """,
                    (now, dedupe_tag),
                )
                if await cur.fetchone() is not None:
                    return None

        return await self.insert_utterance_candidate(
            seed=seed.seed_text,
            source=seed.source,
            expires_at=seed.expires_at,
            priority=evaluated.priority,
            urgent=evaluated.urgent,
            maturity=1,
            generated_text=evaluated.generated_text,
            context_tags=evaluated.context_tags,
            metadata_json=seed.metadata_json,
            created_at=created_at,
        )

    async def fetch_active_utterance_candidates(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UtteranceCandidate]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        seed,
                        generated_text,
                        generated_audio,
                        priority,
                        urgent,
                        created_at,
                        expires_at,
                        spoken_at,
                        dismissed_at,
                        maturity,
                        source,
                        context_tags,
                        metadata_json
                    FROM utterance_candidates
                    WHERE spoken_at IS NULL
                      AND dismissed_at IS NULL
                      AND expires_at > %s
                    ORDER BY priority DESC, created_at ASC
                    LIMIT %s
                    """,
                    (now, limit),
                )
                rows = await cur.fetchall()
        return [UtteranceCandidate.from_db_row(row) for row in rows]

    async def mark_utterance_spoken(
        self,
        candidate_id: UUID,
        *,
        spoken_at: datetime,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE utterance_candidates
                    SET spoken_at = COALESCE(spoken_at, %s)
                    WHERE id = %s
                    """,
                    (spoken_at, candidate_id),
                )

    async def mark_utterance_pregenerated(
        self,
        candidate_id: UUID,
        *,
        generated_audio: bytes,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE utterance_candidates
                    SET generated_audio = %s,
                        maturity = GREATEST(maturity, 2)
                    WHERE id = %s
                    """,
                    (generated_audio, candidate_id),
                )

    async def dismiss_utterance_candidate(
        self,
        candidate_id: UUID,
        *,
        dismissed_at: datetime,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE utterance_candidates
                    SET dismissed_at = COALESCE(dismissed_at, %s)
                    WHERE id = %s
                    """,
                    (dismissed_at, candidate_id),
                )

    async def mark_expired_utterance_candidates(self, now: datetime) -> int:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE utterance_candidates
                    SET dismissed_at = %s
                    WHERE spoken_at IS NULL
                      AND dismissed_at IS NULL
                      AND expires_at <= %s
                    """,
                    (now, now),
                )
                return cur.rowcount or 0

    async def insert_arrival_candidate(
        self,
        *,
        context_snapshot: ArrivalContextSnapshot,
        behavior: ArrivalBehavior,
        valid_until: datetime,
        computed_at: datetime | None = None,
        utterance_text: str | None = None,
        utterance_audio: bytes | None = None,
    ) -> ArrivalCandidate:
        _as_arrival_behavior(behavior)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO arrival_candidates (
                        device_id,
                        computed_at,
                        valid_until,
                        context_snapshot,
                        behavior,
                        utterance_text,
                        utterance_audio
                    )
                    VALUES (%s, COALESCE(%s, now()), %s, %s, %s, %s, %s)
                    RETURNING
                        id,
                        computed_at,
                        valid_until,
                        context_snapshot,
                        behavior,
                        utterance_text,
                        utterance_audio,
                        used_at
                    """,
                    (
                        context_snapshot.device_id,
                        computed_at,
                        valid_until,
                        Jsonb(context_snapshot.to_json()),
                        behavior,
                        utterance_text,
                        utterance_audio,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("arrival candidate insert returned no row")
        return ArrivalCandidate.from_db_row(row)

    async def fetch_latest_fresh_arrival_candidate(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalCandidate | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        computed_at,
                        valid_until,
                        context_snapshot,
                        behavior,
                        utterance_text,
                        utterance_audio,
                        used_at
                    FROM arrival_candidates
                    WHERE used_at IS NULL
                      AND valid_until > %s
                      AND (%s::text IS NULL OR device_id IS NULL OR device_id = %s)
                    ORDER BY computed_at DESC
                    LIMIT 1
                    """,
                    (now, device_id, device_id),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return ArrivalCandidate.from_db_row(row)

    async def mark_arrival_used(
        self,
        candidate_id: UUID,
        *,
        used_at: datetime,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE arrival_candidates
                    SET used_at = COALESCE(used_at, %s)
                    WHERE id = %s
                    """,
                    (used_at, candidate_id),
                )

    async def delete_expired_arrival_candidates(
        self,
        *,
        older_than: datetime,
    ) -> int:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM arrival_candidates
                    WHERE valid_until < %s
                    """,
                    (older_than,),
                )
                return cur.rowcount or 0


class PostgresPregeneratedAudioChunkStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def replace_chunks(
        self,
        candidate_id: UUID,
        chunks: tuple[bytes, ...],
        *,
        audio_format: str = "riff_wave",
        created_at: datetime | None = None,
    ) -> tuple[PregeneratedAudioChunk, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM pregenerated_audio_chunks
                    WHERE utterance_candidate_id = %s
                    """,
                    (candidate_id,),
                )
                rows: list[tuple[object, ...]] = []
                for index, audio_data in enumerate(chunks):
                    await cur.execute(
                        """
                        INSERT INTO pregenerated_audio_chunks (
                            utterance_candidate_id,
                            chunk_index,
                            audio_data,
                            audio_format,
                            is_last,
                            created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
                        RETURNING
                            id,
                            utterance_candidate_id,
                            chunk_index,
                            audio_data,
                            audio_format,
                            is_last,
                            created_at
                        """,
                        (
                            candidate_id,
                            index,
                            audio_data,
                            audio_format,
                            index == len(chunks) - 1,
                            created_at,
                        ),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        raise RuntimeError("pregenerated audio insert returned no row")
                    rows.append(row)
            await conn.commit()
        return tuple(PregeneratedAudioChunk.from_db_row(row) for row in rows)

    async def fetch_chunks(
        self,
        candidate_id: UUID,
    ) -> tuple[PregeneratedAudioChunk, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        utterance_candidate_id,
                        chunk_index,
                        audio_data,
                        audio_format,
                        is_last,
                        created_at
                    FROM pregenerated_audio_chunks
                    WHERE utterance_candidate_id = %s
                    ORDER BY chunk_index ASC
                    """,
                    (candidate_id,),
                )
                rows = await cur.fetchall()
        return tuple(PregeneratedAudioChunk.from_db_row(row) for row in rows)


def _as_sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Expected datetime value, got {type(value)!r}")


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _as_datetime(value)


def _parse_datetime_value(value: object) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return _as_datetime(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _int_or_zero(value: object) -> int:
    if value is None:
        return 0
    return int(value)


def _format_local_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _as_maturity(value: object) -> CandidateMaturity:
    maturity = int(value)
    if maturity not in _VALID_MATURITIES:
        raise ValueError(f"Unsupported candidate maturity: {maturity}")
    return maturity  # type: ignore[return-value]


def _as_arrival_behavior(value: object) -> ArrivalBehavior:
    behavior = str(value)
    if behavior not in _VALID_ARRIVAL_BEHAVIORS:
        raise ValueError(f"Unsupported arrival behavior: {behavior}")
    return behavior  # type: ignore[return-value]


def dedupe_key_to_tag(dedupe_key: str) -> str:
    return f"{_DEDUPE_TAG_PREFIX}{dedupe_key}"
