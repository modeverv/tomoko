from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from server.shared.models import PerceptionFrame
from server.shared.perception import (
    HumanActivityObservationStore,
    HumanPresenceObservationStore,
    PerceptionFrameStore,
)
from server.thinker.perception.presence import ModelLoader, PresenceStreamGenerator


@dataclass(frozen=True)
class HumanActivityInferenceResult:
    activity_label: str
    confidence: float
    model: str
    raw_reason_json: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.activity_label:
            raise ValueError("HumanActivityInferenceResult.activity_label must not be empty")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("HumanActivityInferenceResult.confidence must be 0.0-1.0")
        if not self.model:
            raise ValueError("HumanActivityInferenceResult.model must not be empty")


class HumanActivityInferenceBackend(Protocol):
    model: str

    async def infer_activity(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> HumanActivityInferenceResult: ...


@dataclass(frozen=True)
class HumanActivityInferenceProcessResult:
    processed_frame_id: UUID | None
    skipped_stale_count: int = 0
    skipped_backlog_count: int = 0
    already_processed_count: int = 0


class HumanActivityInferenceWorker:
    def __init__(
        self,
        *,
        frame_store: PerceptionFrameStore,
        activity_store: HumanActivityObservationStore,
        presence_store: HumanPresenceObservationStore | None,
        backend: HumanActivityInferenceBackend,
        stale_after: timedelta = timedelta(minutes=5),
        backlog_limit: int = 10,
    ) -> None:
        self.frame_store = frame_store
        self.activity_store = activity_store
        self.presence_store = presence_store
        self.backend = backend
        self.stale_after = stale_after
        self.backlog_limit = backlog_limit

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> HumanActivityInferenceProcessResult:
        observed_at = now or datetime.now(UTC)
        frames = await self.frame_store.fetch_retained_frames(
            source="camera",
            limit=self.backlog_limit,
        )
        pending: list[PerceptionFrame] = []
        skipped_stale_count = 0
        already_processed_count = 0
        for frame in frames:
            if observed_at - frame.captured_at > self.stale_after:
                skipped_stale_count += 1
                continue
            if frame.id is None:
                continue
            if await self.activity_store.fetch_by_frame(frame.id) is not None:
                already_processed_count += 1
                continue
            pending.append(frame)

        if not pending:
            return HumanActivityInferenceProcessResult(
                processed_frame_id=None,
                skipped_stale_count=skipped_stale_count,
                already_processed_count=already_processed_count,
            )

        frame = pending[0]
        assert frame.id is not None
        result = await self.backend.infer_activity(
            frame.file_path,
            frame_id=frame.id,
        )
        presence_observation_id = None
        if self.presence_store is not None:
            presence = await self.presence_store.fetch_by_frame(frame.id)
            presence_observation_id = presence.id if presence is not None else None
        await self.activity_store.insert_observation(
            frame_id=frame.id,
            presence_observation_id=presence_observation_id,
            observed_at=observed_at,
            activity_label=result.activity_label,
            confidence=result.confidence,
            model=result.model,
            raw_reason_json=result.raw_reason_json,
        )
        return HumanActivityInferenceProcessResult(
            processed_frame_id=frame.id,
            skipped_stale_count=skipped_stale_count,
            skipped_backlog_count=max(0, len(pending) - 1),
            already_processed_count=already_processed_count,
        )


class MlxVlmActivityBackend:
    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 64,
        model_loader: ModelLoader | None = None,
        stream_generator: PresenceStreamGenerator | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._model_loader = model_loader or _load_mlx_vlm_model
        self._stream_generator = stream_generator or _stream_mlx_vlm
        self._loaded_model: Any | None = None
        self._processor: Any | None = None

    async def infer_activity(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> HumanActivityInferenceResult:
        del frame_id
        model, processor = self._load()
        output = _collect_stream_text(
            self._stream_generator(
                model,
                processor,
                _ACTIVITY_PROMPT,
                frame_path,
                self.max_tokens,
            )
        )
        return parse_activity_inference_json(
            _parse_json_object(output),
            model=self.model,
        )

    def _load(self) -> tuple[Any, Any]:
        if self._loaded_model is None or self._processor is None:
            self._loaded_model, self._processor = self._model_loader(self.model)
        return self._loaded_model, self._processor


def parse_activity_inference_json(
    payload: dict[str, object],
    *,
    model: str,
) -> HumanActivityInferenceResult:
    activity_label = payload.get("activity_label")
    if not isinstance(activity_label, str) or not activity_label.strip():
        raise ValueError("activity inference JSON requires non-empty activity_label")
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        raise ValueError("activity inference JSON requires numeric confidence")
    raw_reason_json = {
        key: value
        for key, value in payload.items()
        if key not in {"activity_label", "confidence"}
    }
    return HumanActivityInferenceResult(
        activity_label=activity_label.strip(),
        confidence=float(confidence),
        model=model,
        raw_reason_json=raw_reason_json,
    )


def coherent_activity_label(
    *,
    present: bool | None,
    activity_label: str | None,
) -> str | None:
    if present is False:
        return "away"
    return activity_label


_ACTIVITY_PROMPT = """画像の人間が何をしているかを一言で判定してください。
出力は次の JSON object だけにしてください:
{
  "activity_label": "typing | reading | idle | away | unknown など短いラベル",
  "confidence": 0.0-1.0,
  "reason": "短い理由"
}
"""


def _load_mlx_vlm_model(model_name: str) -> tuple[Any, Any]:
    from mlx_vlm import load

    return load(model_name)


def _stream_mlx_vlm(
    model: Any,
    processor: Any,
    prompt: str,
    image: str,
    max_tokens: int,
) -> Any:
    from mlx_vlm import stream_generate

    return stream_generate(
        model,
        processor,
        prompt,
        image=image,
        max_tokens=max_tokens,
        temperature=0.0,
    )


def _collect_stream_text(stream: Any) -> str:
    if isinstance(stream, str):
        return stream
    parts: list[str] = []
    for chunk in stream:
        if isinstance(chunk, str):
            parts.append(chunk)
            continue
        text = getattr(chunk, "text", "")
        if text:
            parts.append(str(text))
    return "".join(parts)


def _parse_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("activity inference output was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("activity inference output must be a JSON object")
    return dict(payload)
