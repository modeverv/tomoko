from __future__ import annotations

from datetime import timedelta

from server.shared.candidate import CandidateSeed, ThinkerSourceContext
from server.world_observations.store import WorldObservationStore

_SOURCE_PREFIX = "world_observation"


class WorldObservationSource:
    def __init__(
        self,
        *,
        store: WorldObservationStore,
        priority: float = 0.62,
        ttl: timedelta = timedelta(hours=6),
        limit: int = 5,
    ) -> None:
        self.store = store
        self.priority = priority
        self.ttl = ttl
        self.limit = limit

    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        interpretations = await self.store.fetch_candidate_interpretations(
            limit=self.limit,
            min_confidence=0.45,
            min_interest=0.45,
        )
        seeds: list[CandidateSeed] = []
        for interpretation in interpretations:
            score = max(interpretation.tomoko_interest, interpretation.relevance_to_user)
            seeds.append(
                CandidateSeed(
                    seed_text=_seed_text(
                        interpretation.candidate_seed_text
                        or interpretation.tomoko_private_reaction
                        or interpretation.interpretation_text
                    ),
                    source=f"{_SOURCE_PREFIX}:{interpretation.id}",
                    priority=min(1.0, max(self.priority, score)),
                    urgent=interpretation.freshness in {"breaking", "fresh"}
                    and score >= 0.75,
                    expires_at=context.observed_at + self.ttl,
                    dedupe_key=f"{_SOURCE_PREFIX}:{interpretation.id}",
                    context_tags=(
                        f"topic:{interpretation.topic}",
                        f"freshness:{interpretation.freshness}",
                        f"world_observation_document:{interpretation.document_id}",
                        f"world_observation_item:{interpretation.item_id}",
                        f"world_observation_interpretation:{interpretation.id}",
                    ),
                    metadata_json={
                        "schema_version": 1,
                        "world_observation": {
                            "document_id": str(interpretation.document_id),
                            "item_id": str(interpretation.item_id),
                            "interpretation_id": str(interpretation.id),
                            "topic": interpretation.topic,
                            "freshness": interpretation.freshness,
                            "speakability_hint": interpretation.speakability_hint,
                            "tomoko_private_reaction": (
                                interpretation.tomoko_private_reaction
                            ),
                            "candidate_seed_text": interpretation.candidate_seed_text,
                            "reason": interpretation.reason_json,
                        },
                    },
                )
            )
        return seeds


def _seed_text(interpretation_text: str) -> str:
    text = " ".join(
        line.strip() for line in interpretation_text.splitlines() if line.strip()
    )
    if len(text) > 120:
        text = f"{text[:117]}..."
    return f"外部観測から、押しつけず短く話題候補にする: {text}"
