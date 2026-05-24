from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from server.shared.candidate import (
    CandidateSeed,
    CandidateStore,
    ThinkerEvaluationContext,
    ThinkerSourceContext,
    UtteranceCandidate,
)
from server.shared.config import NodeConfig
from server.shared.inference.router import InferenceRouter
from server.thinker.arrival import ArrivalPrecomputer
from server.thinker.evaluator.base import UtteranceEvaluator
from server.thinker.evaluator.llm import LLMUtteranceEvaluator
from server.thinker.sources.base import InformationSource
from server.thinker.sources.time_based import TimeBasedSource

logger = logging.getLogger(__name__)

NowFactory = Callable[[], datetime]
SleepFunc = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class ThinkerLoopConfig:
    device_id: str | None = None
    candidate_interval_sec: float = 60.0
    arrival_interval_sec: float = 180.0


@dataclass(frozen=True)
class CandidateGenerationResult:
    generated_seed_count: int
    inserted_seed_count: int
    kept_candidate_count: int
    dismissed_expired_count: int
    elapsed_ms: float
    error_count: int = 0


@dataclass(frozen=True)
class ArrivalPrecomputeResult:
    behavior: str
    elapsed_ms: float
    error_count: int = 0


@dataclass(frozen=True)
class ThinkerRunOnceResult:
    candidate: CandidateGenerationResult
    arrival: ArrivalPrecomputeResult | None


class ThinkerProcess:
    def __init__(
        self,
        *,
        store: CandidateStore,
        sources: Sequence[InformationSource],
        evaluator: UtteranceEvaluator,
        arrival_precomputer: ArrivalPrecomputer | None = None,
        config: ThinkerLoopConfig | None = None,
    ) -> None:
        self.store = store
        self.sources = tuple(sources)
        self.evaluator = evaluator
        self.arrival_precomputer = arrival_precomputer
        self.config = config or ThinkerLoopConfig()

    async def run_once(self, *, now: datetime | None = None) -> ThinkerRunOnceResult:
        observed_at = now or datetime.now(UTC)
        candidate = await self.run_candidate_generation_once(now=observed_at)
        arrival = None
        if self.arrival_precomputer is not None:
            arrival = await self.run_arrival_precompute_once(now=observed_at)
        return ThinkerRunOnceResult(candidate=candidate, arrival=arrival)

    async def run_candidate_generation_once(
        self,
        *,
        now: datetime | None = None,
    ) -> CandidateGenerationResult:
        observed_at = now or datetime.now(UTC)
        started_at = time.perf_counter()
        error_count = 0
        generated: list[CandidateSeed] = []
        inserted_seed_count = 0
        kept_candidate_count = 0

        try:
            dismissed_expired_count = await self.store.mark_expired_utterance_candidates(
                observed_at
            )
        except Exception as exc:
            dismissed_expired_count = 0
            error_count += 1
            logger.info(
                "thinker candidate expired dismissal failed reason=%s",
                type(exc).__name__,
            )

        source_context = ThinkerSourceContext(
            observed_at=observed_at,
            device_id=self.config.device_id,
        )
        for source in self.sources:
            try:
                generated.extend(await source.collect(source_context))
            except Exception as exc:
                error_count += 1
                logger.info(
                    "thinker source failed source=%s reason=%s",
                    type(source).__name__,
                    type(exc).__name__,
                )

        eval_context = ThinkerEvaluationContext(
            observed_at=observed_at,
            device_id=self.config.device_id,
        )
        for seed in generated:
            try:
                inserted_seed = await self._insert_seed(seed, observed_at)
            except Exception as exc:
                error_count += 1
                logger.info(
                    "thinker seed candidate save failed source=%s reason=%s",
                    seed.source,
                    type(exc).__name__,
                )
                continue
            if inserted_seed is None:
                continue
            inserted_seed_count += 1

            try:
                evaluated = await self.evaluator.evaluate(seed, eval_context)
            except Exception as exc:
                error_count += 1
                logger.info(
                    "thinker evaluator failed source=%s reason=%s",
                    seed.source,
                    type(exc).__name__,
                )
                continue

            try:
                saved = await self.store.insert_evaluated_utterance_once(
                    seed,
                    evaluated,
                    created_at=observed_at,
                )
            except Exception as exc:
                error_count += 1
                logger.info(
                    "thinker evaluated candidate save failed source=%s reason=%s",
                    seed.source,
                    type(exc).__name__,
                )
                continue
            if saved is not None:
                kept_candidate_count += 1

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        result = CandidateGenerationResult(
            generated_seed_count=len(generated),
            inserted_seed_count=inserted_seed_count,
            kept_candidate_count=kept_candidate_count,
            dismissed_expired_count=dismissed_expired_count,
            elapsed_ms=elapsed_ms,
            error_count=error_count,
        )
        logger.info(
            "thinker candidate_generation generated_seed_count=%s "
            "inserted_seed_count=%s kept_candidate_count=%s "
            "dismissed_expired_count=%s elapsed_ms=%.1f error_count=%s",
            result.generated_seed_count,
            result.inserted_seed_count,
            result.kept_candidate_count,
            result.dismissed_expired_count,
            result.elapsed_ms,
            result.error_count,
        )
        return result

    async def run_arrival_precompute_once(
        self,
        *,
        now: datetime | None = None,
    ) -> ArrivalPrecomputeResult:
        if self.arrival_precomputer is None:
            raise RuntimeError("arrival_precomputer is not configured")
        observed_at = now or datetime.now(UTC)
        started_at = time.perf_counter()
        error_count = 0
        try:
            candidate = await self.arrival_precomputer.precompute_once(
                now=observed_at,
                device_id=self.config.device_id,
            )
            behavior = candidate.behavior
        except Exception as exc:
            error_count = 1
            behavior = "error"
            logger.info(
                "thinker arrival_precompute failed reason=%s",
                type(exc).__name__,
            )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        result = ArrivalPrecomputeResult(
            behavior=behavior,
            elapsed_ms=elapsed_ms,
            error_count=error_count,
        )
        logger.info(
            "thinker arrival_precompute behavior=%s elapsed_ms=%.1f error_count=%s",
            result.behavior,
            result.elapsed_ms,
            result.error_count,
        )
        return result

    async def _insert_seed(
        self,
        seed: CandidateSeed,
        observed_at: datetime,
    ) -> UtteranceCandidate | None:
        return await self.store.insert_seed_candidate_once(
            seed,
            created_at=observed_at,
        )


