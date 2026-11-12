"""合成波形と偽モデルだけで、同期推論・時計・破棄・終了を確かめる。"""

from __future__ import annotations

import asyncio
import queue
import threading
import time

import numpy as np
import pytest

from engine.dto import MaaiFrame
from engine.sensors.maai import MaaiSensor

pytestmark = pytest.mark.unit


def predictions():
    return {
        "t": 999999.0,
        "vap": {"p_now": [0.1, 0.9], "p_future": [0.2, 0.8], "vad": [0.3, 0.7]},
        "bc": {"p_bc": 0.6, "p_bc_detect": 0.99},
        "bc_det": {"p_bc_det": [0.4, 0.5]},
    }


class FakeModel:
    """MOCK: テスト専用。process と同じスレッドで結果をキューへ置く。"""

    def __init__(self, *, gate=None, skip_first=False, result=None):
        self.result_dict_queue = queue.Queue()
        self.calls = queue.Queue()
        self.finished = queue.Queue()
        self.gate, self.skip_first = gate, skip_first
        self.result = predictions() if result is None else result
        self.stopped = threading.Event()
        self.count = 0
        self.context, self.resets = [], 0

    def process(self, ch1, ch2):
        assert self.result_dict_queue.empty()
        self.count += 1
        self.context.append(float(ch1[0]))
        self.calls.put((ch1.copy(), ch2.copy(), threading.get_ident()))
        if self.gate is not None and self.count == 1:
            assert self.gate.wait(3), "テストの解除漏れ"
        if not (self.skip_first and self.count == 1):
            self.result_dict_queue.put(self.result)
        self.finished.put(self.count)

    def reset_runtime_state(self):
        self.context.clear()
        self.resets += 1

    def stop(self, wait=True):
        assert wait
        self.stopped.set()


@pytest.fixture
def make_sensor():
    sensors = []

    def make(model=None, **kwargs):
        model = FakeModel() if model is None else model
        sensor = MaaiSensor(lambda: model, **kwargs)
        sensors.append((sensor, model))
        return sensor, model

    yield make
    for sensor, model in sensors:
        if model.gate is not None:
            model.gate.set()
        sensor.close()


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "作業スレッドの完了待ちが期限超過"
        time.sleep(0.001)


def channels(model, count):
    calls = [model.calls.get(timeout=2) for _ in range(count)]
    return np.concatenate([c[0] for c in calls]), np.concatenate([c[1] for c in calls])


def test_arbitrary_mic_chunks_make_exact_frames_and_own_the_input(make_sensor):
    sensor, model = make_sensor(max_pending_frames=20)
    original = np.arange(1280 * 3 + 77, dtype=np.float32)
    cursor = 0
    for size in (1, 127, 512, 17, 2560, 700):
        chunk = original[cursor:cursor + size].copy()
        assert sensor.feed_mic(chunk) == 0
        chunk[:] = -1
        cursor += size
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.24)
    mic, tomoko = channels(model, 3)
    np.testing.assert_array_equal(mic, original[:3840])
    np.testing.assert_array_equal(tomoko, np.zeros(3840))
    assert model.calls.empty()
    sensor.feed_mic(np.zeros(1280 - 77, dtype=np.float32))
    wait_until(lambda: sensor.latest().at_s == 0.32)


