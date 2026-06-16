from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from server.shared.models import PerceptionFrame
from server.shared.perception import (
    HumanPresenceObservationStore,
    PerceptionFrameStore,
)

SleepFunc = Callable[[float], Awaitable[None]]
ModelLoader = Callable[[str], tuple[Any, Any]]
PresenceStreamGenerator = Callable[[Any, Any, str, str, int], Any]


@dataclass(frozen=True)
class CapturedFrameArtifact:
    file_path: str
    sha256: str
    captured_at: datetime
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not self.file_path:
            raise ValueError("CapturedFrameArtifact.file_path must not be empty")
        if not self.sha256:
            raise ValueError("CapturedFrameArtifact.sha256 must not be empty")


class CameraCaptureProvider(Protocol):
    async def capture(self, *, captured_at: datetime) -> CapturedFrameArtifact: ...


@dataclass(frozen=True)
class HumanPresenceInferenceResult:
    present: bool
    confidence: float
    model: str
    raw_reason_json: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("HumanPresenceInferenceResult.confidence must be 0.0-1.0")
        if not self.model:
            raise ValueError("HumanPresenceInferenceResult.model must not be empty")


class HumanPresenceInferenceBackend(Protocol):
    model: str

    async def infer_presence(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> HumanPresenceInferenceResult: ...


@dataclass(frozen=True)
class PresenceInferenceProcessResult:
    processed_frame_id: UUID | None
    skipped_stale_count: int = 0
    skipped_backlog_count: int = 0
    already_processed_count: int = 0


class CameraCaptureWorker:
    def __init__(
        self,
        *,
        frame_store: PerceptionFrameStore,
        provider: CameraCaptureProvider,
        device_id: str | None = None,
        retention_limit: int = 100,
    ) -> None:
        self.frame_store = frame_store
        self.provider = provider
        self.device_id = device_id
        self.retention_limit = retention_limit

    async def capture_once(self, *, now: datetime | None = None) -> PerceptionFrame:
        captured_at = now or datetime.now(UTC)
        artifact = await self.provider.capture(captured_at=captured_at)
        frame = await self.frame_store.insert_frame(
            source="camera",
            device_id=self.device_id,
            file_path=artifact.file_path,
            sha256=artifact.sha256,
            captured_at=artifact.captured_at,
            width=artifact.width,
            height=artifact.height,
        )
        await self.frame_store.apply_retention(
            source="camera",
            keep_latest=self.retention_limit,
        )
        return frame

    async def capture_loop(
        self,
        *,
        interval_sec: float = 30.0,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        while True:
            await self.capture_once()
            await sleep(interval_sec)


class PresenceInferenceWorker:
    def __init__(
        self,
        *,
        frame_store: PerceptionFrameStore,
        observation_store: HumanPresenceObservationStore,
        backend: HumanPresenceInferenceBackend,
        stale_after: timedelta = timedelta(minutes=5),
        backlog_limit: int = 10,
    ) -> None:
        self.frame_store = frame_store
        self.observation_store = observation_store
        self.backend = backend
        self.stale_after = stale_after
        self.backlog_limit = backlog_limit

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PresenceInferenceProcessResult:
        observed_at = now or datetime.now(UTC)
        frames = await self.frame_store.fetch_retained_frames(
            source="camera",
            limit=self.backlog_limit,
        )
        skipped_stale_count = 0
        already_processed_count = 0
        pending: list[PerceptionFrame] = []
        for frame in frames:
            if observed_at - frame.captured_at > self.stale_after:
                skipped_stale_count += 1
                continue
            if frame.id is None:
                continue
            if await self.observation_store.fetch_by_frame(frame.id) is not None:
                already_processed_count += 1
                continue
            pending.append(frame)

        if not pending:
            return PresenceInferenceProcessResult(
                processed_frame_id=None,
                skipped_stale_count=skipped_stale_count,
                already_processed_count=already_processed_count,
            )

        frame = pending[0]
        assert frame.id is not None
        result = await self.backend.infer_presence(
            frame.file_path,
            frame_id=frame.id,
        )
        await self.observation_store.insert_observation(
            frame_id=frame.id,
            observed_at=observed_at,
            present=result.present,
            confidence=result.confidence,
            model=result.model,
            raw_reason_json=result.raw_reason_json,
        )
        return PresenceInferenceProcessResult(
            processed_frame_id=frame.id,
            skipped_stale_count=skipped_stale_count,
            skipped_backlog_count=max(0, len(pending) - 1),
            already_processed_count=already_processed_count,
        )


class MlxVlmPresenceBackend:
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

    async def infer_presence(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> HumanPresenceInferenceResult:
        del frame_id
        model, processor = self._load()
        output = _collect_stream_text(
            self._stream_generator(
                model,
                processor,
                _PRESENCE_PROMPT,
                frame_path,
                self.max_tokens,
            )
        )
        return parse_presence_inference_json(
            _parse_json_object(output),
            model=self.model,
        )

    def _load(self) -> tuple[Any, Any]:
        if self._loaded_model is None or self._processor is None:
            self._loaded_model, self._processor = self._model_loader(self.model)
        return self._loaded_model, self._processor


def parse_presence_inference_json(
    payload: dict[str, object],
    *,
    model: str,
) -> HumanPresenceInferenceResult:
    if not isinstance(payload.get("present"), bool):
        raise ValueError("presence inference JSON requires boolean present")
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        raise ValueError("presence inference JSON requires numeric confidence")
    raw_reason_json = {
        key: value
        for key, value in payload.items()
        if key not in {"present", "confidence"}
    }
    return HumanPresenceInferenceResult(
        present=bool(payload["present"]),
        confidence=float(confidence),
        model=model,
        raw_reason_json=raw_reason_json,
    )


_PRESENCE_PROMPT = """画像に人間が写っているかだけを判定してください。
出力は次の JSON object だけにしてください:
{"present": true | false, "confidence": 0.0-1.0, "reason": "短い理由"}
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
        raise ValueError("presence inference output was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("presence inference output must be a JSON object")
    return dict(payload)
