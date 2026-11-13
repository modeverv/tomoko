"""MOCK: 偽時計・同期モデル・止めたイベントループでワーカーの各時計を照合する。"""

from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest
from test_engine_maai_sensor import FakeModel, wait_until

from engine.sensors.maai import MaaiSensor

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.value = 10.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


class TimedModel(FakeModel):
    def __init__(self, clock: Clock, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.clock = clock

    def process(self, ch1: np.ndarray, ch2: np.ndarray) -> None:
        super().process(ch1, ch2)
        self.clock.advance(0.02)

    def reset_runtime_state(self) -> None:
        super().reset_runtime_state()
        self.clock.advance(0.03)


class SkipAfterResetModel(TimedModel):
    def __init__(self, clock: Clock, **kwargs: object) -> None:
        super().__init__(clock, **kwargs)
        self.skip_next = True

    def process(self, ch1: np.ndarray, ch2: np.ndarray) -> None:
        super().process(ch1, ch2)
        if self.skip_next:
            self.result_dict_queue.get_nowait()
            self.skip_next = False

    def reset_runtime_state(self) -> None:
        super().reset_runtime_state()
        self.skip_next = True


def test_factory_wait_is_included_in_first_frame_queue_wait() -> None:
    clock, loop, observations = Clock(), asyncio.new_event_loop(), []
    entered, release = threading.Event(), threading.Event()
    model = TimedModel(clock)

    def factory():
        entered.set()
        assert release.wait(2)
        return model

    sensor = MaaiSensor(factory, source="cpc", clock=clock,
                        on_observation=observations.append, loop=loop)
    try:
        assert entered.wait(2)
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))
        clock.advance(0.7)
        release.set()
        wait_until(lambda: sensor.latest() is not None)
        loop.run_until_complete(asyncio.sleep(0))
        ready = next(r for r in observations if r.kind == "ready")
        frame = next(r for r in observations if r.kind == "process")
        assert ready.model_ready_perf_s - ready.worker_started_perf_s == pytest.approx(0.7)
        assert frame.dequeued_perf_s - frame.enqueued_perf_s == pytest.approx(0.7)
    finally:
        release.set()
        sensor.close()
        loop.close()


def test_queue_process_and_dispatch_use_one_perf_clock() -> None:
    clock, loop, observations = Clock(), asyncio.new_event_loop(), []
    model = TimedModel(clock, gate=threading.Event())
    sensor = MaaiSensor(lambda: model, source="mimi", clock=clock,
                        on_observation=observations.append, loop=loop)
    try:
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))
        model.calls.get(timeout=2)
        clock.advance(0.1)
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))
        clock.advance(0.2)
        model.gate.set()
        wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.16)
        clock.advance(0.05)
        loop.run_until_complete(asyncio.sleep(0))
        rows = [r for r in observations if r.kind == "process"]
        assert len(rows) == 2 and {r.source for r in rows} == {"mimi"}
        first, second = rows
        assert first.enqueued_perf_s == first.dequeued_perf_s == first.process_started_perf_s == 10
        assert first.process_finished_perf_s == pytest.approx(10.32)
        assert second.dequeued_perf_s - second.enqueued_perf_s == pytest.approx(0.22)
        assert second.process_finished_perf_s - second.process_started_perf_s == pytest.approx(0.02)
        assert second.dispatched_perf_s - second.posted_perf_s == pytest.approx(0.05)
        assert second.at_s == second.latest_at_s == 0.16
    finally:
        model.gate.set()
        sensor.close()
        loop.close()


def test_drop_reset_and_no_output_are_observed_without_a_prediction() -> None:
    clock, loop, observations = Clock(), asyncio.new_event_loop(), []
    model = SkipAfterResetModel(clock, gate=threading.Event())
    sensor = MaaiSensor(lambda: model, source="mimi", max_pending_frames=2, clock=clock,
                        on_observation=observations.append, loop=loop)
    try:
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))
        model.calls.get(timeout=2)
        assert sensor.feed_mic(np.zeros(5 * 1280, dtype=np.float32)) == 3
        model.gate.set()
        wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.48)
        loop.run_until_complete(asyncio.sleep(0))
        rows = [r for r in observations if r.kind == "process"]
        assert len(rows) == 3 and rows[0].output is False and rows[0].latest_at_s is None
        reset = rows[1]
        assert reset.reset_finished_perf_s - reset.reset_started_perf_s == pytest.approx(0.03)
        assert reset.process_started_perf_s >= reset.reset_finished_perf_s
        assert reset.output is False and rows[2].output is True
        assert rows[2].reset_started_perf_s is None
        drops = [r for r in observations if r.kind == "drop"]
        assert len(drops) == 1 and drops[0].dropped_frames == 3
    finally:
        model.gate.set()
        sensor.close()
        loop.close()


def test_observer_failure_is_logged_and_does_not_break_inference(caplog) -> None:
    loop = asyncio.new_event_loop()
    model = FakeModel()

    def broken(observation) -> None:
        raise RuntimeError("MOCK: observer failed")

    sensor = MaaiSensor(lambda: model, on_observation=broken, loop=loop)
    try:
        sensor.feed_mic(np.zeros(2560, dtype=np.float32))
        wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.16)
        loop.run_until_complete(asyncio.sleep(0))
        assert sensor.error is None
        assert "observation callback failed" in caplog.text
    finally:
        sensor.close()
        loop.close()


def test_close_discards_observations_queued_on_the_event_loop() -> None:
    loop, observations = asyncio.new_event_loop(), []
    sensor = MaaiSensor(FakeModel, on_observation=observations.append, loop=loop)
    try:
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))
        wait_until(lambda: sensor.latest() is not None)
        sensor.close()
        loop.run_until_complete(asyncio.sleep(0))
        assert observations == []
    finally:
        sensor.close()
        loop.close()