def test_tomoko_starts_on_the_mic_clock_and_is_resampled(make_sensor):
    sensor, model = make_sensor()
    audio = np.arange(2400, dtype=np.float32) / 2400
    sensor.add_tomoko_audio(audio, at_mic_s=0.04)
    audio[:] = -1
    sensor.feed_mic(np.zeros(2560, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.16)
    _, tomoko = channels(model, 2)
    expected = np.zeros(2560, dtype=np.float32)
    expected[640:2240] = np.arange(1600) * 1.5 / 2400
    np.testing.assert_allclose(tomoko, expected, atol=1e-7)


def test_sent_audio_is_appended_and_later_audio_can_have_a_gap(make_sensor):
    sensor, model = make_sensor(max_pending_frames=4)
    sensor.add_tomoko_audio(np.full(1200, 0.2, dtype=np.float32), 0)
    sensor.add_tomoko_audio(np.full(1200, 0.4, dtype=np.float32), 0)
    sensor.add_tomoko_audio(np.full(1200, 0.8, dtype=np.float32), 0.12)
    sensor.feed_mic(np.zeros(3840, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.24)
    _, tomoko = channels(model, 3)
    expected = np.zeros(3840, dtype=np.float32)
    expected[:800], expected[800:1600], expected[1920:2720] = 0.2, 0.4, 0.8
    np.testing.assert_array_equal(tomoko, expected)


def test_tiny_tts_chunks_do_not_accumulate_resampling_roundoff(make_sensor):
    sensor, model = make_sensor(frame_samples=4)
    for value in range(6):
        sensor.add_tomoko_audio(np.array([value], dtype=np.float32), 0)
    sensor.feed_mic(np.zeros(4, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None)
    _, tomoko = channels(model, 1)
    np.testing.assert_array_equal(tomoko, [0, 1.5, 3, 4.5])


def test_late_tts_does_not_rewrite_already_submitted_mic_frames(make_sensor):
    sensor, model = make_sensor(frame_samples=1)
    sensor.add_tomoko_audio(np.array([0, 1], dtype=np.float32), 0)
    sensor.feed_mic(np.zeros(2, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 2 / 16000)
    sensor.add_tomoko_audio(np.array([2, 3], dtype=np.float32), 0)
    sensor.feed_mic(np.zeros(1, dtype=np.float32))
    wait_until(lambda: sensor.latest().at_s == 3 / 16000)
    _, tomoko = channels(model, 3)
    np.testing.assert_array_equal(tomoko, [0, 1, 3])  # 未着の sample 2 を待たず右端保持


def test_stop_truncates_future_audio_and_allows_a_new_reply(make_sensor):
    sensor, model = make_sensor()
    sensor.add_tomoko_audio(np.full(4800, 0.2, dtype=np.float32), 0)
    sensor.feed_mic(np.zeros(640, dtype=np.float32))
    sensor.stop_tomoko(0.06)
    sensor.add_tomoko_audio(np.full(1200, 0.8, dtype=np.float32), 0.08)
    sensor.feed_mic(np.zeros(1920, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.16)
    _, tomoko = channels(model, 2)
    expected = np.zeros(2560, dtype=np.float32)
    expected[:960], expected[1280:2080] = 0.2, 0.8
    np.testing.assert_array_equal(tomoko, expected)


def test_stop_also_truncates_audio_in_waiting_frames(make_sensor):
    model = FakeModel(gate=threading.Event())
    sensor, _ = make_sensor(model)
    sensor.feed_mic(np.zeros(1280, dtype=np.float32))
    model.calls.get(timeout=2)
    sensor.add_tomoko_audio(np.ones(3840, dtype=np.float32), 0.08)
    sensor.feed_mic(np.zeros(2560, dtype=np.float32))
    sensor.stop_tomoko(0.12)
    model.gate.set()
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.24)
    _, tomoko = channels(model, 2)
    np.testing.assert_array_equal(tomoko, np.r_[np.ones(640), np.zeros(1920)])


@pytest.mark.parametrize("user_channel", [0, 1])
def test_nested_output_uses_user_tomoko_order_and_sample_time(make_sensor, user_channel):
    sensor, model = make_sensor(user_channel=user_channel)
    sensor.feed_mic(np.ones(1280, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None)
    frame = sensor.latest()
    assert isinstance(frame, MaaiFrame) and not hasattr(frame, "__dict__")
    assert frame.at_s == 0.08
    assert frame.p_now == ((0.1, 0.9) if user_channel == 0 else (0.9, 0.1))
    assert frame.p_future == ((0.2, 0.8) if user_channel == 0 else (0.8, 0.2))
    assert frame.p_bc == 0.6
    assert frame.p_bc_det_user == (0.4 if user_channel == 0 else 0.5)
    assert (frame.vad_user, frame.vad_tomoko) == (
        (0.3, 0.7) if user_channel == 0 else (0.7, 0.3)
    )
    first, second = channels(model, 1)
    np.testing.assert_array_equal(first, np.full(1280, user_channel == 0))
    np.testing.assert_array_equal(second, np.full(1280, user_channel == 1))


@pytest.mark.parametrize("user_channel", [0, 1])
def test_single_cpc_flat_output_uses_user_tomoko_order_and_sample_time(make_sensor, user_channel):
    model = FakeModel(result={"t": 999999.0, **predictions()["vap"]})
    sensor, _ = make_sensor(model, frame_samples=1600, user_channel=user_channel)
    sensor.feed_mic(np.ones(1600, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None or sensor.error is not None)
    assert sensor.error is None
    assert sensor.latest() == MaaiFrame(
        at_s=0.1,
        p_now=(0.1, 0.9) if user_channel == 0 else (0.9, 0.1),
        p_future=(0.2, 0.8) if user_channel == 0 else (0.8, 0.2),
        vad_user=0.3 if user_channel == 0 else 0.7,
        vad_tomoko=0.7 if user_channel == 0 else 0.3,
    )


def test_optional_modes_and_initial_no_output_do_not_block(make_sensor):
    model = FakeModel(skip_first=True, result={"vap": predictions()["vap"]})
    sensor, _ = make_sensor(model)
    assert sensor.latest() is None
    sensor.feed_mic(np.zeros(1280, dtype=np.float32))
    assert model.finished.get(timeout=2) == 1
    assert sensor.latest() is None
    sensor.feed_mic(np.zeros(1280, dtype=np.float32))
    wait_until(lambda: sensor.latest() is not None)
    assert sensor.latest().at_s == 0.16
    assert sensor.latest().p_bc is None and sensor.latest().p_bc_det_user is None


def test_lag_drops_oldest_waiting_frames_and_reports_the_total(make_sensor, caplog):
    model = FakeModel(gate=threading.Event())
    sensor, _ = make_sensor(model, max_pending_frames=2)
    sensor.feed_mic(np.zeros(1280, dtype=np.float32))
    model.calls.get(timeout=2)
    assert sensor.feed_mic(np.repeat(np.arange(1, 6, dtype=np.float32), 1280)) == 3
    assert sensor.dropped_frames == 3
    model.gate.set()
    wait_until(lambda: sensor.latest() is not None and sensor.latest().at_s == 0.48)
    mic, _ = channels(model, 2)
    np.testing.assert_array_equal(mic, np.repeat([4, 5], 1280))
    assert "dropped_frames=3" in caplog.text
    assert model.resets == 1 and model.context == [4, 5]


async def test_callback_runs_on_the_event_loop_for_each_result(make_sensor):
    frames, threads, arrived = [], [], asyncio.Event()

    def callback(frame):
        frames.append(frame)
        threads.append(threading.get_ident())
        if len(frames) == 2:
            arrived.set()

    sensor, model = make_sensor(on_frame=callback, loop=asyncio.get_running_loop())
    sensor.feed_mic(np.zeros(2560, dtype=np.float32))
    await asyncio.wait_for(arrived.wait(), 2)
    assert [frame.at_s for frame in frames] == [0.08, 0.16]
    assert threads == [threading.get_ident()] * 2
    assert model.calls.get(timeout=2)[2] != threading.get_ident()


def test_close_discards_callbacks_already_waiting_on_the_loop(make_sensor):
    loop, frames = asyncio.new_event_loop(), []
    try:
        sensor, _ = make_sensor(on_frame=frames.append, loop=loop)
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))
        wait_until(lambda: sensor.latest() is not None)
        sensor.close()
        loop.run_until_complete(asyncio.sleep(0))
        assert frames == []
    finally:
        loop.close()


def test_close_joins_the_worker_and_is_idempotent(make_sensor):
    sensor, model = make_sensor()
    sensor.close()
    assert model.stopped.is_set()
    assert not sensor._thread.is_alive()
    sensor.close()
    with pytest.raises(RuntimeError, match="closed"):
        sensor.feed_mic(np.zeros(1280, dtype=np.float32))


def test_close_has_a_deadline_when_process_is_stuck(make_sensor):
    model = FakeModel(gate=threading.Event())
    sensor, _ = make_sensor(model)
    sensor.feed_mic(np.zeros(1280, dtype=np.float32))
    model.calls.get(timeout=2)
    with pytest.raises(TimeoutError):
        sensor.close(timeout=0.01)
    model.gate.set()
    sensor.close()
    assert not sensor._thread.is_alive() and sensor.latest() is None


@pytest.mark.parametrize("kwargs", [
    {"frame_samples": 0}, {"max_pending_frames": 0}, {"user_channel": 2},
])
def test_invalid_sensor_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        MaaiSensor(FakeModel, **kwargs)
