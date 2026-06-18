from __future__ import annotations

import argparse
import asyncio
import html
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from server.shared.candidate import CandidateSeed, CandidateStore, ThinkerSourceContext
from server.shared.models import UserContextSnapshot
from server.shared.perception import (
    PostgresHumanActivityObservationStore,
    PostgresHumanPresenceObservationStore,
    PostgresScreenActivityObservationStore,
    PostgresUserContextSnapshotStore,
)
from server.thinker.perception.context_snapshot import UserContextSnapshotBuilder
from server.thinker.sources.base import InformationSource
from server.thinker.sources.context_snapshot import ActivityContextSource, ScreenContextSource

logger = logging.getLogger(__name__)
SleepFunc = Callable[[float], Awaitable[None]]
DEFAULT_VLM_MODEL = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"


class SnapshotBuilder(Protocol):
    async def build_once(self, *, now: datetime): ...


class CaptureWorker(Protocol):
    async def capture_once(self, *, now: datetime | None = None): ...


class PerceptionInferenceWorker(Protocol):
    async def process_once(self, *, now: datetime | None = None): ...


@dataclass(frozen=True)
class CapturedPerceptionFrameSummary:
    source: str
    file_path: str
    width: int | None = None
    height: int | None = None
    sha256_prefix: str = ""


@dataclass(frozen=True)
class PerceptionInferenceSummary:
    kind: str
    label: str
    confidence: float
    model: str
    detail: str = ""
    frame_id: str = ""


@dataclass(frozen=True)
class Thinker2RunResult:
    snapshot_readiness: str
    snapshot_summary: str
    candidate_generated_count: int
    candidate_inserted_count: int
    queue_depths: dict[str, int] = field(default_factory=dict)
    inference_latency_ms: dict[str, float] = field(default_factory=dict)
    skipped_stale_frame_count: int = 0
    skipped_backlog_frame_count: int = 0
    camera_context_summary: str = "no_observation"
    screen_context_summary: str = "no_observation"
    captured_frames: tuple[CapturedPerceptionFrameSummary, ...] = ()
    capture_errors: tuple[str, ...] = ()
    perception_inferences: tuple[PerceptionInferenceSummary, ...] = ()
    perception_inference_errors: tuple[str, ...] = ()
    elapsed_ms: float = 0.0


