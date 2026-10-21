from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

APPEND_DEDUPE_LABELS = ("duplicate", "continuation", "new_intent")
APPEND_DEDUPE_EXTRA_FEATURES = (
    "previous_length",
    "current_length",
    "length_ratio",
    "raw_similarity",
    "normalized_similarity",
    "current_contains_previous",
    "previous_contains_current",
    "time_close",
    "time_medium",
    "tomoko_speaking",
    "speech_queue_active",
    "current_is_final",
    "continuation_cue",
    "new_intent_cue",
    "correction_cue",
    "previous_vague",
    "topic_overlap",
)
FILLER_PREFIXES = ("えーっと", "えっと", "あの", "まあ", "なんか", "うん", "その")
CONTINUATION_CUES = (
    "もう少し",
    "具体的",
    "というのは",
    "つまり",
    "補足",
    "設定ファイル",
    "言い換えると",
)
NEW_INTENT_CUES = (
    "ところで",
    "別件",
    "音量",
    "予定",
    "テレビ",
    "ライト",
    "タイマー",
    "話変わる",
)
CORRECTION_CUES = ("いや", "違う", "じゃなくて", "ではなく", "訂正", "やっぱり")
VAGUE_CUES = ("わかってない", "分かってない", "わからない", "分からない", "曖昧", "微妙")
PUNCTUATION = " \t\r\n。、，,.!！?？「」『』()（）[]【】"


@dataclass(frozen=True, slots=True)
class AppendDedupeConfig:
    hash_size: int = 2048
    ngram_min: int = 1
    ngram_max: int = 4
    ridge_lambda: float = 0.1
    heuristic_weight: float = 0.35


@dataclass(frozen=True, slots=True)
class AppendDedupeInput:
    previous_user_text: str
    current_user_text: str
    time_delta_ms: int = 0
    tomoko_speaking: bool = False
    speech_queue_active: bool = False
    current_is_final: bool = True


