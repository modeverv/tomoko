from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from server.world_observations.operator_client import (
    WorldObservationOperatorRequest,
    WorldObservationOperatorResult,
    build_daily_world_observation_request,
    save_world_observation_markdown,
)


@dataclass(frozen=True)
class WorldTopicSeed:
    topic: str
    prompt_hint: str


DEFAULT_WORLD_TOPIC_SEEDS: tuple[WorldTopicSeed, ...] = (
    WorldTopicSeed("news", "世界と日本の主要ニュース。日付が新しいものを優先する。"),
    WorldTopicSeed("economy", "生活費、仕事、開発者の購買判断に関係しそうな動き。"),
    WorldTopicSeed("technology", "開発者が気にしそうな技術ニュースとOSS動向。"),
    WorldTopicSeed("culture", "本、音楽、映像、ネット文化の軽い話題。"),
    WorldTopicSeed("local_life", "日常生活、季節、地域生活につながる話題。"),
    WorldTopicSeed("ai", "AIサービス、研究、規制、モデル公開。"),
    WorldTopicSeed("local_inference", "Apple Silicon、MLX、音声モデル、ローカル推論。"),
)


class WorldObservationClient(Protocol):
    async def observe(
        self,
        request: WorldObservationOperatorRequest,
    ) -> WorldObservationOperatorResult: ...


@dataclass(frozen=True)
class WorldInformationCollectionResult:
    ok: bool
    status: str
    output_path: Path | None = None
    error_reason: str | None = None


class WorldInformationCollectionWorker:
    def __init__(
        self,
        *,
        client: WorldObservationClient,
        prompt_template: str,
        output_dir: Path | str,
        topic_seeds: tuple[WorldTopicSeed, ...] = DEFAULT_WORLD_TOPIC_SEEDS,
    ) -> None:
        self.client = client
        self.prompt_template = prompt_template
        self.output_dir = Path(output_dir)
        self.topic_seeds = topic_seeds

    async def collect_once(
        self,
        *,
        collection_date: str,
        observed_at: str | None = None,
    ) -> WorldInformationCollectionResult:
        request = build_daily_world_observation_request(
            prompt_template=build_seeded_world_prompt_template(
                self.prompt_template,
                topic_seeds=self.topic_seeds,
            ),
            collection_date=collection_date,
            observed_at=observed_at,
        )
        result = await self.client.observe(request)
        if not result.is_completed():
            return WorldInformationCollectionResult(
                ok=False,
                status=result.status,
                error_reason=result.error_reason,
            )
        try:
            output_path = save_world_observation_markdown(
                result,
                output_dir=self.output_dir,
                collection_date=collection_date,
            )
        except ValueError as exc:
            return WorldInformationCollectionResult(
                ok=False,
                status="failed",
                error_reason=str(exc),
            )
        return WorldInformationCollectionResult(
            ok=True,
            status=result.status,
            output_path=output_path,
        )


def build_seeded_world_prompt_template(
    prompt_template: str,
    *,
    topic_seeds: tuple[WorldTopicSeed, ...] = DEFAULT_WORLD_TOPIC_SEEDS,
) -> str:
    lines = [
        "",
        "## deterministic thinker2 topic seeds",
        "",
        "以下の seed は thinker2 の定常観測用です。",
        "private page、ログイン必須ページ、個人情報、秘密情報には依存しないでください。",
    ]
    lines.extend(f"- {seed.topic}: {seed.prompt_hint}" for seed in topic_seeds)
    return prompt_template.rstrip() + "\n" + "\n".join(lines) + "\n"