class Thinker2Process:
    def __init__(
        self,
        *,
        snapshot_builder: SnapshotBuilder,
        candidate_store: CandidateStore,
        candidate_sources: Sequence[InformationSource],
        capture_workers: Sequence[CaptureWorker] = (),
        perception_inference_workers: Sequence[PerceptionInferenceWorker] = (),
    ) -> None:
        self.snapshot_builder = snapshot_builder
        self.candidate_store = candidate_store
        self.candidate_sources = tuple(candidate_sources)
        self.capture_workers = tuple(capture_workers)
        self.perception_inference_workers = tuple(perception_inference_workers)

    async def run_once(self, *, now: datetime | None = None) -> Thinker2RunResult:
        observed_at = now or datetime.now(UTC)
        started_at = time.perf_counter()
        captured_frames = []
        capture_errors = []
        for worker in self.capture_workers:
            try:
                captured_frames.append(
                    _captured_frame_summary(
                        await worker.capture_once(now=observed_at),
                    )
                )
            except Exception as exc:
                error = f"{type(worker).__name__}: {exc}"
                capture_errors.append(error)
                logger.warning("thinker2 perception capture failed: %s", error)
        perception_inferences = []
        perception_inference_errors = []
        presence_absent = False
        presence_absent_frame_id = ""
        for worker in self.perception_inference_workers:
            if presence_absent and bool(getattr(worker, "skip_when_presence_absent", False)):
                if hasattr(worker, "process_absent"):
                    perception_inferences.append(
                        await worker.process_absent(
                            frame_id=presence_absent_frame_id,
                            now=observed_at,
                        )
                    )
                else:
                    perception_inferences.append(
                        PerceptionInferenceSummary(
                            kind="camera_activity",
                            label="away",
                            confidence=1.0,
                            model="presence_gate",
                            detail="reason=no human visible",
                            frame_id=presence_absent_frame_id,
                        )
                    )
                continue
            try:
                summary = await worker.process_once(now=observed_at)
                if summary is not None:
                    perception_inferences.append(summary)
                    if summary.kind == "presence" and summary.label == "present=False":
                        presence_absent = True
                        presence_absent_frame_id = summary.frame_id
            except Exception as exc:
                error = f"{type(worker).__name__}: {exc}"
                perception_inference_errors.append(error)
                logger.warning("thinker2 perception inference failed: %s", error)
        build_result = await self.snapshot_builder.build_once(now=observed_at)
        generated: list[CandidateSeed] = []
        source_context = ThinkerSourceContext(observed_at=observed_at)
        for source in self.candidate_sources:
            generated.extend(await source.collect(source_context))

        inserted_count = 0
        for seed in generated:
            inserted = await self.candidate_store.insert_seed_candidate_once(
                seed,
                created_at=observed_at,
            )
            if inserted is not None:
                inserted_count += 1

        snapshot: UserContextSnapshot = build_result.snapshot
        trace = build_result.trace
        result = Thinker2RunResult(
            snapshot_readiness=snapshot.interaction_readiness,
            snapshot_summary=snapshot.context_summary,
            candidate_generated_count=len(generated),
            candidate_inserted_count=inserted_count,
            queue_depths={"candidate_sources": len(self.candidate_sources)},
            inference_latency_ms={"snapshot_build": float(trace.elapsed_ms)},
            skipped_stale_frame_count=0,
            skipped_backlog_frame_count=0,
            camera_context_summary=format_camera_context_summary(snapshot),
            screen_context_summary=format_screen_context_summary(snapshot),
            captured_frames=tuple(captured_frames),
            capture_errors=tuple(capture_errors),
            perception_inferences=tuple(perception_inferences),
            perception_inference_errors=tuple(perception_inference_errors),
            elapsed_ms=(time.perf_counter() - started_at) * 1000,
        )
        logger.info(
            "thinker2 run_once readiness=%s candidate_generated_count=%s "
            "candidate_inserted_count=%s queue_depths=%s inference_latency_ms=%s "
            "skipped_stale_frame_count=%s skipped_backlog_frame_count=%s "
            "camera_context=%s screen_context=%s captured_frames=%s "
            "capture_errors=%s perception_inferences=%s "
            "perception_inference_errors=%s elapsed_ms=%.1f",
            result.snapshot_readiness,
            result.candidate_generated_count,
            result.candidate_inserted_count,
            result.queue_depths,
            result.inference_latency_ms,
            result.skipped_stale_frame_count,
            result.skipped_backlog_frame_count,
            result.camera_context_summary,
            result.screen_context_summary,
            result.captured_frames,
            result.capture_errors,
            result.perception_inferences,
            result.perception_inference_errors,
            result.elapsed_ms,
        )
        return result


async def run_watch(
    process: Thinker2Process,
    *,
    interval_sec: float,
    sleep: SleepFunc = asyncio.sleep,
) -> None:
    while True:
        result = await process.run_once()
        print(format_thinker2_console_report(result), flush=True)
        await sleep(interval_sec)


