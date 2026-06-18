from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from server.shared.candidate import CandidateStore, UtteranceCandidate
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import TTSInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PregenerationResult:
    scanned_count: int
    pregenerated_count: int
    error_count: int
    elapsed_ms: float


class UtterancePregenerator:
    def __init__(
        self,
        *,
        store: CandidateStore,
        tts_backend: TTSBackend,
        priority_threshold: float = 0.8,
        fetch_limit: int = 20,
    ) -> None:
        self.store = store
        self.tts_backend = tts_backend
        self.priority_threshold = priority_threshold
        self.fetch_limit = fetch_limit

    async def pregenerate_once(self, *, now: datetime | None = None) -> PregenerationResult:
        observed_at = now or datetime.now(UTC)
        started_at = time.perf_counter()
        candidates = await self.store.fetch_active_utterance_candidates(
            now=observed_at,
            limit=self.fetch_limit,
        )
        error_count = 0
        pregenerated_count = 0
        for candidate in candidates:
            if not _should_pregenerate(candidate, threshold=self.priority_threshold):
                continue
            try:
                audio = await self._first_audio_chunk(candidate)
                if audio is None:
                    continue
                await self.store.mark_utterance_pregenerated(
                    candidate.id,
                    generated_audio=audio,
                )
                pregenerated_count += 1
            except Exception as exc:
                error_count += 1
                logger.warning(
                    "utterance pregeneration failed candidate_id=%s reason=%s",
                    candidate.id,
                    exc,
                )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "utterance pregeneration scanned_count=%s pregenerated_count=%s "
            "error_count=%s elapsed_ms=%.1f",
            len(candidates),
            pregenerated_count,
            error_count,
            elapsed_ms,
        )
        return PregenerationResult(
            scanned_count=len(candidates),
            pregenerated_count=pregenerated_count,
            error_count=error_count,
            elapsed_ms=elapsed_ms,
        )

    async def _first_audio_chunk(self, candidate: UtteranceCandidate) -> bytes | None:
        assert candidate.generated_text is not None
        async for chunk in self.tts_backend.synthesize(
            TTSInput(text=candidate.generated_text, style="neutral")
        ):
            return chunk.data
        return None


def _should_pregenerate(
    candidate: UtteranceCandidate,
    *,
    threshold: float,
) -> bool:
    return (
        candidate.priority >= threshold
        and candidate.maturity == 1
        and candidate.generated_text is not None
        and candidate.generated_audio is None
    )