async def candidate_generation_loop(
    thinker: ThinkerProcess,
    *,
    sleep: SleepFunc = asyncio.sleep,
    now_factory: NowFactory = lambda: datetime.now(UTC),
) -> None:
    while True:
        await thinker.run_candidate_generation_once(now=now_factory())
        await sleep(thinker.config.candidate_interval_sec)


async def arrival_precompute_loop(
    thinker: ThinkerProcess,
    *,
    sleep: SleepFunc = asyncio.sleep,
    now_factory: NowFactory = lambda: datetime.now(UTC),
) -> None:
    while True:
        await thinker.run_arrival_precompute_once(now=now_factory())
        await sleep(thinker.config.arrival_interval_sec)


async def run_watch(thinker: ThinkerProcess) -> None:
    tasks = [candidate_generation_loop(thinker)]
    if thinker.arrival_precomputer is not None:
        tasks.append(arrival_precompute_loop(thinker))
    await asyncio.gather(*tasks)


def build_default_thinker(config: NodeConfig) -> ThinkerProcess:
    from server.shared.candidate import PostgresCandidateStore

    store = PostgresCandidateStore(config.database.dsn)
    router = InferenceRouter(config=config)
    return ThinkerProcess(
        store=store,
        sources=[TimeBasedSource()],
        evaluator=LLMUtteranceEvaluator(router=router),
        arrival_precomputer=ArrivalPrecomputer(store=store, router=router),
        config=ThinkerLoopConfig(device_id=config.node.device_id),
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Tomoko thinker process.")
    parser.add_argument(
        "--config",
        default="config/central_realtime.toml",
        help="Path to TOML config.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--candidate-interval-sec", type=float, default=60.0)
    parser.add_argument("--arrival-interval-sec", type=float, default=180.0)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    config = NodeConfig.load(args.config)
    thinker = build_default_thinker(config)
    thinker.config = ThinkerLoopConfig(
        device_id=config.node.device_id,
        candidate_interval_sec=args.candidate_interval_sec,
        arrival_interval_sec=args.arrival_interval_sec,
    )

    if args.watch:
        await run_watch(thinker)
        return 0

    result = await thinker.run_once()
    print(
        "candidate_generated="
        f"{result.candidate.generated_seed_count} "
        f"candidate_inserted={result.candidate.inserted_seed_count} "
        f"candidate_kept={result.candidate.kept_candidate_count} "
        f"arrival_behavior={result.arrival.behavior if result.arrival else 'none'}"
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