def build_default_thinker2(
    config_path: str,
    *,
    capture_camera: bool = False,
    capture_screenshot: bool = False,
    infer_perception: bool = False,
    vlm_model: str = DEFAULT_VLM_MODEL,
    camera_index: int = 0,
    perception_artifact_dir: str = "logs/perception",
) -> Thinker2Process:
    from server.shared.calendar import PostgresCalendarEventStore
    from server.shared.candidate import PostgresCandidateStore
    from server.shared.config import NodeConfig
    from server.shared.perception import PostgresPerceptionFrameStore
    from server.thinker.perception.activity import (
        HumanActivityInferenceWorker,
        MlxVlmActivityBackend,
    )
    from server.thinker.perception.os_capture import (
        MacOSScreenshotCaptureProvider,
        OpenCVCameraCaptureProvider,
    )
    from server.thinker.perception.presence import (
        CameraCaptureWorker,
        MlxVlmPresenceBackend,
        PresenceInferenceWorker,
    )
    from server.thinker.perception.screen import (
        MlxVlmScreenActivityBackend,
        ScreenActivityInferenceWorker,
        ScreenshotCaptureWorker,
    )
    from server.world_observations.store import PostgresWorldObservationStore

    config = NodeConfig.load(config_path)
    frame_store = PostgresPerceptionFrameStore(config.database.dsn)
    presence_store = PostgresHumanPresenceObservationStore(config.database.dsn)
    activity_store = PostgresHumanActivityObservationStore(config.database.dsn)
    screen_store = PostgresScreenActivityObservationStore(config.database.dsn)
    snapshot_store = PostgresUserContextSnapshotStore(config.database.dsn)
    snapshot_builder = UserContextSnapshotBuilder(
        snapshot_store=snapshot_store,
        presence_store=presence_store,
        activity_store=activity_store,
        screen_store=screen_store,
        calendar_store=PostgresCalendarEventStore(config.database.dsn),
        world_store=PostgresWorldObservationStore(config.database.dsn),
        device_id=config.node.device_id,
    )
    capture_workers: list[CaptureWorker] = []
    artifact_root = Path(perception_artifact_dir)
    if capture_camera:
        capture_workers.append(
            CameraCaptureWorker(
                frame_store=frame_store,
                provider=OpenCVCameraCaptureProvider(
                    output_dir=artifact_root / "camera",
                    camera_index=camera_index,
                ),
                device_id=config.node.device_id,
            )
        )
    if capture_screenshot:
        capture_workers.append(
            ScreenshotCaptureWorker(
                frame_store=frame_store,
                provider=MacOSScreenshotCaptureProvider(
                    output_dir=artifact_root / "screenshot",
                ),
                device_id=config.node.device_id,
            )
        )
    perception_inference_workers: list[PerceptionInferenceWorker] = []
    if infer_perception:
        model_loader = _shared_mlx_vlm_loader()
        if capture_camera:
            perception_inference_workers.extend(
                [
                    PresenceInferenceConsoleWorker(
                        worker=PresenceInferenceWorker(
                            frame_store=frame_store,
                            observation_store=presence_store,
                            backend=MlxVlmPresenceBackend(
                                model=vlm_model,
                                model_loader=model_loader,
                            ),
                        ),
                        store=presence_store,
                    ),
                    ActivityInferenceConsoleWorker(
                        worker=HumanActivityInferenceWorker(
                            frame_store=frame_store,
                            activity_store=activity_store,
                            presence_store=presence_store,
                            backend=MlxVlmActivityBackend(
                                model=vlm_model,
                                model_loader=model_loader,
                            ),
                        ),
                        store=activity_store,
                    ),
                ]
            )
        if capture_screenshot:
            perception_inference_workers.append(
                ScreenActivityInferenceConsoleWorker(
                    worker=ScreenActivityInferenceWorker(
                        frame_store=frame_store,
                        screen_activity_store=screen_store,
                        backend=MlxVlmScreenActivityBackend(
                            model=vlm_model,
                            model_loader=model_loader,
                        ),
                    ),
                    store=screen_store,
                )
            )
    return Thinker2Process(
        snapshot_builder=snapshot_builder,
        candidate_store=PostgresCandidateStore(config.database.dsn),
        candidate_sources=[
            ScreenContextSource(snapshot_store=snapshot_store),
            ActivityContextSource(snapshot_store=snapshot_store),
        ],
        capture_workers=capture_workers,
        perception_inference_workers=perception_inference_workers,
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Tomoko thinker2 process.")
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    parser.add_argument("--inspection-output", default="reports/thinker2/latest.html")
    parser.add_argument("--capture-camera", action="store_true")
    parser.add_argument("--capture-screenshot", action="store_true")
    parser.add_argument("--capture-perception", action="store_true")
    parser.add_argument("--infer-perception", action="store_true")
    parser.add_argument("--vlm-model", default=DEFAULT_VLM_MODEL)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--perception-artifact-dir", default="logs/perception")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )
    await ensure_default_thinker2_schemas(args.config)
    capture_camera = bool(args.capture_camera or args.capture_perception)
    capture_screenshot = bool(args.capture_screenshot or args.capture_perception)
    process = build_default_thinker2(
        args.config,
        capture_camera=capture_camera,
        capture_screenshot=capture_screenshot,
        infer_perception=args.infer_perception,
        vlm_model=args.vlm_model,
        camera_index=args.camera_index,
        perception_artifact_dir=args.perception_artifact_dir,
    )
    if args.watch:
        await run_watch(process, interval_sec=args.interval_sec)
        return 0

    result = await process.run_once()
    output_path = Path(args.inspection_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_thinker2_inspection_html(result), encoding="utf-8")
    print(format_thinker2_console_report(result))
    print(
        "thinker2_once "
        f"readiness={result.snapshot_readiness} "
        f"candidate_generated={result.candidate_generated_count} "
        f"candidate_inserted={result.candidate_inserted_count} "
        f"inspection={output_path}"
    )
    return 0


def render_thinker2_inspection_html(result: Thinker2RunResult) -> str:
    rows = {
        "snapshot_readiness": result.snapshot_readiness,
        "snapshot_summary": result.snapshot_summary,
        "candidate_generated_count": result.candidate_generated_count,
        "candidate_inserted_count": result.candidate_inserted_count,
        "queue_depths": result.queue_depths,
        "inference_latency_ms": result.inference_latency_ms,
        "skipped_stale_frame_count": result.skipped_stale_frame_count,
        "skipped_backlog_frame_count": result.skipped_backlog_frame_count,
        "camera_context_summary": result.camera_context_summary,
        "screen_context_summary": result.screen_context_summary,
        "captured_frames": result.captured_frames,
        "capture_errors": result.capture_errors,
        "perception_inferences": result.perception_inferences,
        "perception_inference_errors": result.perception_inference_errors,
        "elapsed_ms": round(result.elapsed_ms, 1),
    }
    body = "\n".join(
        "<tr>"
        f"<th>{html.escape(str(key))}</th>"
        f"<td>{html.escape(str(value))}</td>"
        "</tr>"
        for key, value in rows.items()
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>thinker2 inspection</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;}"
        "table{border-collapse:collapse;max-width:960px;width:100%;}"
        "th,td{border:1px solid #ddd;padding:8px;text-align:left;}"
        "th{width:260px;background:#f6f8fa;}</style></head>"
        "<body><h1>thinker2 inspection</h1><table>"
        f"{body}</table></body></html>\n"
    )


