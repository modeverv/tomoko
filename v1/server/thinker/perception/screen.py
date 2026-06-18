from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from server.shared.models import PerceptionFrame
from server.shared.perception import PerceptionFrameStore, ScreenActivityObservationStore
from server.thinker.perception.presence import ModelLoader, PresenceStreamGenerator

SleepFunc = Callable[[float], Awaitable[None]]
OcrTextProvider = Callable[[str], str]
ScreenMetadataProvider = Callable[[], "ScreenMetadata"]
ScreenTextMergeProvider = Callable[
    ["ScreenActivityInferenceResult", str, "ScreenMetadata"], dict[str, object]
]


@dataclass(frozen=True)
class CapturedScreenshotArtifact:
    file_path: str
    sha256: str
    captured_at: datetime
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not self.file_path:
            raise ValueError("CapturedScreenshotArtifact.file_path must not be empty")
        if not self.sha256:
            raise ValueError("CapturedScreenshotArtifact.sha256 must not be empty")


class ScreenshotCaptureProvider(Protocol):
    async def capture(self, *, captured_at: datetime) -> CapturedScreenshotArtifact: ...


@dataclass(frozen=True)
class ScreenActivityInferenceResult:
    screen_activity_label: str
    confidence: float
    model: str
    app_hint: str | None = None
    document_hint: str | None = None
    url_hint: str | None = None
    raw_reason_json: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.screen_activity_label:
            raise ValueError("ScreenActivityInferenceResult.screen_activity_label is empty")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("ScreenActivityInferenceResult.confidence must be 0.0-1.0")
        if not self.model:
            raise ValueError("ScreenActivityInferenceResult.model must not be empty")


@dataclass(frozen=True)
class ScreenMetadata:
    front_app: str | None = None
    front_window_title: str | None = None
    chrome_title: str | None = None
    chrome_url: str | None = None


@dataclass(frozen=True)
class ScreenVisualCoarseSignal:
    watching_video: bool = False
    playing_game: bool = False
    coding_or_terminal: bool = False
    reading_document_or_web: bool = False
    reviewing_report_or_logs: bool = False
    communication_app: bool = False


