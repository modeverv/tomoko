from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from server.shared.candidate import CandidateSeed, ThinkerSourceContext

_SOURCE_NAME = "time_based"


@dataclass(frozen=True)
class TimeBucket:
    name: str
    seed_text: str
    priority: float
    ttl: timedelta


_BUCKETS = {
    "late_night": TimeBucket(
        name="late_night",
        seed_text="深夜なので、必要なら静かに一言だけ声をかける",
        priority=0.25,
        ttl=timedelta(hours=3),
    ),
    "morning": TimeBucket(
        name="morning",
        seed_text="朝の始まりに、軽く調子を聞く",
        priority=0.4,
        ttl=timedelta(hours=3),
    ),
    "daytime": TimeBucket(
        name="daytime",
        seed_text="昼の流れを邪魔しない程度に、今の様子を聞く",
        priority=0.3,
        ttl=timedelta(hours=4),
    ),
    "evening": TimeBucket(
        name="evening",
        seed_text="夜になったので、今日の疲れ具合をそっと聞く",
        priority=0.35,
        ttl=timedelta(hours=4),
    ),
}


class TimeBasedSource:
    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        bucket = _bucket_for(context.observed_at)
        date_key = context.observed_at.date().isoformat()
        dedupe_key = f"{_SOURCE_NAME}:{bucket.name}:{date_key}"
        return [
            CandidateSeed(
                seed_text=bucket.seed_text,
                source=_SOURCE_NAME,
                priority=bucket.priority,
                expires_at=context.observed_at + bucket.ttl,
                dedupe_key=dedupe_key,
                context_tags=(f"time_of_day:{bucket.name}",),
            )
        ]


def _bucket_for(observed_at: datetime) -> TimeBucket:
    hour = observed_at.hour
    if 0 <= hour < 5:
        return _BUCKETS["late_night"]
    if 5 <= hour < 11:
        return _BUCKETS["morning"]
    if 11 <= hour < 18:
        return _BUCKETS["daytime"]
    return _BUCKETS["evening"]

