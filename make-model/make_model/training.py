from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from make_model.model import (
    HashRidgeConfig,
    HashRidgeSaturationModel,
    evaluate_model,
    fit_hash_ridge_model,
)
from make_model.schema import TeacherLabel


@dataclass(frozen=True, slots=True)
class TrainConfig:
    hash_size: int = 2048
    ngram_min: int = 1
    ngram_max: int = 4
    ridge_lambda: float = 1.0

    def to_model_config(self) -> HashRidgeConfig:
        return HashRidgeConfig(
            hash_size=self.hash_size,
            ngram_min=self.ngram_min,
            ngram_max=self.ngram_max,
            ridge_lambda=self.ridge_lambda,
        )


def train_hash_ridge_model(
    labels: list[TeacherLabel],
    config: TrainConfig,
    *,
    artifact_path: Path,
) -> tuple[HashRidgeSaturationModel, dict[str, float]]:
    model_config = config.to_model_config()
    model = fit_hash_ridge_model(
        labels,
        model_config,
        metadata={
            "train_count": len(labels),
            "train_config": asdict(config),
            "teacher_models": sorted({label.teacher_model for label in labels}),
        },
    )
    metrics = evaluate_model(model, labels)
    metrics["train_count"] = float(len(labels))
    model.metadata["train_metrics"] = metrics
    model.save(artifact_path)
    return model, metrics
