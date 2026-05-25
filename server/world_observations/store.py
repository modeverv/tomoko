from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from server.shared.models import (
    WorldObservationDocumentRecord,
    WorldObservationDocumentStatus,
    WorldObservationInterpretation,
    WorldObservationInterpretationRecord,
    WorldObservationItemRecord,
    WorldObservationNormalizedBatch,
    WorldObservationRawDocument,
)


class WorldObservationStore(Protocol):
    async def import_raw_document_once(
        self,
        document: WorldObservationRawDocument,
        *,
        checksum: str,
        imported_at: datetime | None = None,
    ) -> tuple[WorldObservationDocumentRecord, bool]: ...

    async def save_normalized_batch(
        self,
        document_id: UUID,
        batch: WorldObservationNormalizedBatch,
    ) -> tuple[WorldObservationItemRecord, ...]: ...

    async def mark_document_status(
        self,
        document_id: UUID,
        status: WorldObservationDocumentStatus,
    ) -> None: ...

    async def fetch_items_without_interpretation(
        self,
        *,
        limit: int,
        min_confidence: float = 0.35,
    ) -> tuple[WorldObservationItemRecord, ...]: ...

    async def save_interpretation(
        self,
        interpretation: WorldObservationInterpretation,
    ) -> WorldObservationInterpretationRecord: ...

    async def fetch_candidate_interpretations(
        self,
        *,
        limit: int,
        min_confidence: float = 0.45,
        min_interest: float = 0.45,
    ) -> tuple[WorldObservationInterpretationRecord, ...]: ...

    async def fetch_journalist_interpretations(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[WorldObservationInterpretationRecord, ...]: ...


class InMemoryWorldObservationStore:
    def __init__(self) -> None:
        self.documents: list[WorldObservationDocumentRecord] = []
        self.items: list[WorldObservationItemRecord] = []
        self.interpretations: list[WorldObservationInterpretationRecord] = []

    async def import_raw_document_once(
        self,
        document: WorldObservationRawDocument,
        *,
        checksum: str,
        imported_at: datetime | None = None,
    ) -> tuple[WorldObservationDocumentRecord, bool]:
        for existing in self.documents:
            if existing.sha256_checksum == checksum:
                return existing, False
        if document.metadata is None:
            raise ValueError("cannot import invalid raw document")
        record = WorldObservationDocumentRecord(
            id=uuid4(),
            raw_file_path=document.path,
            sha256_checksum=checksum,
            generated_by=document.metadata.generated_by,
            observed_at=document.metadata.observed_at,
            imported_at=imported_at or datetime.now(UTC),
            status="pending",
            metadata_json=document.metadata.to_json(),
            parse_issues_json=[issue.to_json() for issue in document.issues],
        )
        self.documents.append(record)
        return record, True

    async def save_normalized_batch(
        self,
        document_id: UUID,
        batch: WorldObservationNormalizedBatch,
    ) -> tuple[WorldObservationItemRecord, ...]:
        self.items = [item for item in self.items if item.document_id != document_id]
        now = datetime.now(UTC)
        inserted = tuple(
            WorldObservationItemRecord(
                id=uuid4(),
                document_id=document_id,
                topic=item.topic,
                title=item.title,
                summary=item.summary,
                source_hint=item.source_hint,
                freshness=item.freshness,
                confidence=item.confidence,
                item_json={
                    **item.item_json,
                    "parse_notes": list(item.parse_notes),
                    "normalize_trace": batch.trace.to_json(),
                },
                raw_excerpt=item.raw_excerpt,
                created_at=now,
            )
            for item in batch.items
        )
        self.items.extend(inserted)
        await self.mark_document_status(document_id, "completed")
        return inserted

    async def mark_document_status(
        self,
        document_id: UUID,
        status: WorldObservationDocumentStatus,
    ) -> None:
        for index, document in enumerate(self.documents):
            if document.id == document_id:
                self.documents[index] = replace(document, status=status)
                return

    async def fetch_items_without_interpretation(
        self,
        *,
        limit: int,
        min_confidence: float = 0.35,
    ) -> tuple[WorldObservationItemRecord, ...]:
        interpreted = {record.item_id for record in self.interpretations}
        return tuple(
            item
            for item in self.items
            if item.id not in interpreted and item.confidence >= min_confidence
        )[:limit]

    async def save_interpretation(
        self,
        interpretation: WorldObservationInterpretation,
    ) -> WorldObservationInterpretationRecord:
        item = next(item for item in self.items if item.id == interpretation.item_id)
        existing_index = next(
            (
                index
                for index, record in enumerate(self.interpretations)
                if record.item_id == interpretation.item_id
            ),
            None,
        )
        record = WorldObservationInterpretationRecord(
            id=uuid4(),
            item_id=item.id,
            document_id=item.document_id,
            topic=item.topic,
            title=item.title,
            summary=item.summary,
            source_hint=item.source_hint,
            freshness=item.freshness,
            confidence=item.confidence,
            persona_state_version_id=interpretation.persona_state_version_id,
            persona_lexicon_version_id=interpretation.persona_lexicon_version_id,
            relevance_to_user=interpretation.relevance_to_user,
            tomoko_interest=interpretation.tomoko_interest,
            emotional_tone=interpretation.emotional_tone,
            memory_value=interpretation.memory_value,
            speakability_hint=interpretation.speakability_hint,
            interpretation_text=interpretation.interpretation_text,
            reason_json=interpretation.reason_json,
            created_at=datetime.now(UTC),
        )
        if existing_index is None:
            self.interpretations.append(record)
        else:
            self.interpretations[existing_index] = record
        return record

    async def fetch_candidate_interpretations(
        self,
        *,
        limit: int,
        min_confidence: float = 0.45,
        min_interest: float = 0.45,
    ) -> tuple[WorldObservationInterpretationRecord, ...]:
        records = [
            record
            for record in self.interpretations
            if record.confidence >= min_confidence
            and max(record.tomoko_interest, record.relevance_to_user) >= min_interest
            and record.freshness != "stale"
        ]
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    -max(record.tomoko_interest, record.relevance_to_user),
                    -record.confidence,
                    record.created_at,
                ),
            )[:limit]
        )

    async def fetch_journalist_interpretations(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[WorldObservationInterpretationRecord, ...]:
        return tuple(
            record
            for record in self.interpretations
            if started_at <= record.created_at < ended_at
        )[:limit]


class PostgresWorldObservationStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def import_raw_document_once(
        self,
        document: WorldObservationRawDocument,
        *,
        checksum: str,
        imported_at: datetime | None = None,
    ) -> tuple[WorldObservationDocumentRecord, bool]:
        if document.metadata is None:
            raise ValueError("cannot import invalid raw document")
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO world_observation_documents (
                        raw_file_path,
                        sha256_checksum,
                        generated_by,
                        observed_at,
                        imported_at,
                        status,
                        metadata_json,
                        parse_issues_json
                    )
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()), 'pending', %s, %s)
                    ON CONFLICT (sha256_checksum) DO NOTHING
                    RETURNING
                        id,
                        raw_file_path,
                        sha256_checksum,
                        generated_by,
                        observed_at,
                        imported_at,
                        status,
                        metadata_json,
                        parse_issues_json
                    """,
                    (
                        document.path,
                        checksum,
                        document.metadata.generated_by,
                        document.metadata.observed_at,
                        imported_at,
                        Jsonb(document.metadata.to_json()),
                        Jsonb([issue.to_json() for issue in document.issues]),
                    ),
                )
                row = await cur.fetchone()
                inserted = row is not None
                if row is None:
                    await cur.execute(
                        """
                        SELECT
                            id,
                            raw_file_path,
                            sha256_checksum,
                            generated_by,
                            observed_at,
                            imported_at,
                            status,
                            metadata_json,
                            parse_issues_json
                        FROM world_observation_documents
                        WHERE sha256_checksum = %s
                        """,
                        (checksum,),
                    )
                    row = await cur.fetchone()
        if row is None:
            raise RuntimeError("world observation document import returned no row")
        return _document_from_row(row), inserted

    async def save_normalized_batch(
        self,
        document_id: UUID,
        batch: WorldObservationNormalizedBatch,
    ) -> tuple[WorldObservationItemRecord, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM world_observation_items WHERE document_id = %s",
                    (document_id,),
                )
                rows = []
                for item in batch.items:
                    await cur.execute(
                        """
                        INSERT INTO world_observation_items (
                            document_id,
                            topic,
                            title,
                            summary,
                            source_hint,
                            freshness,
                            confidence,
                            item_json,
                            raw_excerpt
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING
                            id,
                            document_id,
                            topic,
                            title,
                            summary,
                            source_hint,
                            freshness,
                            confidence,
                            item_json,
                            raw_excerpt,
                            created_at
                        """,
                        (
                            document_id,
                            item.topic,
                            item.title,
                            item.summary,
                            item.source_hint,
                            item.freshness,
                            item.confidence,
                            Jsonb(
                                {
                                    **item.item_json,
                                    "parse_notes": list(item.parse_notes),
                                    "normalize_trace": batch.trace.to_json(),
                                }
                            ),
                            item.raw_excerpt,
                        ),
                    )
                    rows.append(await cur.fetchone())
                await cur.execute(
                    """
                    UPDATE world_observation_documents
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (document_id,),
                )
        return tuple(_item_from_row(row) for row in rows if row is not None)

    async def mark_document_status(
        self,
        document_id: UUID,
        status: WorldObservationDocumentStatus,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE world_observation_documents
                    SET status = %s
                    WHERE id = %s
                    """,
                    (status, document_id),
                )

    async def fetch_items_without_interpretation(
        self,
        *,
        limit: int,
        min_confidence: float = 0.35,
    ) -> tuple[WorldObservationItemRecord, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        i.id,
                        i.document_id,
                        i.topic,
                        i.title,
                        i.summary,
                        i.source_hint,
                        i.freshness,
                        i.confidence,
                        i.item_json,
                        i.raw_excerpt,
                        i.created_at
                    FROM world_observation_items i
                    LEFT JOIN world_observation_interpretations p
                      ON p.item_id = i.id
                    WHERE p.id IS NULL
                      AND i.confidence >= %s
                    ORDER BY i.created_at ASC
                    LIMIT %s
                    """,
                    (min_confidence, limit),
                )
                rows = await cur.fetchall()
        return tuple(_item_from_row(row) for row in rows)

    async def save_interpretation(
        self,
        interpretation: WorldObservationInterpretation,
    ) -> WorldObservationInterpretationRecord:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO world_observation_interpretations (
                        item_id,
                        persona_state_version_id,
                        persona_lexicon_version_id,
                        relevance_to_user,
                        tomoko_interest,
                        emotional_tone,
                        memory_value,
                        speakability_hint,
                        interpretation_text,
                        reason_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (item_id) DO UPDATE SET
                        persona_state_version_id = EXCLUDED.persona_state_version_id,
                        persona_lexicon_version_id = EXCLUDED.persona_lexicon_version_id,
                        relevance_to_user = EXCLUDED.relevance_to_user,
                        tomoko_interest = EXCLUDED.tomoko_interest,
                        emotional_tone = EXCLUDED.emotional_tone,
                        memory_value = EXCLUDED.memory_value,
                        speakability_hint = EXCLUDED.speakability_hint,
                        interpretation_text = EXCLUDED.interpretation_text,
                        reason_json = EXCLUDED.reason_json
                    RETURNING id
                    """,
                    (
                        interpretation.item_id,
                        interpretation.persona_state_version_id,
                        interpretation.persona_lexicon_version_id,
                        interpretation.relevance_to_user,
                        interpretation.tomoko_interest,
                        interpretation.emotional_tone,
                        interpretation.memory_value,
                        interpretation.speakability_hint,
                        interpretation.interpretation_text,
                        Jsonb(interpretation.reason_json),
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("world observation interpretation returned no id")
                return await self._fetch_interpretation_record(cur, row[0])

    async def fetch_candidate_interpretations(
        self,
        *,
        limit: int,
        min_confidence: float = 0.45,
        min_interest: float = 0.45,
    ) -> tuple[WorldObservationInterpretationRecord, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM world_observation_trace
                    WHERE confidence >= %s
                      AND GREATEST(tomoko_interest, relevance_to_user) >= %s
                      AND freshness <> 'stale'
                      AND interpretation_id IS NOT NULL
                    ORDER BY
                      GREATEST(tomoko_interest, relevance_to_user) DESC,
                      confidence DESC,
                      interpretation_created_at ASC
                    LIMIT %s
                    """,
                    (min_confidence, min_interest, limit),
                )
                rows = await cur.fetchall()
        return tuple(_interpretation_from_trace_row(row) for row in rows)

    async def fetch_journalist_interpretations(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[WorldObservationInterpretationRecord, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM world_observation_trace
                    WHERE interpretation_created_at >= %s
                      AND interpretation_created_at < %s
                      AND interpretation_id IS NOT NULL
                    ORDER BY interpretation_created_at ASC
                    LIMIT %s
                    """,
                    (started_at, ended_at, limit),
                )
                rows = await cur.fetchall()
        return tuple(_interpretation_from_trace_row(row) for row in rows)

    async def _fetch_interpretation_record(
        self,
        cur: psycopg.AsyncCursor,
        interpretation_id: UUID,
    ) -> WorldObservationInterpretationRecord:
        await cur.execute(
            """
            SELECT * FROM world_observation_trace
            WHERE interpretation_id = %s
            """,
            (interpretation_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("world observation interpretation trace row missing")
        return _interpretation_from_trace_row(row)


def _document_from_row(row: tuple[object, ...]) -> WorldObservationDocumentRecord:
    return WorldObservationDocumentRecord(
        id=_as_uuid(row[0]),
        raw_file_path=str(row[1]),
        sha256_checksum=str(row[2]),
        generated_by=str(row[3]),
        observed_at=_as_datetime(row[4]),
        imported_at=_as_datetime(row[5]),
        status=str(row[6]),  # type: ignore[arg-type]
        metadata_json=dict(row[7] or {}),
        parse_issues_json=list(row[8] or []),
    )


def _item_from_row(row: tuple[object, ...]) -> WorldObservationItemRecord:
    freshness = str(row[6])
    if freshness not in {"breaking", "fresh", "recent", "stale", "unknown"}:
        freshness = "unknown"
    return WorldObservationItemRecord(
        id=_as_uuid(row[0]),
        document_id=_as_uuid(row[1]),
        topic=str(row[2]),
        title=str(row[3]),
        summary=str(row[4]),
        source_hint=str(row[5]),
        freshness=freshness,  # type: ignore[arg-type]
        confidence=float(row[7]),
        item_json=dict(row[8] or {}),
        raw_excerpt=str(row[9]),
        created_at=_as_datetime(row[10]),
    )


def _interpretation_from_trace_row(
    row: tuple[object, ...],
) -> WorldObservationInterpretationRecord:
    tone = str(row[17])
    if tone not in {"neutral", "hopeful", "concerned", "curious", "playful", "sad"}:
        tone = "neutral"
    freshness = str(row[9])
    if freshness not in {"breaking", "fresh", "recent", "stale", "unknown"}:
        freshness = "unknown"
    return WorldObservationInterpretationRecord(
        id=_as_uuid(row[12]),
        item_id=_as_uuid(row[5]),
        document_id=_as_uuid(row[0]),
        topic=str(row[6]),
        title=str(row[7]),
        summary=str(row[8]),
        source_hint=str(row[10]),
        freshness=freshness,  # type: ignore[arg-type]
        confidence=float(row[11]),
        persona_state_version_id=_optional_uuid(row[13]),
        persona_lexicon_version_id=_optional_uuid(row[14]),
        relevance_to_user=float(row[15]),
        tomoko_interest=float(row[16]),
        emotional_tone=tone,  # type: ignore[arg-type]
        memory_value=float(row[18]),
        speakability_hint=str(row[19]),
        interpretation_text=str(row[20]),
        reason_json=dict(row[21] or {}),
        created_at=_as_datetime(row[22]),
    )


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _optional_uuid(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    return _as_uuid(value)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