@dataclass(frozen=True, slots=True)
class AppendDedupeExample:
    previous_user_text: str
    current_user_text: str
    label: str
    time_delta_ms: int = 0
    tomoko_speaking: bool = False
    speech_queue_active: bool = False
    current_is_final: bool = True
    source: str = "public_synthetic_append_dedupe_anchor"
    example_id: str = ""

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> AppendDedupeExample:
        return cls(
            previous_user_text=str(payload["previous_user_text"]),
            current_user_text=str(payload["current_user_text"]),
            label=_validate_label(str(payload["label"])),
            time_delta_ms=int(payload.get("time_delta_ms", 0)),
            tomoko_speaking=bool(payload.get("tomoko_speaking", False)),
            speech_queue_active=bool(payload.get("speech_queue_active", False)),
            current_is_final=bool(payload.get("current_is_final", True)),
            source=str(payload.get("source", "public_synthetic_append_dedupe_anchor")),
            example_id=str(payload.get("example_id", "")),
        )

    def to_input(self) -> AppendDedupeInput:
        return AppendDedupeInput(
            previous_user_text=self.previous_user_text,
            current_user_text=self.current_user_text,
            time_delta_ms=self.time_delta_ms,
            tomoko_speaking=self.tomoko_speaking,
            speech_queue_active=self.speech_queue_active,
            current_is_final=self.current_is_final,
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AppendDedupeResult:
    duplicate_score: float
    continuation_score: float
    new_intent_score: float
    label: str
    features: dict[str, float | str | bool]


@dataclass(slots=True)
class HashRidgeAppendDedupeModel:
    config: AppendDedupeConfig
    weights: dict[str, list[float]]
    bias: dict[str, float]
    metadata: dict[str, Any]

    def predict(self, sample: AppendDedupeInput) -> AppendDedupeResult:
        features = append_dedupe_features(sample, self.config)
        debug = append_dedupe_debug_features(sample)
        heuristic = heuristic_append_dedupe_scores(sample, debug)
        weight = max(0.0, min(1.0, self.config.heuristic_weight))
        scores: dict[str, float] = {}
        for label in APPEND_DEDUPE_LABELS:
            label_weights = np.array(self.weights[label], dtype=np.float64)
            aligned_features = align_append_dedupe_features(features, label_weights)
            raw_score = float(np.dot(label_weights, aligned_features) + self.bias[label])
            raw_score = max(0.0, min(1.0, raw_score))
            scores[label] = (1.0 - weight) * raw_score + weight * heuristic[label]
        scores = _apply_safety_adjustments(scores, debug)
        label = max(APPEND_DEDUPE_LABELS, key=lambda key: scores[key])
        return AppendDedupeResult(
            duplicate_score=scores["duplicate"],
            continuation_score=scores["continuation"],
            new_intent_score=scores["new_intent"],
            label=label,
            features=debug,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_type": "hash_ridge_append_dedupe",
            "labels": list(APPEND_DEDUPE_LABELS),
            "config": asdict(self.config),
            "extra_features": list(APPEND_DEDUPE_EXTRA_FEATURES),
            "weights": self.weights,
            "bias": self.bias,
            "metadata": self.metadata,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")

    @classmethod
    def load(cls, path: Path) -> HashRidgeAppendDedupeModel:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("model_type") != "hash_ridge_append_dedupe":
            raise ValueError("unsupported model_type")
        if tuple(payload.get("labels") or ()) != APPEND_DEDUPE_LABELS:
            raise ValueError("unsupported labels")
        config = AppendDedupeConfig(**payload["config"])
        return cls(
            config=config,
            weights={
                label: [float(value) for value in payload["weights"][label]]
                for label in APPEND_DEDUPE_LABELS
            },
            bias={label: float(payload["bias"][label]) for label in APPEND_DEDUPE_LABELS},
            metadata=dict(payload.get("metadata") or {}),
        )


def fit_append_dedupe_model(
    examples: list[AppendDedupeExample],
    config: AppendDedupeConfig,
    *,
    metadata: dict[str, Any] | None = None,
) -> HashRidgeAppendDedupeModel:
    if not examples:
        raise ValueError("at least one append dedupe example is required")
    for example in examples:
        _validate_label(example.label)
    matrix = np.vstack([append_dedupe_features(example.to_input(), config) for example in examples])
    ones = np.ones((matrix.shape[0], 1), dtype=np.float64)
    design = np.hstack([matrix, ones])
    regularizer = config.ridge_lambda * np.eye(design.shape[1], dtype=np.float64)
    regularizer[-1, -1] = 0.0
    lhs = design.T @ design + regularizer
    weights: dict[str, list[float]] = {}
    bias: dict[str, float] = {}
    for label in APPEND_DEDUPE_LABELS:
        y = np.array([1.0 if example.label == label else 0.0 for example in examples])
        rhs = design.T @ y
        try:
            solution = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            solution = np.linalg.pinv(lhs) @ rhs
        weights[label] = [float(value) for value in solution[:-1]]
        bias[label] = float(solution[-1])
    model = HashRidgeAppendDedupeModel(
        config=config,
        weights=weights,
        bias=bias,
        metadata=metadata or {},
    )
    model.metadata["train_count"] = len(examples)
    model.metadata["label_counts"] = {
        label: sum(1 for example in examples if example.label == label)
        for label in APPEND_DEDUPE_LABELS
    }
    return model


def append_dedupe_features(sample: AppendDedupeInput, config: AppendDedupeConfig) -> np.ndarray:
    if config.hash_size < 8:
        raise ValueError("hash_size must be >= 8")
    if config.ngram_min < 1 or config.ngram_max < config.ngram_min:
        raise ValueError("invalid ngram range")
    feature_count = config.hash_size + len(APPEND_DEDUPE_EXTRA_FEATURES)
    features = np.zeros(feature_count, dtype=np.float64)
    previous = normalize_for_append_dedupe(sample.previous_user_text)
    current = normalize_for_append_dedupe(sample.current_user_text)
    raw_previous = compact_text(sample.previous_user_text)
    raw_current = compact_text(sample.current_user_text)
    hashed_text = f"prev={previous}|cur={current}|raw_prev={raw_previous}|raw_cur={raw_current}"
    for ngram in _char_ngrams(hashed_text, config.ngram_min, config.ngram_max):
        index, sign = _hash_index_and_sign(ngram, config.hash_size)
        features[index] += sign
    norm = np.linalg.norm(features[: config.hash_size])
    if norm > 0:
        features[: config.hash_size] /= norm
    debug = append_dedupe_debug_features(sample)
    offset = config.hash_size
    for index, name in enumerate(APPEND_DEDUPE_EXTRA_FEATURES):
        features[offset + index] = float(debug[name])
    return features


def append_dedupe_debug_features(sample: AppendDedupeInput) -> dict[str, float | str | bool]:
    previous = compact_text(sample.previous_user_text)
    current = compact_text(sample.current_user_text)
    normalized_previous = normalize_for_append_dedupe(sample.previous_user_text)
    normalized_current = normalize_for_append_dedupe(sample.current_user_text)
    previous_terms = set(_char_ngrams(normalized_previous, 2, 2))
    current_terms = set(_char_ngrams(normalized_current, 2, 2))
    topic_overlap = (
        len(previous_terms & current_terms) / max(1, len(previous_terms | current_terms))
        if previous_terms or current_terms
        else 0.0
    )
    raw_similarity = _similarity(previous, current)
    normalized_similarity = _similarity(normalized_previous, normalized_current)
    length_ratio = min(len(normalized_previous), len(normalized_current)) / max(
        1,
        max(len(normalized_previous), len(normalized_current)),
    )
    return {
        "previous_normalized": normalized_previous,
        "current_normalized": normalized_current,
        "previous_length": min(len(normalized_previous), 80) / 80.0,
        "current_length": min(len(normalized_current), 80) / 80.0,
        "length_ratio": length_ratio,
        "raw_similarity": raw_similarity,
        "normalized_similarity": normalized_similarity,
        "current_contains_previous": (
            1.0
            if normalized_previous and normalized_previous in normalized_current
            else 0.0
        ),
        "previous_contains_current": (
            1.0
            if normalized_current and normalized_current in normalized_previous
            else 0.0
        ),
        "time_close": 1.0 if 0 <= sample.time_delta_ms <= 1500 else 0.0,
        "time_medium": 1.0 if 1500 < sample.time_delta_ms <= 5000 else 0.0,
        "tomoko_speaking": sample.tomoko_speaking,
        "speech_queue_active": sample.speech_queue_active,
        "current_is_final": sample.current_is_final,
        "continuation_cue": 1.0 if _contains_any(normalized_current, CONTINUATION_CUES) else 0.0,
        "new_intent_cue": 1.0 if _contains_any(normalized_current, NEW_INTENT_CUES) else 0.0,
        "correction_cue": 1.0 if _contains_any(normalized_current, CORRECTION_CUES) else 0.0,
        "previous_vague": 1.0 if _contains_any(normalized_previous, VAGUE_CUES) else 0.0,
        "topic_overlap": topic_overlap,
    }


def heuristic_append_dedupe_scores(
    sample: AppendDedupeInput,
    debug: dict[str, float | str | bool] | None = None,
) -> dict[str, float]:
    debug = debug or append_dedupe_debug_features(sample)
    similarity = float(debug["normalized_similarity"])
    contains = max(
        float(debug["current_contains_previous"]),
        float(debug["previous_contains_current"]),
    )
    time_bonus = (
        0.08 if float(debug["time_close"]) else 0.03 if float(debug["time_medium"]) else 0.0
    )
    active_bonus = 0.05 if sample.tomoko_speaking or sample.speech_queue_active else 0.0
    duplicate = min(0.98, similarity * 0.78 + contains * 0.16 + time_bonus + active_bonus)
    continuation = (
        0.16
        + 0.42 * float(debug["continuation_cue"])
        + 0.16 * float(debug["previous_vague"])
        + 0.12 * (1.0 - float(debug["topic_overlap"]))
    )
    new_intent = (
        0.12
        + 0.48 * float(debug["new_intent_cue"])
        + 0.42 * float(debug["correction_cue"])
        + 0.18 * (1.0 - float(debug["topic_overlap"]))
    )
    if float(debug["correction_cue"]) > 0.0:
        duplicate *= 0.35
    if float(debug["continuation_cue"]) > 0.0 and float(debug["previous_vague"]) > 0.0:
        duplicate *= 0.5
    return {
        "duplicate": max(0.0, min(1.0, duplicate)),
        "continuation": max(0.0, min(1.0, continuation)),
        "new_intent": max(0.0, min(1.0, new_intent)),
    }


def evaluate_append_dedupe_model(
    model: HashRidgeAppendDedupeModel,
    examples: list[AppendDedupeExample],
) -> dict[str, float]:
    if not examples:
        return {"count": 0.0, "accuracy": 0.0}
    correct = 0
    label_total = {label: 0 for label in APPEND_DEDUPE_LABELS}
    label_correct = {label: 0 for label in APPEND_DEDUPE_LABELS}
    confusion = {
        (expected, predicted): 0
        for expected in APPEND_DEDUPE_LABELS
        for predicted in APPEND_DEDUPE_LABELS
    }
    for example in examples:
        result = model.predict(example.to_input())
        label_total[example.label] += 1
        confusion[(example.label, result.label)] += 1
        if result.label == example.label:
            correct += 1
            label_correct[example.label] += 1
    metrics: dict[str, float] = {
        "count": float(len(examples)),
        "accuracy": correct / len(examples),
    }
    for label in APPEND_DEDUPE_LABELS:
        total = label_total[label]
        metrics[f"label_count.{label}"] = float(total)
        metrics[f"label_accuracy.{label}"] = (
            label_correct[label] / total if total else 0.0
        )
    for (expected, predicted), count in confusion.items():
        metrics[f"confusion.{expected}.{predicted}"] = float(count)
    return metrics


def normalize_for_append_dedupe(text: str) -> str:
    normalized = compact_text(text)
    changed = True
    while changed:
        changed = False
        for filler in FILLER_PREFIXES:
            if normalized.startswith(filler) and len(normalized) > len(filler) + 1:
                normalized = normalized[len(filler) :]
                changed = True
    return normalized


def compact_text(text: str) -> str:
    return "".join(char for char in text.strip(PUNCTUATION) if char not in PUNCTUATION)


def align_append_dedupe_features(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
    if features.shape[0] == weights.shape[0]:
        return features
    if features.shape[0] > weights.shape[0]:
        return features[: weights.shape[0]]
    return np.pad(features, (0, weights.shape[0] - features.shape[0]))


def _apply_safety_adjustments(
    scores: dict[str, float],
    debug: dict[str, float | str | bool],
) -> dict[str, float]:
    adjusted = {label: max(0.0, min(1.0, score)) for label, score in scores.items()}
    if debug["previous_normalized"] == debug["current_normalized"] and debug["current_normalized"]:
        adjusted["duplicate"] = max(adjusted["duplicate"], 0.92)
        adjusted["new_intent"] = min(adjusted["new_intent"], 0.18)
    if float(debug["correction_cue"]) > 0.0:
        adjusted["duplicate"] = min(adjusted["duplicate"], 0.42)
        adjusted["new_intent"] = max(adjusted["new_intent"], adjusted["duplicate"] + 0.04)
    if float(debug["continuation_cue"]) > 0.0 and float(debug["previous_vague"]) > 0.0:
        adjusted["duplicate"] = min(adjusted["duplicate"], 0.44)
        adjusted["continuation"] = max(adjusted["continuation"], 0.58)
    return {label: max(0.0, min(1.0, adjusted[label])) for label in APPEND_DEDUPE_LABELS}


def _validate_label(label: str) -> str:
    if label not in APPEND_DEDUPE_LABELS:
        raise ValueError(f"unsupported append dedupe label: {label}")
    return label


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


def _similarity(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


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