def format_camera_context_summary(snapshot: UserContextSnapshot) -> str:
    parts: list[str] = []
    if snapshot.present is not None:
        parts.append(f"present={snapshot.present}")
    if snapshot.activity_label:
        parts.append(f"activity={snapshot.activity_label}")
    if snapshot.presence_observed_at is not None:
        parts.append(f"presence_observed_at={snapshot.presence_observed_at.isoformat()}")
    if snapshot.activity_observed_at is not None:
        parts.append(f"activity_observed_at={snapshot.activity_observed_at.isoformat()}")
    return "; ".join(parts) if parts else "no_observation"


def format_screen_context_summary(snapshot: UserContextSnapshot) -> str:
    parts: list[str] = []
    if snapshot.screen_activity_label:
        parts.append(f"activity={snapshot.screen_activity_label}")
    if snapshot.screen_observed_at is not None:
        parts.append(f"observed_at={snapshot.screen_observed_at.isoformat()}")
    return "; ".join(parts) if parts else "no_observation"


def format_thinker2_console_report(result: Thinker2RunResult) -> str:
    return (
        "thinker2_context "
        f"camera=\"{_quote_console_value(result.camera_context_summary)}\" "
        f"screen=\"{_quote_console_value(result.screen_context_summary)}\" "
        f"captured=\"{_quote_console_value(_format_captured_frames(result))}\" "
        f"capture_errors=\"{_quote_console_value(_format_capture_errors(result))}\" "
        f"perception=\"{_quote_console_value(_format_perception_inferences(result))}\" "
        f"perception_errors=\"{_quote_console_value(_format_perception_errors(result))}\" "
        f"readiness={result.snapshot_readiness} "
        f"candidates={result.candidate_generated_count}/{result.candidate_inserted_count}"
    )


class PresenceInferenceConsoleWorker:
    def __init__(self, *, worker, store) -> None:
        self.worker = worker
        self.store = store

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PerceptionInferenceSummary | None:
        result = await self.worker.process_once(now=now)
        if result.processed_frame_id is None:
            return None
        observation = await self.store.fetch_by_frame(result.processed_frame_id)
        if observation is None:
            return None
        return PerceptionInferenceSummary(
            kind="presence",
            label=f"present={observation.present}",
            confidence=float(observation.confidence),
            model=str(observation.model),
            detail=_reason_detail(observation.raw_reason_json),
            frame_id=str(result.processed_frame_id),
        )


class ActivityInferenceConsoleWorker:
    skip_when_presence_absent = True

    def __init__(self, *, worker, store) -> None:
        self.worker = worker
        self.store = store

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PerceptionInferenceSummary | None:
        result = await self.worker.process_once(now=now)
        if result.processed_frame_id is None:
            return None
        observation = await self.store.fetch_by_frame(result.processed_frame_id)
        if observation is None:
            return None
        return PerceptionInferenceSummary(
            kind="camera_activity",
            label=str(observation.activity_label),
            confidence=float(observation.confidence),
            model=str(observation.model),
            detail=_reason_detail(observation.raw_reason_json),
            frame_id=str(result.processed_frame_id),
        )

    async def process_absent(
        self,
        *,
        frame_id: str,
        now: datetime,
    ) -> PerceptionInferenceSummary:
        frame_uuid = UUID(frame_id) if frame_id else None
        presence_observation_id = None
        if frame_uuid is not None and self.worker.presence_store is not None:
            presence = await self.worker.presence_store.fetch_by_frame(frame_uuid)
            presence_observation_id = presence.id if presence is not None else None
        if frame_uuid is not None:
            await self.store.insert_observation(
                frame_id=frame_uuid,
                presence_observation_id=presence_observation_id,
                observed_at=now,
                activity_label="away",
                confidence=1.0,
                model="presence_gate",
                raw_reason_json={"reason": "no human visible"},
            )
        return PerceptionInferenceSummary(
            kind="camera_activity",
            label="away",
            confidence=1.0,
            model="presence_gate",
            detail="reason=no human visible",
            frame_id=frame_id,
        )


class ScreenActivityInferenceConsoleWorker:
    def __init__(self, *, worker, store) -> None:
        self.worker = worker
        self.store = store

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PerceptionInferenceSummary | None:
        result = await self.worker.process_once(now=now)
        if result.processed_frame_id is None:
            return None
        observation = await self.store.fetch_by_frame(result.processed_frame_id)
        if observation is None:
            return None
        detail = _join_detail_parts(
            (
                f"app={observation.app_hint}" if observation.app_hint else "",
                f"document={observation.document_hint}"
                if observation.document_hint
                else "",
                f"url={observation.url_hint}" if observation.url_hint else "",
                _summary_detail(observation.raw_reason_json),
                _task_context_detail(observation.raw_reason_json),
                _reason_detail(observation.raw_reason_json),
            )
        )
        return PerceptionInferenceSummary(
            kind="screen_activity",
            label=str(observation.screen_activity_label),
            confidence=float(observation.confidence),
            model=str(observation.model),
            detail=detail,
            frame_id=str(result.processed_frame_id),
        )


