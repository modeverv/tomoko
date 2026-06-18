from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from server.shared.models import (
    HumanActivityObservation,
    HumanPresenceObservation,
    PerceptionFrame,
    PerceptionFrameSource,
    ScreenActivityObservation,
    UserContextSnapshot,
)

PERCEPTION_FRAMES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS perception_frames (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    device_id TEXT,
    captured_at TIMESTAMPTZ NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    retained BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT perception_frames_source_check
        CHECK (source IN ('camera', 'screenshot')),
    CONSTRAINT perception_frames_width_check
        CHECK (width IS NULL OR width > 0),
    CONSTRAINT perception_frames_height_check
        CHECK (height IS NULL OR height > 0)
);

CREATE INDEX IF NOT EXISTS perception_frames_source_captured_idx
    ON perception_frames (source, captured_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS perception_frames_retained_source_captured_idx
    ON perception_frames (retained, source, captured_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS perception_frames_sha256_idx
    ON perception_frames (sha256);
"""

HUMAN_PRESENCE_OBSERVATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS human_presence_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id UUID NOT NULL REFERENCES perception_frames(id)
        ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    present BOOLEAN NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    model TEXT NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT human_presence_observations_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS human_presence_observations_frame_idx
    ON human_presence_observations (frame_id);

CREATE INDEX IF NOT EXISTS human_presence_observations_observed_idx
    ON human_presence_observations (observed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS human_presence_observations_present_observed_idx
    ON human_presence_observations (present, observed_at DESC);
"""

HUMAN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS human_activity_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id UUID NOT NULL REFERENCES perception_frames(id)
        ON DELETE CASCADE,
    presence_observation_id UUID REFERENCES human_presence_observations(id)
        ON DELETE SET NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    activity_label TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    model TEXT NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT human_activity_observations_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS human_activity_observations_frame_idx
    ON human_activity_observations (frame_id);

CREATE INDEX IF NOT EXISTS human_activity_observations_observed_idx
    ON human_activity_observations (observed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS human_activity_observations_label_observed_idx
    ON human_activity_observations (activity_label, observed_at DESC);
"""

SCREEN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS screen_activity_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id UUID NOT NULL REFERENCES perception_frames(id)
        ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    screen_activity_label TEXT NOT NULL,
    app_hint TEXT,
    document_hint TEXT,
    url_hint TEXT,
    confidence DOUBLE PRECISION NOT NULL,
    model TEXT NOT NULL,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT screen_activity_observations_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE UNIQUE INDEX IF NOT EXISTS screen_activity_observations_frame_idx
    ON screen_activity_observations (frame_id);

CREATE INDEX IF NOT EXISTS screen_activity_observations_observed_idx
    ON screen_activity_observations (observed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS screen_activity_observations_label_observed_idx
    ON screen_activity_observations (screen_activity_label, observed_at DESC);
"""

USER_CONTEXT_SNAPSHOTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_context_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at TIMESTAMPTZ NOT NULL,
    device_id TEXT,
    present BOOLEAN,
    presence_observed_at TIMESTAMPTZ,
    activity_label TEXT,
    activity_observed_at TIMESTAMPTZ,
    screen_activity_label TEXT,
    screen_observed_at TIMESTAMPTZ,
    calendar_summary TEXT,
    world_summary TEXT,
    user_activity_summary TEXT NOT NULL,
    context_summary TEXT NOT NULL,
    interaction_readiness TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    source_frame_ids UUID[] NOT NULL DEFAULT '{}',
    source_observation_ids UUID[] NOT NULL DEFAULT '{}',
    model TEXT,
    raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_context_snapshots_readiness_check
        CHECK (
            interaction_readiness IN (
                'away',
                'do_not_disturb',
                'low_intrusion_ok',
                'chat_ok',
                'needs_help_maybe'
            )
        ),
    CONSTRAINT user_context_snapshots_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS user_context_snapshots_computed_idx
    ON user_context_snapshots (computed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS user_context_snapshots_device_computed_idx
    ON user_context_snapshots (device_id, computed_at DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS user_context_snapshots_readiness_computed_idx
    ON user_context_snapshots (interaction_readiness, computed_at DESC);
"""


class PerceptionFrameStore(Protocol):
    async def insert_frame(
        self,
        *,
        source: PerceptionFrameSource,
        file_path: str,
        sha256: str,
        captured_at: datetime,
        device_id: str | None = None,
        width: int | None = None,
        height: int | None = None,
        frame_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> PerceptionFrame: ...

    async def fetch_frame(self, frame_id: UUID) -> PerceptionFrame | None: ...

    async def fetch_retained_frames(
        self,
        *,
        source: PerceptionFrameSource,
        limit: int,
    ) -> list[PerceptionFrame]: ...

    async def apply_retention(
        self,
        *,
        source: PerceptionFrameSource,
        keep_latest: int = 100,
    ) -> int: ...


class HumanPresenceObservationStore(Protocol):
    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        present: bool,
        confidence: float,
        model: str,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> HumanPresenceObservation: ...

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> HumanPresenceObservation | None: ...

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[HumanPresenceObservation]: ...


class HumanActivityObservationStore(Protocol):
    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        activity_label: str,
        confidence: float,
        model: str,
        presence_observation_id: UUID | None = None,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> HumanActivityObservation: ...

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> HumanActivityObservation | None: ...

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[HumanActivityObservation]: ...


class ScreenActivityObservationStore(Protocol):
    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        screen_activity_label: str,
        confidence: float,
        model: str,
        app_hint: str | None = None,
        document_hint: str | None = None,
        url_hint: str | None = None,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ScreenActivityObservation: ...

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> ScreenActivityObservation | None: ...

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[ScreenActivityObservation]: ...


class UserContextSnapshotStore(Protocol):
    async def insert_snapshot(
        self,
        snapshot: UserContextSnapshot,
    ) -> UserContextSnapshot: ...

    async def fetch_latest(
        self,
        *,
        limit: int,
        device_id: str | None = None,
    ) -> list[UserContextSnapshot]: ...


class InMemoryPerceptionFrameStore:
    def __init__(self) -> None:
        self.frames: list[PerceptionFrame] = []

    async def insert_frame(
        self,
        *,
        source: PerceptionFrameSource,
        file_path: str,
        sha256: str,
        captured_at: datetime,
        device_id: str | None = None,
        width: int | None = None,
        height: int | None = None,
        frame_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> PerceptionFrame:
        frame = PerceptionFrame(
            id=frame_id or uuid4(),
            source=source,
            device_id=device_id,
            captured_at=captured_at,
            file_path=file_path,
            sha256=sha256,
            width=width,
            height=height,
            retained=True,
            created_at=created_at or datetime.now(UTC),
        )
        self.frames.append(frame)
        return frame

    async def fetch_frame(self, frame_id: UUID) -> PerceptionFrame | None:
        for frame in self.frames:
            if frame.id == frame_id:
                return frame
        return None

    async def fetch_retained_frames(
        self,
        *,
        source: PerceptionFrameSource,
        limit: int,
    ) -> list[PerceptionFrame]:
        return _latest_first(
            [frame for frame in self.frames if frame.source == source and frame.retained]
        )[:limit]

    async def apply_retention(
        self,
        *,
        source: PerceptionFrameSource,
        keep_latest: int = 100,
    ) -> int:
        if keep_latest < 0:
            raise ValueError("keep_latest must be non-negative")
        retained = _latest_first(
            [frame for frame in self.frames if frame.source == source and frame.retained]
        )
        retire_ids = {frame.id for frame in retained[keep_latest:]}
        if not retire_ids:
            return 0
        replaced: list[PerceptionFrame] = []
        retired_count = 0
        for frame in self.frames:
            if frame.id in retire_ids:
                replaced.append(_replace_retained(frame, retained=False))
                retired_count += 1
            else:
                replaced.append(frame)
        self.frames = replaced
        return retired_count


class InMemoryHumanPresenceObservationStore:
    def __init__(self) -> None:
        self.observations: list[HumanPresenceObservation] = []

    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        present: bool,
        confidence: float,
        model: str,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> HumanPresenceObservation:
        existing = await self.fetch_by_frame(frame_id)
        if existing is not None:
            return existing
        observation = HumanPresenceObservation(
            id=observation_id or uuid4(),
            frame_id=frame_id,
            observed_at=observed_at,
            present=present,
            confidence=confidence,
            model=model,
            raw_reason_json=dict(raw_reason_json or {}),
            created_at=created_at or datetime.now(UTC),
        )
        self.observations.append(observation)
        return observation

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> HumanPresenceObservation | None:
        for observation in self.observations:
            if observation.frame_id == frame_id:
                return observation
        return None

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[HumanPresenceObservation]:
        return sorted(
            self.observations,
            key=lambda observation: (
                observation.observed_at,
                observation.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )[:limit]


class InMemoryHumanActivityObservationStore:
    def __init__(self) -> None:
        self.observations: list[HumanActivityObservation] = []

    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        activity_label: str,
        confidence: float,
        model: str,
        presence_observation_id: UUID | None = None,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> HumanActivityObservation:
        existing = await self.fetch_by_frame(frame_id)
        if existing is not None:
            return existing
        observation = HumanActivityObservation(
            id=observation_id or uuid4(),
            frame_id=frame_id,
            presence_observation_id=presence_observation_id,
            observed_at=observed_at,
            activity_label=activity_label,
            confidence=confidence,
            model=model,
            raw_reason_json=dict(raw_reason_json or {}),
            created_at=created_at or datetime.now(UTC),
        )
        self.observations.append(observation)
        return observation

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> HumanActivityObservation | None:
        for observation in self.observations:
            if observation.frame_id == frame_id:
                return observation
        return None

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[HumanActivityObservation]:
        return sorted(
            self.observations,
            key=lambda observation: (
                observation.observed_at,
                observation.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )[:limit]


class InMemoryScreenActivityObservationStore:
    def __init__(self) -> None:
        self.observations: list[ScreenActivityObservation] = []

    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        screen_activity_label: str,
        confidence: float,
        model: str,
        app_hint: str | None = None,
        document_hint: str | None = None,
        url_hint: str | None = None,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ScreenActivityObservation:
        existing = await self.fetch_by_frame(frame_id)
        if existing is not None:
            return existing
        observation = ScreenActivityObservation(
            id=observation_id or uuid4(),
            frame_id=frame_id,
            observed_at=observed_at,
            screen_activity_label=screen_activity_label,
            app_hint=app_hint,
            document_hint=document_hint,
            url_hint=url_hint,
            confidence=confidence,
            model=model,
            raw_reason_json=dict(raw_reason_json or {}),
            created_at=created_at or datetime.now(UTC),
        )
        self.observations.append(observation)
        return observation

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> ScreenActivityObservation | None:
        for observation in self.observations:
            if observation.frame_id == frame_id:
                return observation
        return None

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[ScreenActivityObservation]:
        return sorted(
            self.observations,
            key=lambda observation: (
                observation.observed_at,
                observation.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )[:limit]


class InMemoryUserContextSnapshotStore:
    def __init__(self) -> None:
        self.snapshots: list[UserContextSnapshot] = []

    async def insert_snapshot(
        self,
        snapshot: UserContextSnapshot,
    ) -> UserContextSnapshot:
        saved = _with_snapshot_ids(snapshot)
        self.snapshots.append(saved)
        return saved

    async def fetch_latest(
        self,
        *,
        limit: int,
        device_id: str | None = None,
    ) -> list[UserContextSnapshot]:
        snapshots = [
            snapshot
            for snapshot in self.snapshots
            if device_id is None or snapshot.device_id == device_id
        ]
        return sorted(
            snapshots,
            key=lambda snapshot: (
                snapshot.computed_at,
                snapshot.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )[:limit]


class PostgresPerceptionFrameStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(PERCEPTION_FRAMES_SCHEMA_SQL)

    async def insert_frame(
        self,
        *,
        source: PerceptionFrameSource,
        file_path: str,
        sha256: str,
        captured_at: datetime,
        device_id: str | None = None,
        width: int | None = None,
        height: int | None = None,
        frame_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> PerceptionFrame:
        PerceptionFrame(
            id=frame_id,
            source=source,
            device_id=device_id,
            captured_at=captured_at,
            file_path=file_path,
            sha256=sha256,
            width=width,
            height=height,
            created_at=created_at,
        )
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO perception_frames (
                        id,
                        source,
                        device_id,
                        captured_at,
                        file_path,
                        sha256,
                        width,
                        height,
                        created_at
                    )
                    VALUES (
                        COALESCE(%s, gen_random_uuid()),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now())
                    )
                    RETURNING id, source, device_id, captured_at, file_path, sha256,
                              width, height, retained, created_at
                    """,
                    (
                        frame_id,
                        source,
                        device_id,
                        captured_at,
                        file_path,
                        sha256,
                        width,
                        height,
                        created_at,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("perception frame insert returned no row")
        return _frame_from_row(row)

    async def fetch_frame(self, frame_id: UUID) -> PerceptionFrame | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, source, device_id, captured_at, file_path, sha256,
                           width, height, retained, created_at
                    FROM perception_frames
                    WHERE id = %s
                    """,
                    (frame_id,),
                )
                row = await cur.fetchone()
        return _frame_from_row(row) if row is not None else None

    async def fetch_retained_frames(
        self,
        *,
        source: PerceptionFrameSource,
        limit: int,
    ) -> list[PerceptionFrame]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, source, device_id, captured_at, file_path, sha256,
                           width, height, retained, created_at
                    FROM perception_frames
                    WHERE source = %s
                      AND retained = true
                    ORDER BY captured_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (source, limit),
                )
                rows = await cur.fetchall()
        return [_frame_from_row(row) for row in rows]

    async def apply_retention(
        self,
        *,
        source: PerceptionFrameSource,
        keep_latest: int = 100,
    ) -> int:
        if keep_latest < 0:
            raise ValueError("keep_latest must be non-negative")
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            id,
                            row_number() OVER (
                                ORDER BY captured_at DESC, created_at DESC
                            ) AS rn
                        FROM perception_frames
                        WHERE source = %s
                          AND retained = true
                    ),
                    retired AS (
                        UPDATE perception_frames
                        SET retained = false
                        WHERE id IN (
                            SELECT id FROM ranked WHERE rn > %s
                        )
                        RETURNING id
                    )
                    SELECT count(*) FROM retired
                    """,
                    (source, keep_latest),
                )
                row = await cur.fetchone()
        return int(row[0]) if row is not None else 0


class PostgresHumanPresenceObservationStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(HUMAN_PRESENCE_OBSERVATIONS_SCHEMA_SQL)

    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        present: bool,
        confidence: float,
        model: str,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> HumanPresenceObservation:
        HumanPresenceObservation(
            id=observation_id,
            frame_id=frame_id,
            observed_at=observed_at,
            present=present,
            confidence=confidence,
            model=model,
            raw_reason_json=dict(raw_reason_json or {}),
            created_at=created_at,
        )
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO human_presence_observations (
                        id,
                        frame_id,
                        observed_at,
                        present,
                        confidence,
                        model,
                        raw_reason_json,
                        created_at
                    )
                    VALUES (
                        COALESCE(%s, gen_random_uuid()),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now())
                    )
                    ON CONFLICT (frame_id) DO UPDATE
                    SET frame_id = human_presence_observations.frame_id
                    RETURNING id, frame_id, observed_at, present, confidence, model,
                              raw_reason_json, created_at
                    """,
                    (
                        observation_id,
                        frame_id,
                        observed_at,
                        present,
                        confidence,
                        model,
                        Jsonb(raw_reason_json or {}),
                        created_at,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("human presence observation insert returned no row")
        return _presence_observation_from_row(row)

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> HumanPresenceObservation | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, frame_id, observed_at, present, confidence, model,
                           raw_reason_json, created_at
                    FROM human_presence_observations
                    WHERE frame_id = %s
                    """,
                    (frame_id,),
                )
                row = await cur.fetchone()
        return _presence_observation_from_row(row) if row is not None else None

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[HumanPresenceObservation]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, frame_id, observed_at, present, confidence, model,
                           raw_reason_json, created_at
                    FROM human_presence_observations
                    ORDER BY observed_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
        return [_presence_observation_from_row(row) for row in rows]


class PostgresHumanActivityObservationStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(HUMAN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL)

    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        activity_label: str,
        confidence: float,
        model: str,
        presence_observation_id: UUID | None = None,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> HumanActivityObservation:
        HumanActivityObservation(
            id=observation_id,
            frame_id=frame_id,
            presence_observation_id=presence_observation_id,
            observed_at=observed_at,
            activity_label=activity_label,
            confidence=confidence,
            model=model,
            raw_reason_json=dict(raw_reason_json or {}),
            created_at=created_at,
        )
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO human_activity_observations (
                        id,
                        frame_id,
                        presence_observation_id,
                        observed_at,
                        activity_label,
                        confidence,
                        model,
                        raw_reason_json,
                        created_at
                    )
                    VALUES (
                        COALESCE(%s, gen_random_uuid()),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now())
                    )
                    ON CONFLICT (frame_id) DO UPDATE
                    SET frame_id = human_activity_observations.frame_id
                    RETURNING id, frame_id, presence_observation_id, observed_at,
                              activity_label, confidence, model, raw_reason_json,
                              created_at
                    """,
                    (
                        observation_id,
                        frame_id,
                        presence_observation_id,
                        observed_at,
                        activity_label,
                        confidence,
                        model,
                        Jsonb(raw_reason_json or {}),
                        created_at,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("human activity observation insert returned no row")
        return _activity_observation_from_row(row)

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> HumanActivityObservation | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, frame_id, presence_observation_id, observed_at,
                           activity_label, confidence, model, raw_reason_json,
                           created_at
                    FROM human_activity_observations
                    WHERE frame_id = %s
                    """,
                    (frame_id,),
                )
                row = await cur.fetchone()
        return _activity_observation_from_row(row) if row is not None else None

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[HumanActivityObservation]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, frame_id, presence_observation_id, observed_at,
                           activity_label, confidence, model, raw_reason_json,
                           created_at
                    FROM human_activity_observations
                    ORDER BY observed_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
        return [_activity_observation_from_row(row) for row in rows]


class PostgresScreenActivityObservationStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(SCREEN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL)

    async def insert_observation(
        self,
        *,
        frame_id: UUID,
        observed_at: datetime,
        screen_activity_label: str,
        confidence: float,
        model: str,
        app_hint: str | None = None,
        document_hint: str | None = None,
        url_hint: str | None = None,
        raw_reason_json: dict[str, object] | None = None,
        observation_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ScreenActivityObservation:
        ScreenActivityObservation(
            id=observation_id,
            frame_id=frame_id,
            observed_at=observed_at,
            screen_activity_label=screen_activity_label,
            app_hint=app_hint,
            document_hint=document_hint,
            url_hint=url_hint,
            confidence=confidence,
            model=model,
            raw_reason_json=dict(raw_reason_json or {}),
            created_at=created_at,
        )
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO screen_activity_observations (
                        id,
                        frame_id,
                        observed_at,
                        screen_activity_label,
                        app_hint,
                        document_hint,
                        url_hint,
                        confidence,
                        model,
                        raw_reason_json,
                        created_at
                    )
                    VALUES (
                        COALESCE(%s, gen_random_uuid()),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now())
                    )
                    ON CONFLICT (frame_id) DO UPDATE
                    SET frame_id = screen_activity_observations.frame_id
                    RETURNING id, frame_id, observed_at, screen_activity_label,
                              app_hint, document_hint, url_hint, confidence, model,
                              raw_reason_json, created_at
                    """,
                    (
                        observation_id,
                        frame_id,
                        observed_at,
                        screen_activity_label,
                        app_hint,
                        document_hint,
                        url_hint,
                        confidence,
                        model,
                        Jsonb(raw_reason_json or {}),
                        created_at,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("screen activity observation insert returned no row")
        return _screen_observation_from_row(row)

    async def fetch_by_frame(
        self,
        frame_id: UUID,
    ) -> ScreenActivityObservation | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, frame_id, observed_at, screen_activity_label,
                           app_hint, document_hint, url_hint, confidence, model,
                           raw_reason_json, created_at
                    FROM screen_activity_observations
                    WHERE frame_id = %s
                    """,
                    (frame_id,),
                )
                row = await cur.fetchone()
        return _screen_observation_from_row(row) if row is not None else None

    async def fetch_latest(
        self,
        *,
        limit: int,
    ) -> list[ScreenActivityObservation]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, frame_id, observed_at, screen_activity_label,
                           app_hint, document_hint, url_hint, confidence, model,
                           raw_reason_json, created_at
                    FROM screen_activity_observations
                    ORDER BY observed_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
        return [_screen_observation_from_row(row) for row in rows]


class PostgresUserContextSnapshotStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(USER_CONTEXT_SNAPSHOTS_SCHEMA_SQL)

    async def insert_snapshot(
        self,
        snapshot: UserContextSnapshot,
    ) -> UserContextSnapshot:
        UserContextSnapshot(
            computed_at=snapshot.computed_at,
            device_id=snapshot.device_id,
            present=snapshot.present,
            presence_observed_at=snapshot.presence_observed_at,
            activity_label=snapshot.activity_label,
            activity_observed_at=snapshot.activity_observed_at,
            screen_activity_label=snapshot.screen_activity_label,
            screen_observed_at=snapshot.screen_observed_at,
            calendar_summary=snapshot.calendar_summary,
            world_summary=snapshot.world_summary,
            user_activity_summary=snapshot.user_activity_summary,
            context_summary=snapshot.context_summary,
            interaction_readiness=snapshot.interaction_readiness,
            confidence=snapshot.confidence,
            source_frame_ids=snapshot.source_frame_ids,
            source_observation_ids=snapshot.source_observation_ids,
            model=snapshot.model,
            raw_reason_json=snapshot.raw_reason_json,
            id=snapshot.id,
            created_at=snapshot.created_at,
        )
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO user_context_snapshots (
                        id,
                        computed_at,
                        device_id,
                        present,
                        presence_observed_at,
                        activity_label,
                        activity_observed_at,
                        screen_activity_label,
                        screen_observed_at,
                        calendar_summary,
                        world_summary,
                        user_activity_summary,
                        context_summary,
                        interaction_readiness,
                        confidence,
                        source_frame_ids,
                        source_observation_ids,
                        model,
                        raw_reason_json,
                        created_at
                    )
                    VALUES (
                        COALESCE(%s, gen_random_uuid()),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now())
                    )
                    RETURNING id, computed_at, device_id, present, presence_observed_at,
                              activity_label, activity_observed_at,
                              screen_activity_label, screen_observed_at,
                              calendar_summary, world_summary,
                              user_activity_summary, context_summary,
                              interaction_readiness, confidence, source_frame_ids,
                              source_observation_ids, model, raw_reason_json, created_at
                    """,
                    (
                        snapshot.id,
                        snapshot.computed_at,
                        snapshot.device_id,
                        snapshot.present,
                        snapshot.presence_observed_at,
                        snapshot.activity_label,
                        snapshot.activity_observed_at,
                        snapshot.screen_activity_label,
                        snapshot.screen_observed_at,
                        snapshot.calendar_summary,
                        snapshot.world_summary,
                        snapshot.user_activity_summary,
                        snapshot.context_summary,
                        snapshot.interaction_readiness,
                        snapshot.confidence,
                        list(snapshot.source_frame_ids),
                        list(snapshot.source_observation_ids),
                        snapshot.model,
                        Jsonb(snapshot.raw_reason_json),
                        snapshot.created_at,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("user context snapshot insert returned no row")
        return _user_context_snapshot_from_row(row)

    async def fetch_latest(
        self,
        *,
        limit: int,
        device_id: str | None = None,
    ) -> list[UserContextSnapshot]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, computed_at, device_id, present, presence_observed_at,
                           activity_label, activity_observed_at,
                           screen_activity_label, screen_observed_at,
                           calendar_summary, world_summary,
                           user_activity_summary, context_summary,
                           interaction_readiness, confidence, source_frame_ids,
                           source_observation_ids, model, raw_reason_json, created_at
                    FROM user_context_snapshots
                    WHERE (%s::text IS NULL OR device_id = %s)
                    ORDER BY computed_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (device_id, device_id, limit),
                )
                rows = await cur.fetchall()
        return [_user_context_snapshot_from_row(row) for row in rows]


def _latest_first(frames: list[PerceptionFrame]) -> list[PerceptionFrame]:
    return sorted(
        frames,
        key=lambda frame: (
            frame.captured_at,
            frame.created_at or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )


def _replace_retained(
    frame: PerceptionFrame,
    *,
    retained: bool,
) -> PerceptionFrame:
    return PerceptionFrame(
        id=frame.id,
        source=frame.source,
        device_id=frame.device_id,
        captured_at=frame.captured_at,
        file_path=frame.file_path,
        sha256=frame.sha256,
        width=frame.width,
        height=frame.height,
        retained=retained,
        created_at=frame.created_at,
    )


def _frame_from_row(row: tuple[object, ...]) -> PerceptionFrame:
    return PerceptionFrame(
        id=row[0],  # type: ignore[arg-type]
        source=row[1],  # type: ignore[arg-type]
        device_id=str(row[2]) if row[2] is not None else None,
        captured_at=row[3],  # type: ignore[arg-type]
        file_path=str(row[4]),
        sha256=str(row[5]),
        width=int(row[6]) if row[6] is not None else None,
        height=int(row[7]) if row[7] is not None else None,
        retained=bool(row[8]),
        created_at=row[9],  # type: ignore[arg-type]
    )


def _presence_observation_from_row(row: tuple[object, ...]) -> HumanPresenceObservation:
    return HumanPresenceObservation(
        id=row[0],  # type: ignore[arg-type]
        frame_id=row[1],  # type: ignore[arg-type]
        observed_at=row[2],  # type: ignore[arg-type]
        present=bool(row[3]),
        confidence=float(row[4]),
        model=str(row[5]),
        raw_reason_json=dict(row[6] or {}),
        created_at=row[7],  # type: ignore[arg-type]
    )


def _activity_observation_from_row(row: tuple[object, ...]) -> HumanActivityObservation:
    return HumanActivityObservation(
        id=row[0],  # type: ignore[arg-type]
        frame_id=row[1],  # type: ignore[arg-type]
        presence_observation_id=row[2],  # type: ignore[arg-type]
        observed_at=row[3],  # type: ignore[arg-type]
        activity_label=str(row[4]),
        confidence=float(row[5]),
        model=str(row[6]),
        raw_reason_json=dict(row[7] or {}),
        created_at=row[8],  # type: ignore[arg-type]
    )


def _screen_observation_from_row(row: tuple[object, ...]) -> ScreenActivityObservation:
    return ScreenActivityObservation(
        id=row[0],  # type: ignore[arg-type]
        frame_id=row[1],  # type: ignore[arg-type]
        observed_at=row[2],  # type: ignore[arg-type]
        screen_activity_label=str(row[3]),
        app_hint=str(row[4]) if row[4] is not None else None,
        document_hint=str(row[5]) if row[5] is not None else None,
        url_hint=str(row[6]) if row[6] is not None else None,
        confidence=float(row[7]),
        model=str(row[8]),
        raw_reason_json=dict(row[9] or {}),
        created_at=row[10],  # type: ignore[arg-type]
    )


def _with_snapshot_ids(snapshot: UserContextSnapshot) -> UserContextSnapshot:
    return UserContextSnapshot(
        id=snapshot.id or uuid4(),
        computed_at=snapshot.computed_at,
        device_id=snapshot.device_id,
        present=snapshot.present,
        presence_observed_at=snapshot.presence_observed_at,
        activity_label=snapshot.activity_label,
        activity_observed_at=snapshot.activity_observed_at,
        screen_activity_label=snapshot.screen_activity_label,
        screen_observed_at=snapshot.screen_observed_at,
        calendar_summary=snapshot.calendar_summary,
        world_summary=snapshot.world_summary,
        user_activity_summary=snapshot.user_activity_summary,
        context_summary=snapshot.context_summary,
        interaction_readiness=snapshot.interaction_readiness,
        confidence=snapshot.confidence,
        source_frame_ids=snapshot.source_frame_ids,
        source_observation_ids=snapshot.source_observation_ids,
        model=snapshot.model,
        raw_reason_json=snapshot.raw_reason_json,
        created_at=snapshot.created_at or datetime.now(UTC),
    )


def _user_context_snapshot_from_row(row: tuple[object, ...]) -> UserContextSnapshot:
    return UserContextSnapshot(
        id=row[0],  # type: ignore[arg-type]
        computed_at=row[1],  # type: ignore[arg-type]
        device_id=str(row[2]) if row[2] is not None else None,
        present=bool(row[3]) if row[3] is not None else None,
        presence_observed_at=row[4],  # type: ignore[arg-type]
        activity_label=str(row[5]) if row[5] is not None else None,
        activity_observed_at=row[6],  # type: ignore[arg-type]
        screen_activity_label=str(row[7]) if row[7] is not None else None,
        screen_observed_at=row[8],  # type: ignore[arg-type]
        calendar_summary=str(row[9]) if row[9] is not None else None,
        world_summary=str(row[10]) if row[10] is not None else None,
        user_activity_summary=str(row[11]),
        context_summary=str(row[12]),
        interaction_readiness=row[13],  # type: ignore[arg-type]
        confidence=float(row[14]),
        source_frame_ids=tuple(row[15] or ()),  # type: ignore[arg-type]
        source_observation_ids=tuple(row[16] or ()),  # type: ignore[arg-type]
        model=str(row[17]) if row[17] is not None else None,
        raw_reason_json=dict(row[18] or {}),
        created_at=row[19],  # type: ignore[arg-type]
    )
