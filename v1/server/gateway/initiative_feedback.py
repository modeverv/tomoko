from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

import psycopg

from server.shared.candidate import UtteranceCandidate
from server.shared.models import (
    CandidateFeedbackScope,
    CandidateFeedbackSummary,
    CandidateSpeakMetadata,
    Transcript,
)

FeedbackKind = Literal["rejection", "acceptance", "defer"]


@dataclass(frozen=True)
class CandidateFeedbackSignal:
    scope: CandidateFeedbackScope
    kind: FeedbackKind
    score: float
    observed_at: datetime
    transcript_text: str | None = None


class CandidateFeedbackStore(Protocol):
    async def record(self, signal: CandidateFeedbackSignal) -> None: ...

    async def summarize(
        self,
        scope: CandidateFeedbackScope,
        *,
        now: datetime,
    ) -> CandidateFeedbackSummary: ...


class InMemoryCandidateFeedbackStore:
    def __init__(self) -> None:
        self.signals: list[CandidateFeedbackSignal] = []

    async def record(self, signal: CandidateFeedbackSignal) -> None:
        self.signals.append(signal)

    async def summarize(
        self,
        scope: CandidateFeedbackScope,
        *,
        now: datetime,
    ) -> CandidateFeedbackSummary:
        return summarize_feedback_signals(self.signals, scope=scope, now=now)


class PostgresCandidateFeedbackStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def record(self, signal: CandidateFeedbackSignal) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO initiative_feedback_signals (
                        observed_at,
                        candidate_id,
                        source,
                        topic,
                        emotional_need,
                        feedback_kind,
                        score,
                        transcript_text
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        signal.observed_at,
                        signal.scope.candidate_id,
                        signal.scope.source,
                        signal.scope.topic,
                        signal.scope.emotional_need,
                        signal.kind,
                        signal.score,
                        signal.transcript_text,
                    ),
                )

    async def summarize(
        self,
        scope: CandidateFeedbackScope,
        *,
        now: datetime,
    ) -> CandidateFeedbackSummary:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT observed_at, candidate_id, source, topic, emotional_need,
                           feedback_kind, score, transcript_text
                    FROM initiative_feedback_signals
                    WHERE observed_at >= %s
                      AND (
                        source = %s
                        OR topic = %s
                        OR emotional_need = %s
                      )
                    ORDER BY observed_at DESC
                    LIMIT 100
                    """,
                    (
                        now - timedelta(days=7),
                        scope.source,
                        scope.topic,
                        scope.emotional_need,
                    ),
                )
                rows = await cur.fetchall()
        signals = [
            CandidateFeedbackSignal(
                scope=CandidateFeedbackScope(
                    candidate_id=row[1],
                    source=str(row[2]),
                    topic=row[3],
                    emotional_need=row[4],
                ),
                kind=row[5],
                score=float(row[6]),
                observed_at=row[0],
                transcript_text=row[7],
            )
            for row in rows
        ]
        return summarize_feedback_signals(signals, scope=scope, now=now)


def feedback_scope_from_metadata(
    metadata: CandidateSpeakMetadata,
) -> CandidateFeedbackScope:
    return CandidateFeedbackScope(
        candidate_id=metadata.candidate_id,
        source=metadata.source,
        topic=_topic_from_tags(metadata.context_tags),
        emotional_need=_emotional_need_bucket(metadata.emotional_need),
    )


def feedback_scope_from_candidate(
    candidate: UtteranceCandidate,
) -> CandidateFeedbackScope:
    return CandidateFeedbackScope(
        candidate_id=candidate.id,
        source=candidate.source,
        topic=_topic_from_tags(candidate.context_tags),
        emotional_need=_emotional_need_bucket(
            _tag_float(candidate.context_tags, "emotional_need")
        ),
    )


def apply_feedback_to_metadata(
    metadata: CandidateSpeakMetadata,
    summary: CandidateFeedbackSummary,
) -> CandidateSpeakMetadata:
    return CandidateSpeakMetadata(
        candidate_id=metadata.candidate_id,
        source=metadata.source,
        priority=metadata.priority,
        urgency=metadata.urgency,
        intrusion_risk=metadata.intrusion_risk,
        emotional_need=metadata.emotional_need,
        feedback_penalty=summary.feedback_penalty,
        feedback_boost=summary.feedback_boost,
        maturity=metadata.maturity,
        text_ready=metadata.text_ready,
        audio_ready=metadata.audio_ready,
        expires_at=metadata.expires_at,
        context_tags=metadata.context_tags,
        reason=metadata.reason,
    )


def classify_feedback(
    transcript: Transcript,
    scope: CandidateFeedbackScope,
    *,
    observed_at: datetime | None = None,
) -> CandidateFeedbackSignal | None:
    text = transcript.text.strip()
    if not text:
        return None
    if _looks_like_rejection(text):
        return CandidateFeedbackSignal(
            scope=scope,
            kind="rejection",
            score=1.0,
            observed_at=observed_at or transcript.recorded_at,
            transcript_text=transcript.text,
        )
    if _looks_like_defer(text):
        return CandidateFeedbackSignal(
            scope=scope,
            kind="defer",
            score=0.75,
            observed_at=observed_at or transcript.recorded_at,
            transcript_text=transcript.text,
        )
    if _looks_like_acceptance(text):
        return CandidateFeedbackSignal(
            scope=scope,
            kind="acceptance",
            score=0.7,
            observed_at=observed_at or transcript.recorded_at,
            transcript_text=transcript.text,
        )
    return None


def summarize_feedback_signals(
    signals: list[CandidateFeedbackSignal],
    *,
    scope: CandidateFeedbackScope,
    now: datetime,
) -> CandidateFeedbackSummary:
    rejection = 0.0
    acceptance = 0.0
    for signal in signals:
        if not _scope_matches(signal.scope, scope):
            continue
        age_sec = max(0.0, (now - signal.observed_at).total_seconds())
        weight = _time_weight(age_sec)
        weighted_score = _clamp(signal.score * weight * _scope_weight(signal.scope, scope))
        if signal.kind == "acceptance":
            acceptance = max(acceptance, weighted_score)
        else:
            rejection = max(rejection, weighted_score)
    penalty = _clamp(rejection * 0.8)
    boost = _clamp(acceptance * 0.45)
    return CandidateFeedbackSummary(
        rejection_score=rejection,
        acceptance_score=acceptance,
        intrusion_penalty=penalty,
        feedback_penalty=penalty,
        feedback_boost=boost,
    )


def _scope_matches(left: CandidateFeedbackScope, right: CandidateFeedbackScope) -> bool:
    return (
        left.source == right.source
        or (left.topic is not None and left.topic == right.topic)
        or (
            left.emotional_need is not None
            and left.emotional_need == right.emotional_need
        )
    )


def _scope_weight(left: CandidateFeedbackScope, right: CandidateFeedbackScope) -> float:
    weight = 0.0
    if left.source == right.source:
        weight = max(weight, 0.7)
    if left.topic is not None and left.topic == right.topic:
        weight = max(weight, 1.0)
    if left.emotional_need is not None and left.emotional_need == right.emotional_need:
        weight = max(weight, 0.5)
    return weight


def _time_weight(age_sec: float) -> float:
    if age_sec <= 3600:
        return 1.0
    if age_sec >= 7 * 24 * 3600:
        return 0.0
    return max(0.0, 1.0 - (age_sec - 3600) / (7 * 24 * 3600 - 3600))


def _topic_from_tags(tags: tuple[str, ...]) -> str | None:
    for tag in tags:
        if tag.startswith("topic:"):
            return tag.removeprefix("topic:") or None
    return None


def _tag_float(tags: tuple[str, ...], prefix: str) -> float:
    needle = f"{prefix}:"
    for tag in tags:
        if tag.startswith(needle):
            try:
                return _clamp(float(tag.removeprefix(needle)))
            except ValueError:
                return 0.0
    return 0.0


def _emotional_need_bucket(value: float) -> str:
    if value >= 0.66:
        return "high"
    if value >= 0.33:
        return "medium"
    return "low"


def _looks_like_rejection(text: str) -> bool:
    phrases = (
        "静かにして",
        "今いい",
        "今はいい",
        "いまいい",
        "黙って",
        "話しかけないで",
        "邪魔しないで",
    )
    return any(phrase in text for phrase in phrases)


def _looks_like_defer(text: str) -> bool:
    phrases = ("あとで", "後で", "今じゃない", "それ今じゃない", "あとにして")
    return any(phrase in text for phrase in phrases)


def _looks_like_acceptance(text: str) -> bool:
    phrases = ("うん、なに", "なに？", "どうした", "聞くよ", "言って", "話して")
    return any(phrase in text for phrase in phrases)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
