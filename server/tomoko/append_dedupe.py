from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from server.shared.models import AppendDedupeDecision

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APPEND_DEDUPE_MODEL_PATH = (
    REPO_ROOT
    / "make-model"
    / "artifacts"
    / "public-synthetic-append-dedupe-h2048-l005-model.json"
)


class AppendDedupeGuard(Protocol):
    def inspect(
        self,
        *,
        previous_user_text: str,
        current_user_text: str,
        time_delta_ms: int,
        tomoko_speaking: bool,
        speech_queue_active: bool,
        current_is_final: bool,
    ) -> AppendDedupeDecision: ...


@dataclass(frozen=True, slots=True)
class AppendDedupeGuardConfig:
    duplicate_suppress_threshold: float = 0.85
    max_continuation_score: float = 0.45
    max_new_intent_score: float = 0.45
    max_time_delta_ms: int = 5000


@dataclass(slots=True)
class HashRidgeAppendDedupeGuard:
    model_path: Path | None = None
    model: Any | None = None
    config: AppendDedupeGuardConfig = AppendDedupeGuardConfig()
    source: str = "append_dedupe_hash_ridge"

    def __post_init__(self) -> None:
        if self.model is None:
            if self.model_path is None:
                raise ValueError("model_path is required when model is not provided")
            self.model = _load_append_dedupe_model(self.model_path)

    def inspect(
        self,
        *,
        previous_user_text: str,
        current_user_text: str,
        time_delta_ms: int,
        tomoko_speaking: bool,
        speech_queue_active: bool,
        current_is_final: bool,
    ) -> AppendDedupeDecision:
        if self.model is None:
            raise RuntimeError("append dedupe model is not loaded")
        sample_cls = _append_dedupe_input_class()
        result = self.model.predict(
            sample_cls(
                previous_user_text=previous_user_text,
                current_user_text=current_user_text,
                time_delta_ms=time_delta_ms,
                tomoko_speaking=tomoko_speaking,
                speech_queue_active=speech_queue_active,
                current_is_final=current_is_final,
            )
        )
        should_suppress = (
            result.label == "duplicate"
            and result.duplicate_score >= self.config.duplicate_suppress_threshold
            and result.continuation_score <= self.config.max_continuation_score
            and result.new_intent_score <= self.config.max_new_intent_score
            and 0 <= time_delta_ms <= self.config.max_time_delta_ms
        )
        reason = (
            "append dedupe duplicate score crossed suppress threshold"
            if should_suppress
            else "append dedupe pass"
        )
        return AppendDedupeDecision(
            previous_user_text=previous_user_text,
            current_user_text=current_user_text,
            time_delta_ms=time_delta_ms,
            duplicate_score=float(result.duplicate_score),
            continuation_score=float(result.continuation_score),
            new_intent_score=float(result.new_intent_score),
            label=str(result.label),
            should_suppress=should_suppress,
            reason=reason,
            source=self.source,
        )


def create_default_append_dedupe_guard() -> AppendDedupeGuard | None:
    if os.environ.get("TOMOKO_V2_APPEND_DEDUPE", "1") != "1":
        return None
    model_path = Path(
        os.environ.get(
            "TOMOKO_V2_APPEND_DEDUPE_MODEL",
            str(DEFAULT_APPEND_DEDUPE_MODEL_PATH),
        )
    )
    try:
        return HashRidgeAppendDedupeGuard(
            model_path=model_path,
            config=AppendDedupeGuardConfig(
                duplicate_suppress_threshold=float(
                    os.environ.get("TOMOKO_V2_APPEND_DEDUPE_DUPLICATE_THRESHOLD", "0.85")
                ),
                max_continuation_score=float(
                    os.environ.get("TOMOKO_V2_APPEND_DEDUPE_MAX_CONTINUATION", "0.45")
                ),
                max_new_intent_score=float(
                    os.environ.get("TOMOKO_V2_APPEND_DEDUPE_MAX_NEW_INTENT", "0.45")
                ),
                max_time_delta_ms=int(
                    os.environ.get("TOMOKO_V2_APPEND_DEDUPE_MAX_DELTA_MS", "5000")
                ),
            ),
        )
    except FileNotFoundError:
        return None


def decision_score_breakdown(decision: AppendDedupeDecision) -> dict[str, float]:
    return {
        "append_dedupe_duplicate_score": decision.duplicate_score,
        "append_dedupe_continuation_score": decision.continuation_score,
        "append_dedupe_new_intent_score": decision.new_intent_score,
        "append_dedupe_should_suppress": 1.0 if decision.should_suppress else 0.0,
    }


def _load_append_dedupe_model(model_path: Path) -> Any:
    make_model_dir = REPO_ROOT / "make-model"
    path_text = str(make_model_dir)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    from make_model.append_dedupe import HashRidgeAppendDedupeModel

    return HashRidgeAppendDedupeModel.load(model_path)


def _append_dedupe_input_class() -> Any:
    make_model_dir = REPO_ROOT / "make-model"
    path_text = str(make_model_dir)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    from make_model.append_dedupe import AppendDedupeInput

    return AppendDedupeInput
