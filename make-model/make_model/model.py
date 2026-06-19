from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from make_model.schema import TeacherLabel

EXTRA_FEATURES = ("length", "question_tail", "is_final", "lowering_prefix")
LOWERING_PREFIXES = ("ただ", "でも", "いや", "というか", "一個だけ", "ひとつだけ")


@dataclass(frozen=True, slots=True)
class HashRidgeConfig:
    hash_size: int = 2048
    ngram_min: int = 1
    ngram_max: int = 4
    ridge_lambda: float = 1.0


@dataclass(slots=True)
class HashRidgeSaturationModel:
    config: HashRidgeConfig
    weights: list[float]
    bias: float
    metadata: dict[str, Any]

    def predict(self, text: str, *, is_final: bool = False) -> float:
        features = hashed_features(text, self.config, is_final=is_final)
        score = float(np.dot(np.array(self.weights, dtype=np.float64), features) + self.bias)
        return max(0.0, min(1.0, score))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_type": "hash_ridge_saturation",
            "config": asdict(self.config),
            "weights": self.weights,
            "bias": self.bias,
            "metadata": self.metadata,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")

    @classmethod
    def load(cls, path: Path) -> HashRidgeSaturationModel:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("model_type") != "hash_ridge_saturation":
            raise ValueError("unsupported model_type")
        config = HashRidgeConfig(**payload["config"])
        return cls(
            config=config,
            weights=[float(value) for value in payload["weights"]],
            bias=float(payload["bias"]),
            metadata=dict(payload.get("metadata") or {}),
        )


def fit_hash_ridge_model(
    labels: list[TeacherLabel],
    config: HashRidgeConfig,
    *,
    metadata: dict[str, Any] | None = None,
) -> HashRidgeSaturationModel:
    if not labels:
        raise ValueError("at least one teacher label is required")
    matrix = np.vstack(
        [hashed_features(label.prefix_text, config, is_final=label.is_final) for label in labels]
    )
    y = np.array([max(0.0, min(1.0, label.saturation)) for label in labels], dtype=np.float64)
    ones = np.ones((matrix.shape[0], 1), dtype=np.float64)
    design = np.hstack([matrix, ones])
    regularizer = config.ridge_lambda * np.eye(design.shape[1], dtype=np.float64)
    regularizer[-1, -1] = 0.0
    lhs = design.T @ design + regularizer
    rhs = design.T @ y
    try:
        solution = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        solution = np.linalg.pinv(lhs) @ rhs
    weights = [float(value) for value in solution[:-1]]
    bias = float(solution[-1])
    return HashRidgeSaturationModel(
        config=config,
        weights=weights,
        bias=bias,
        metadata=metadata or {},
    )


def hashed_features(text: str, config: HashRidgeConfig, *, is_final: bool = False) -> np.ndarray:
    if config.hash_size < 8:
        raise ValueError("hash_size must be >= 8")
    if config.ngram_min < 1 or config.ngram_max < config.ngram_min:
        raise ValueError("invalid ngram range")
    feature_count = config.hash_size + len(EXTRA_FEATURES)
    features = np.zeros(feature_count, dtype=np.float64)
    normalized = "".join(text.split())
    if normalized:
        for ngram in _char_ngrams(normalized, config.ngram_min, config.ngram_max):
            index, sign = _hash_index_and_sign(ngram, config.hash_size)
            features[index] += sign
        norm = np.linalg.norm(features[: config.hash_size])
        if norm > 0:
            features[: config.hash_size] /= norm
    offset = config.hash_size
    features[offset] = min(len(normalized), 80) / 80.0
    features[offset + 1] = 1.0 if normalized.endswith(("?", "？", "か")) else 0.0
    features[offset + 2] = 1.0 if is_final else 0.0
    features[offset + 3] = 1.0 if normalized.startswith(LOWERING_PREFIXES) else 0.0
    return features


def evaluate_model(
    model: HashRidgeSaturationModel,
    labels: list[TeacherLabel],
    *,
    threshold: float = 0.75,
) -> dict[str, float]:
    if not labels:
        return {"count": 0.0, "mae": 0.0, "rmse": 0.0, "binary_accuracy": 0.0}
    errors: list[float] = []
    binary_hits = 0
    for label in labels:
        predicted = model.predict(label.prefix_text, is_final=label.is_final)
        error = predicted - label.saturation
        errors.append(error)
        if (predicted >= threshold) == (label.saturation >= threshold):
            binary_hits += 1
    mae = sum(abs(error) for error in errors) / len(errors)
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    return {
        "count": float(len(labels)),
        "mae": float(mae),
        "rmse": float(rmse),
        "binary_accuracy": float(binary_hits / len(labels)),
    }


def _char_ngrams(text: str, ngram_min: int, ngram_max: int) -> list[str]:
    ngrams: list[str] = []
    for size in range(ngram_min, ngram_max + 1):
        if len(text) < size:
            continue
        ngrams.extend(text[index : index + size] for index in range(0, len(text) - size + 1))
    return ngrams


def _hash_index_and_sign(text: str, hash_size: int) -> tuple[int, float]:
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:4], "little") % hash_size
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign
