"""MaaiSensorの専用スレッド本体。状態は引き続きセンサーに置く。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from queue import Empty
from typing import TYPE_CHECKING, Any

from engine.dto import IN_SR, MaaiFrame, MaaiObservation

if TYPE_CHECKING:
    from engine.sensors.maai import MaaiSensor

log = logging.getLogger(__name__)


def run_worker(sensor: MaaiSensor, to_frame: Callable[[Mapping[str, Any], float, int], MaaiFrame]) -> None:
    model = None
    started = sensor._clock()
    log.info("maai sensor starting source=%s", sensor._source)
    try:
        model = sensor._factory()
        if getattr(model, "audio_samples_per_frame", sensor._frame_samples) != sensor._frame_samples:
            raise ValueError("sensor frame_samples does not match the model frame rate")
        ready = sensor._clock()
        sensor._observe(MaaiObservation(
            kind="ready", source=sensor._source, at_s=0.0,
            worker_started_perf_s=started, model_ready_perf_s=ready))
        previous_end = None
        while True:
            with sensor._condition:
                sensor._condition.wait_for(lambda: sensor._closed or sensor._pending)
                if sensor._closed:
                    break
                end, mic, tomoko, enqueued = sensor._pending.popleft()
                dequeued = sensor._clock()
                pending, dropped = len(sensor._pending), sensor._dropped_frames
            reset_start = reset_end = None
            if previous_end is not None and end - previous_end != sensor._frame_samples:
                reset_start = sensor._clock()
                model.reset_runtime_state()
                reset_end = sensor._clock()
                log.warning("maai context reset after input gap source=%s at_s=%.3f",
                            sensor._source, end / IN_SR)
            previous_end = end
            ch1, ch2 = (mic, tomoko) if sensor._user_channel == 0 else (tomoko, mic)
            process_start = sensor._clock()
            model.process(ch1, ch2)
            process_end = sensor._clock()
            try:
                result = model.result_dict_queue.get_nowait()
            except Empty:
                result = None
            if result is not None:
                frame = to_frame(result, end / IN_SR, sensor._user_channel)
                with sensor._condition:
                    if sensor._closed:
                        break
                    sensor._latest = frame
                if sensor._on_frame is not None:
                    sensor._loop.call_soon_threadsafe(sensor._deliver, frame)
            with sensor._condition:
                latest = None if sensor._latest is None else sensor._latest.at_s
            sensor._observe(MaaiObservation(
                kind="process", source=sensor._source, at_s=end / IN_SR,
                enqueued_perf_s=enqueued, dequeued_perf_s=dequeued,
                process_started_perf_s=process_start, process_finished_perf_s=process_end,
                reset_started_perf_s=reset_start, reset_finished_perf_s=reset_end,
                worker_started_perf_s=started, model_ready_perf_s=ready,
                pending_frames=pending, dropped_total=dropped,
                output=result is not None, latest_at_s=latest))
    except Exception as exc:
        log.exception("maai sensor failed source=%s", sensor._source)
        with sensor._condition:
            sensor._error = exc
    finally:
        if model is not None:
            try:
                model.stop(wait=True)
            except Exception as exc:
                log.exception("maai sensor stop failed source=%s", sensor._source)
                with sensor._condition:
                    if sensor._error is None:
                        sensor._error = exc
        with sensor._condition:
            sensor._closed = True
            sensor._pending.clear()
        log.info("maai sensor stopped source=%s", sensor._source)