def _quote_console_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _captured_frame_summary(frame) -> CapturedPerceptionFrameSummary:
    return CapturedPerceptionFrameSummary(
        source=str(frame.source),
        file_path=str(frame.file_path),
        width=frame.width,
        height=frame.height,
        sha256_prefix=str(frame.sha256)[:12],
    )


def _format_captured_frames(result: Thinker2RunResult) -> str:
    if not result.captured_frames:
        return "none"
    return ", ".join(
        f"{frame.source}:{frame.file_path} {_format_dimensions(frame)} "
        f"sha={frame.sha256_prefix}"
        for frame in result.captured_frames
    )


def _format_capture_errors(result: Thinker2RunResult) -> str:
    if not result.capture_errors:
        return "none"
    return "; ".join(result.capture_errors)


def _format_perception_inferences(result: Thinker2RunResult) -> str:
    if not result.perception_inferences:
        return "none"
    return "; ".join(_format_perception_inference(item) for item in result.perception_inferences)


def _format_perception_inference(item: PerceptionInferenceSummary) -> str:
    base = (
        f"{item.kind}:{item.label} conf={item.confidence:.2f} "
        f"model={item.model}"
    )
    if item.detail:
        return f"{base} {item.detail}"
    return base


def _format_perception_errors(result: Thinker2RunResult) -> str:
    if not result.perception_inference_errors:
        return "none"
    return "; ".join(result.perception_inference_errors)


def _reason_detail(reason_json: dict[str, object]) -> str:
    reason = reason_json.get("reason") if isinstance(reason_json, dict) else None
    if not isinstance(reason, str):
        return ""
    reason = " ".join(reason.split())
    if len(reason) > 80:
        reason = f"{reason[:77]}..."
    return f"reason={reason}" if reason else ""


def _summary_detail(reason_json: dict[str, object]) -> str:
    summary = reason_json.get("summary") if isinstance(reason_json, dict) else None
    if not isinstance(summary, str):
        return ""
    summary = " ".join(summary.split())
    if len(summary) > 220:
        summary = f"{summary[:217]}..."
    return f"summary={summary}" if summary else ""


def _task_context_detail(reason_json: dict[str, object]) -> str:
    task_context = reason_json.get("task_context") if isinstance(reason_json, dict) else None
    if not isinstance(task_context, str):
        return ""
    task_context = " ".join(task_context.split())
    if len(task_context) > 120:
        task_context = f"{task_context[:117]}..."
    return f"task={task_context}" if task_context else ""


def _join_detail_parts(parts: Sequence[str]) -> str:
    return " ".join(part for part in parts if part)


def _shared_mlx_vlm_loader():
    cache: dict[str, tuple[object, object]] = {}

    def load_model(model_name: str) -> tuple[object, object]:
        if model_name not in cache:
            from mlx_vlm import load

            cache[model_name] = load(model_name)
        return cache[model_name]

    return load_model


def _format_dimensions(frame: CapturedPerceptionFrameSummary) -> str:
    if frame.width is None or frame.height is None:
        return "unknown_size"
    return f"{frame.width}x{frame.height}"


async def ensure_default_thinker2_schemas(config_path: str) -> None:
    import psycopg

    from server.shared.config import NodeConfig
    from server.shared.perception import (
        HUMAN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
        HUMAN_PRESENCE_OBSERVATIONS_SCHEMA_SQL,
        PERCEPTION_FRAMES_SCHEMA_SQL,
        SCREEN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
        USER_CONTEXT_SNAPSHOTS_SCHEMA_SQL,
    )

    config = NodeConfig.load(config_path)
    async with await psycopg.AsyncConnection.connect(config.database.dsn) as conn:
        async with conn.cursor() as cur:
            for ddl in (
                PERCEPTION_FRAMES_SCHEMA_SQL,
                HUMAN_PRESENCE_OBSERVATIONS_SCHEMA_SQL,
                HUMAN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
                SCREEN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
                USER_CONTEXT_SNAPSHOTS_SCHEMA_SQL,
            ):
                await cur.execute(ddl)


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