class ScreenActivityInferenceBackend(Protocol):
    model: str

    async def infer_screen_activity(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> ScreenActivityInferenceResult: ...


@dataclass(frozen=True)
class ScreenActivityInferenceProcessResult:
    processed_frame_id: UUID | None
    skipped_stale_count: int = 0
    skipped_backlog_count: int = 0
    already_processed_count: int = 0


class ScreenshotCaptureWorker:
    def __init__(
        self,
        *,
        frame_store: PerceptionFrameStore,
        provider: ScreenshotCaptureProvider,
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
            source="screenshot",
            device_id=self.device_id,
            file_path=artifact.file_path,
            sha256=artifact.sha256,
            captured_at=artifact.captured_at,
            width=artifact.width,
            height=artifact.height,
        )
        await self.frame_store.apply_retention(
            source="screenshot",
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


class ScreenActivityInferenceWorker:
    def __init__(
        self,
        *,
        frame_store: PerceptionFrameStore,
        screen_activity_store: ScreenActivityObservationStore,
        backend: ScreenActivityInferenceBackend,
        stale_after: timedelta = timedelta(minutes=5),
        backlog_limit: int = 10,
    ) -> None:
        self.frame_store = frame_store
        self.screen_activity_store = screen_activity_store
        self.backend = backend
        self.stale_after = stale_after
        self.backlog_limit = backlog_limit

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> ScreenActivityInferenceProcessResult:
        observed_at = now or datetime.now(UTC)
        frames = await self.frame_store.fetch_retained_frames(
            source="screenshot",
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
            if await self.screen_activity_store.fetch_by_frame(frame.id) is not None:
                already_processed_count += 1
                continue
            pending.append(frame)

        if not pending:
            return ScreenActivityInferenceProcessResult(
                processed_frame_id=None,
                skipped_stale_count=skipped_stale_count,
                already_processed_count=already_processed_count,
            )

        frame = pending[0]
        assert frame.id is not None
        result = await self.backend.infer_screen_activity(
            frame.file_path,
            frame_id=frame.id,
        )
        await self.screen_activity_store.insert_observation(
            frame_id=frame.id,
            observed_at=observed_at,
            screen_activity_label=result.screen_activity_label,
            app_hint=result.app_hint,
            document_hint=result.document_hint,
            url_hint=result.url_hint,
            confidence=result.confidence,
            model=result.model,
            raw_reason_json=result.raw_reason_json,
        )
        return ScreenActivityInferenceProcessResult(
            processed_frame_id=frame.id,
            skipped_stale_count=skipped_stale_count,
            skipped_backlog_count=max(0, len(pending) - 1),
            already_processed_count=already_processed_count,
        )


class MlxVlmScreenActivityBackend:
    def __init__(
        self,
        *,
        model: str,
        max_tokens: int = 100,
        model_loader: ModelLoader | None = None,
        stream_generator: PresenceStreamGenerator | None = None,
        ocr_text_provider: OcrTextProvider | None = None,
        metadata_provider: ScreenMetadataProvider | None = None,
        text_merge_provider: ScreenTextMergeProvider | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._model_loader = model_loader or _load_mlx_vlm_model
        self._stream_generator = stream_generator or _stream_mlx_vlm
        self._ocr_text_provider = ocr_text_provider or tesseract_ocr_text
        self._metadata_provider = metadata_provider or macos_screen_metadata
        self._text_merge_provider = text_merge_provider or merge_screen_text_evidence
        self._loaded_model: Any | None = None
        self._processor: Any | None = None

    async def infer_screen_activity(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> ScreenActivityInferenceResult:
        del frame_id
        model, processor = self._load()
        ocr_text = _compact_ocr_text(self._ocr_text_provider(frame_path))
        metadata = self._metadata_provider()
        output = _collect_stream_text(
            self._stream_generator(
                model,
                processor,
                build_screen_activity_prompt(ocr_text),
                frame_path,
                self.max_tokens,
            )
        )
        try:
            result = parse_screen_activity_inference_output(output, model=self.model)
        except ValueError:
            if not _has_screen_evidence(ocr_text=ocr_text, metadata=metadata):
                raise
            result = ScreenActivityInferenceResult(
                screen_activity_label="unknown screen activity",
                confidence=0.5,
                model=self.model,
                raw_reason_json={
                    "reason": "vlm_json_parse_failed",
                    "summary": (
                        "The visual model did not return valid JSON; using OCR "
                        "and OS metadata."
                    ),
                    "vlm_raw_output_prefix": _compact_raw_output(output),
                },
            )
        return refine_screen_activity_with_evidence(
            result,
            ocr_text=ocr_text,
            metadata=metadata,
            text_merge_provider=self._text_merge_provider,
        )

    def _load(self) -> tuple[Any, Any]:
        if self._loaded_model is None or self._processor is None:
            self._loaded_model, self._processor = self._model_loader(self.model)
        return self._loaded_model, self._processor


def parse_screen_activity_inference_output(
    output: str,
    *,
    model: str,
) -> ScreenActivityInferenceResult:
    try:
        return parse_screen_activity_inference_lines(output, model=model)
    except ValueError:
        return parse_screen_activity_inference_json(
            _parse_json_object(output),
            model=model,
        )


def parse_screen_activity_inference_lines(
    output: str,
    *,
    model: str,
) -> ScreenActivityInferenceResult:
    values = _parse_key_value_lines(output)
    label = _optional_hint(values.get("SCREEN_ACTIVITY_LABEL"))
    if label is None:
        raise ValueError("screen activity lines require SCREEN_ACTIVITY_LABEL")
    confidence = _parse_float(values.get("CONFIDENCE"))
    if confidence is None:
        raise ValueError("screen activity lines require CONFIDENCE")
    raw_reason_json: dict[str, object] = {
        "reason": values.get("REASON", ""),
        "visual_coarse_signal": {
            "watching_video": _parse_bool_flag(values.get("WATCHING_VIDEO")),
            "playing_game": _parse_bool_flag(values.get("PLAYING_GAME")),
            "coding_or_terminal": _parse_bool_flag(values.get("CODING_OR_TERMINAL")),
            "reading_document_or_web": _parse_bool_flag(
                values.get("READING_DOCUMENT_OR_WEB")
            ),
            "reviewing_report_or_logs": _parse_bool_flag(
                values.get("REVIEWING_REPORT_OR_LOGS")
            ),
            "communication_app": _parse_bool_flag(values.get("COMMUNICATION_APP")),
        },
    }
    return ScreenActivityInferenceResult(
        screen_activity_label=label,
        confidence=confidence,
        model=model,
        app_hint=_none_hint(values.get("APP_HINT")),
        document_hint=_none_hint(values.get("DOCUMENT_HINT")),
        url_hint=_none_hint(values.get("URL_HINT")),
        raw_reason_json=raw_reason_json,
    )


def parse_screen_activity_inference_json(
    payload: dict[str, object],
    *,
    model: str,
) -> ScreenActivityInferenceResult:
    label = payload.get("screen_activity_label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("screen activity JSON requires non-empty screen_activity_label")
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        raise ValueError("screen activity JSON requires numeric confidence")
    raw_reason_json = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "screen_activity_label",
            "confidence",
            "app_hint",
            "document_hint",
            "url_hint",
        }
    }
    raw_reason_json["visual_coarse_signal"] = _coarse_signal_json(payload)
    return ScreenActivityInferenceResult(
        screen_activity_label=label.strip(),
        confidence=float(confidence),
        model=model,
        app_hint=_optional_hint(payload.get("app_hint")),
        document_hint=_optional_hint(payload.get("document_hint")),
        url_hint=_optional_hint(payload.get("url_hint")),
        raw_reason_json=raw_reason_json,
    )


_SCREEN_ACTIVITY_PROMPT = """Return only these fixed lines. Do not return JSON.
SCREEN_ACTIVITY_LABEL=<short label>
CONFIDENCE=<0.0-1.0>
APP_HINT=<app or none>
DOCUMENT_HINT=<document/page/file or none>
URL_HINT=<url or none>
WATCHING_VIDEO=<0 or 1>
PLAYING_GAME=<0 or 1>
CODING_OR_TERMINAL=<0 or 1>
READING_DOCUMENT_OR_WEB=<0 or 1>
REVIEWING_REPORT_OR_LOGS=<0 or 1>
COMMUNICATION_APP=<0 or 1>
REASON=<short evidence>
Question: Classify the visible screen at a coarse level.
OCR_TEXT:
{ocr_text}
Set 1 only when the visual evidence is clear. If uncertain, set 0 and lower
confidence. Do not write a long summary.
"""


def build_screen_activity_prompt(ocr_text: str) -> str:
    return _SCREEN_ACTIVITY_PROMPT.replace("{ocr_text}", ocr_text or "unknown")


def tesseract_ocr_text(frame_path: str) -> str:
    path = Path(frame_path)
    if not path.exists():
        return ""
    try:
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", "eng", "--psm", "6"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def macos_screen_metadata() -> ScreenMetadata:
    front_app = _run_osascript(
        'tell application "System Events" to get name of first application process '
        "whose frontmost is true"
    )
    front_window_title = ""
    if front_app:
        front_window_title = _run_osascript(
            f'tell application "System Events" to tell process "{_escape_applescript(front_app)}" '
            "to get name of front window"
        )
    chrome_title = _run_osascript(
        'tell application "Google Chrome" to get title of active tab of front window'
    )
    chrome_url = _run_osascript(
        'tell application "Google Chrome" to get URL of active tab of front window'
    )
    return ScreenMetadata(
        front_app=front_app or None,
        front_window_title=front_window_title or None,
        chrome_title=chrome_title or None,
        chrome_url=chrome_url or None,
    )


def refine_screen_activity_with_evidence(
    result: ScreenActivityInferenceResult,
    *,
    ocr_text: str,
    metadata: ScreenMetadata,
    text_merge_provider: ScreenTextMergeProvider | None = None,
) -> ScreenActivityInferenceResult:
    merge_provider = text_merge_provider or merge_screen_text_evidence
    evidence_text = " ".join(
        value
        for value in (
            ocr_text,
            metadata.front_app,
            metadata.front_window_title,
            metadata.chrome_title,
            metadata.chrome_url,
        )
        if value
    ).lower()

    if "thinker2" in evidence_text or "thinker?" in evidence_text:
        label = "reviewing thinker2 inspection and capture output"
        document_hint = _prefer_hint(
            metadata.chrome_title,
            metadata.front_window_title,
            result.document_hint,
        )
        return ScreenActivityInferenceResult(
            screen_activity_label=label,
            confidence=max(result.confidence, 0.95),
            model=result.model,
            app_hint=_prefer_hint(metadata.front_app, result.app_hint, "Chrome"),
            document_hint=document_hint,
            url_hint=_prefer_hint(metadata.chrome_url, result.url_hint),
            raw_reason_json={
                **result.raw_reason_json,
                **merge_provider(result, ocr_text, metadata),
                "refined_by": "ocr_os_metadata",
                "evidence": "OCR or window metadata mentions thinker2",
                "vlm_screen_activity_label": result.screen_activity_label,
            },
        )

    if "make thinker2-capture-once" in evidence_text or "thinker2_context" in evidence_text:
        return ScreenActivityInferenceResult(
            screen_activity_label="reviewing thinker2 capture console output",
            confidence=max(result.confidence, 0.94),
            model=result.model,
            app_hint=_prefer_hint(metadata.front_app, result.app_hint, "Terminal"),
            document_hint=_prefer_hint(metadata.front_window_title, result.document_hint),
            url_hint=_prefer_hint(metadata.chrome_url, result.url_hint),
            raw_reason_json={
                **result.raw_reason_json,
                **merge_provider(result, ocr_text, metadata),
                "refined_by": "ocr_os_metadata",
                "evidence": "OCR mentions thinker2 capture console output",
                "vlm_screen_activity_label": result.screen_activity_label,
            },
        )

    merged = merge_provider(result, ocr_text, metadata)
    if not merged:
        return result
    return ScreenActivityInferenceResult(
        screen_activity_label=result.screen_activity_label,
        confidence=result.confidence,
        model=result.model,
        app_hint=result.app_hint,
        document_hint=result.document_hint,
        url_hint=result.url_hint,
        raw_reason_json={**result.raw_reason_json, **merged},
    )


def merge_screen_text_evidence(
    result: ScreenActivityInferenceResult,
    ocr_text: str,
    metadata: ScreenMetadata,
) -> dict[str, object]:
    signal = _coarse_signal_from_reason(result.raw_reason_json)
    evidence = _screen_text_evidence(ocr_text, metadata)
    task_context = _task_context_from_signal(signal, evidence)
    return {
        "summary": _screen_evidence_summary(
            metadata,
            signal=signal,
            task_context=task_context,
            fallback="The screen appears to show active computer work.",
        ),
        "task_context": task_context,
        "visible_text_evidence": _visible_text_evidence(ocr_text),
    }


def _has_screen_evidence(*, ocr_text: str, metadata: ScreenMetadata) -> bool:
    evidence_text = " ".join(
        value
        for value in (
            ocr_text,
            metadata.front_app,
            metadata.front_window_title,
            metadata.chrome_title,
            metadata.chrome_url,
        )
        if value
    ).lower()
    return any(
        marker in evidence_text
        for marker in (
            "thinker2",
            "thinker?",
            "thinker2_context",
            "make thinker2-capture-once",
        )
    )


def _compact_ocr_text(text: str, *, max_chars: int = 1800) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    compact_lines = [line for line in lines if len(line) >= 3]
    compact = "\n".join(compact_lines)
    if len(compact) > max_chars:
        compact = compact[:max_chars].rsplit("\n", 1)[0]
    return compact


def _compact_raw_output(text: str, *, max_chars: int = 500) -> str:
    compact = " ".join(text.split())
    if len(compact) > max_chars:
        return f"{compact[: max_chars - 3]}..."
    return compact


def _optional_hint(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _none_hint(value: object) -> str | None:
    hint = _optional_hint(value)
    if hint is None:
        return None
    if hint.lower() in {"none", "null", "unknown", "n/a"}:
        return None
    return hint


def _parse_key_value_lines(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip().strip("-").strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    if parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


def _parse_bool_flag(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _prefer_hint(*values: str | None) -> str | None:
    for value in values:
        hint = _optional_hint(value)
        if hint:
            return hint
    return None


def _coarse_signal_json(payload: dict[str, object]) -> dict[str, bool]:
    return {
        "watching_video": bool(payload.get("watching_video", False)),
        "playing_game": bool(payload.get("playing_game", False)),
        "coding_or_terminal": bool(payload.get("coding_or_terminal", False)),
        "reading_document_or_web": bool(payload.get("reading_document_or_web", False)),
        "reviewing_report_or_logs": bool(payload.get("reviewing_report_or_logs", False)),
        "communication_app": bool(payload.get("communication_app", False)),
    }


def _coarse_signal_from_reason(reason_json: dict[str, object]) -> ScreenVisualCoarseSignal:
    payload = reason_json.get("visual_coarse_signal")
    if not isinstance(payload, dict):
        payload = reason_json
    return ScreenVisualCoarseSignal(
        watching_video=bool(payload.get("watching_video", False)),
        playing_game=bool(payload.get("playing_game", False)),
        coding_or_terminal=bool(payload.get("coding_or_terminal", False)),
        reading_document_or_web=bool(payload.get("reading_document_or_web", False)),
        reviewing_report_or_logs=bool(payload.get("reviewing_report_or_logs", False)),
        communication_app=bool(payload.get("communication_app", False)),
    )


def _screen_text_evidence(ocr_text: str, metadata: ScreenMetadata) -> str:
    return " ".join(
        value
        for value in (
            ocr_text,
            metadata.front_app,
            metadata.front_window_title,
            metadata.chrome_title,
            metadata.chrome_url,
        )
        if value
    ).lower()


def _task_context_from_signal(signal: ScreenVisualCoarseSignal, evidence: str) -> str:
    if signal.playing_game:
        return "playing or viewing a game"
    if signal.watching_video:
        return "watching a video or media playback"
    if signal.reviewing_report_or_logs or "thinker2" in evidence or "latest.html" in evidence:
        return "validating thinker2 perception capture and screen interpretation"
    if signal.coding_or_terminal:
        return "working in code or terminal output"
    if signal.communication_app:
        return "reading or writing in a communication app"
    if signal.reading_document_or_web:
        return "reading a document or web page"
    return "reviewing visible screen content"


def _visible_text_evidence(ocr_text: str, *, limit: int = 5) -> list[str]:
    evidence: list[str] = []
    for line in ocr_text.splitlines():
        text = " ".join(line.split())
        if len(text) < 5:
            continue
        evidence.append(text[:120])
        if len(evidence) >= limit:
            break
    return evidence


def _screen_evidence_summary(
    metadata: ScreenMetadata,
    *,
    signal: ScreenVisualCoarseSignal,
    task_context: str,
    fallback: str,
) -> str:
    coarse = _coarse_signal_phrase(signal)
    details = [
        f"front app is {metadata.front_app}" if metadata.front_app else "",
        f"window title is {metadata.front_window_title}"
        if metadata.front_window_title
        else "",
        f"Chrome title is {metadata.chrome_title}" if metadata.chrome_title else "",
        f"Chrome URL is {metadata.chrome_url}" if metadata.chrome_url else "",
    ]
    evidence = "; ".join(detail for detail in details if detail)
    base = f"The screen looks like {task_context}."
    if coarse:
        base = f"{base} The visual classifier flags {coarse}."
    if not evidence:
        return base or fallback
    return f"{base} OS metadata says {evidence}."


def _coarse_signal_phrase(signal: ScreenVisualCoarseSignal) -> str:
    labels = []
    if signal.watching_video:
        labels.append("video")
    if signal.playing_game:
        labels.append("game")
    if signal.coding_or_terminal:
        labels.append("code/terminal")
    if signal.reading_document_or_web:
        labels.append("document/web")
    if signal.reviewing_report_or_logs:
        labels.append("report/log review")
    if signal.communication_app:
        labels.append("communication")
    return ", ".join(labels)


def _run_osascript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


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
    payload = _first_json_object(stripped)
    if not isinstance(payload, dict):
        raise ValueError("screen activity output must be a JSON object")
    return dict(payload)


def _first_json_object(text: str) -> object:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return payload
    raise ValueError("screen activity output was not valid JSON")
