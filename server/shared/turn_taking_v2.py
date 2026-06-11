from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from server.shared.db_pool import pooled_connection
from server.shared.models import (
    PartialTranscriptObservation,
    TurnTakingV2Advisory,
    _optional_float,
    _optional_int,
)

if TYPE_CHECKING:
    pass


class TurnTakingV2Store(Protocol):
    async def save_observation(
        self,
        *,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        revision: int,
        vad_state: str | None,
        attention_mode: str | None,
        raw_text: str,
        filtered_text: str | None,
        stable_text: str | None,
        unstable_tail: str | None,
        audio_level_db: float | None,
        source: str | None,
    ) -> UUID: ...

    async def get_observation(
        self,
        observation_id: UUID,
    ) -> PartialTranscriptObservation | None: ...

    async def save_advisory(
        self,
        *,
        observation_id: UUID | None,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        semantic_saturation: float | None,
        remaining_info_risk: float | None,
        semantic_split_risk: float | None,
        speech_decision_score: float | None,
        safe_response_level: int | None,
        proposal: str | None,
        confidence: float | None,
        would_start_inference: bool | None,
        reason: str | None,
    ) -> UUID: ...

    async def get_advisory(
        self,
        advisory_id: UUID,
    ) -> TurnTakingV2Advisory | None: ...

    async def get_turn_history(
        self,
        *,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        before_revision: int | None = None,
    ) -> list[str]: ...


class PostgresTurnTakingV2Store:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def save_observation(
        self,
        *,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        revision: int,
        vad_state: str | None,
        attention_mode: str | None,
        raw_text: str,
        filtered_text: str | None,
        stable_text: str | None,
        unstable_tail: str | None,
        audio_level_db: float | None,
        source: str | None,
    ) -> UUID:
        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO partial_transcript_observations (
                        conversation_session_id,
                        turn_id,
                        revision,
                        observed_at,
                        vad_state,
                        attention_mode,
                        raw_text,
                        filtered_text,
                        stable_text,
                        unstable_tail,
                        audio_level_db,
                        source
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        conversation_session_id,
                        turn_id,
                        revision,
                        datetime.now(UTC),
                        vad_state,
                        attention_mode,
                        raw_text,
                        filtered_text,
                        stable_text,
                        unstable_tail,
                        audio_level_db,
                        source,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("Failed to insert partial_transcript_observation")
                obs_id = row[0]

                # pg_notify
                await cur.execute(
                    "SELECT pg_notify('turn_taking_v2_observation', %s)",
                    (str(obs_id),),
                )
                return obs_id

    async def get_observation(self, observation_id: UUID) -> PartialTranscriptObservation | None:
        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id, conversation_session_id, turn_id, revision, observed_at,
                        vad_state, attention_mode, raw_text, filtered_text,
                        stable_text, unstable_tail, audio_level_db, source
                    FROM partial_transcript_observations
                    WHERE id = %s
                    """,
                    (observation_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                return PartialTranscriptObservation(
                    id=row[0],
                    conversation_session_id=row[1],
                    turn_id=row[2],
                    revision=row[3],
                    observed_at=row[4],
                    vad_state=row[5],
                    attention_mode=row[6],
                    raw_text=row[7],
                    filtered_text=row[8],
                    stable_text=row[9],
                    unstable_tail=row[10],
                    audio_level_db=_optional_float(row[11]),
                    source=row[12],
                )

    async def save_advisory(
        self,
        *,
        observation_id: UUID | None,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        semantic_saturation: float | None,
        remaining_info_risk: float | None,
        semantic_split_risk: float | None,
        speech_decision_score: float | None,
        safe_response_level: int | None,
        proposal: str | None,
        confidence: float | None,
        would_start_inference: bool | None,
        reason: str | None,
    ) -> UUID:
        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO turn_taking_v2_advisories (
                        observation_id,
                        conversation_session_id,
                        turn_id,
                        created_at,
                        semantic_saturation,
                        remaining_info_risk,
                        semantic_split_risk,
                        speech_decision_score,
                        safe_response_level,
                        proposal,
                        confidence,
                        would_start_inference,
                        reason
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        observation_id,
                        conversation_session_id,
                        turn_id,
                        datetime.now(UTC),
                        semantic_saturation,
                        remaining_info_risk,
                        semantic_split_risk,
                        speech_decision_score,
                        safe_response_level,
                        proposal,
                        confidence,
                        would_start_inference,
                        reason,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("Failed to insert turn_taking_v2_advisory")
                advisory_id = row[0]

                # pg_notify
                await cur.execute(
                    "SELECT pg_notify('turn_taking_v2_advisory', %s)",
                    (str(advisory_id),),
                )
                return advisory_id

    async def get_advisory(self, advisory_id: UUID) -> TurnTakingV2Advisory | None:
        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id, observation_id, conversation_session_id, turn_id, created_at,
                        semantic_saturation, remaining_info_risk, semantic_split_risk,
                        speech_decision_score, safe_response_level, proposal, confidence,
                        would_start_inference, reason
                    FROM turn_taking_v2_advisories
                    WHERE id = %s
                    """,
                    (advisory_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                return TurnTakingV2Advisory(
                    id=row[0],
                    observation_id=row[1],
                    conversation_session_id=row[2],
                    turn_id=row[3],
                    created_at=row[4],
                    semantic_saturation=_optional_float(row[5]),
                    remaining_info_risk=_optional_float(row[6]),
                    semantic_split_risk=_optional_float(row[7]),
                    speech_decision_score=_optional_float(row[8]),
                    safe_response_level=_optional_int(row[9]),
                    proposal=row[10],
                    confidence=_optional_float(row[11]),
                    would_start_inference=row[12],
                    reason=row[13],
                )

    async def get_turn_history(
        self,
        *,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        before_revision: int | None = None,
    ) -> list[str]:
        if conversation_session_id is None or turn_id is None:
            return []
        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                if before_revision is not None:
                    await cur.execute(
                        """
                        SELECT raw_text FROM partial_transcript_observations
                        WHERE conversation_session_id = %s
                          AND turn_id = %s
                          AND revision < %s
                        ORDER BY revision ASC
                        """,
                        (conversation_session_id, turn_id, before_revision),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT raw_text FROM partial_transcript_observations
                        WHERE conversation_session_id = %s
                          AND turn_id = %s
                        ORDER BY revision ASC
                        """,
                        (conversation_session_id, turn_id),
                    )
                rows = await cur.fetchall()
                return [row[0] for row in rows]


class NullTurnTakingV2Store:
    async def save_observation(self, **kwargs) -> UUID:
        import uuid
        return uuid.uuid4()

    async def get_observation(self, observation_id: UUID) -> PartialTranscriptObservation | None:
        return None

    async def save_advisory(self, **kwargs) -> UUID:
        import uuid
        return uuid.uuid4()

    async def get_advisory(self, advisory_id: UUID) -> TurnTakingV2Advisory | None:
        return None

    async def get_turn_history(
        self,
        *,
        conversation_session_id: UUID | None,
        turn_id: UUID | None,
        before_revision: int | None = None,
    ) -> list[str]:
        return []
