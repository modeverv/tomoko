"""MaAI のモデルを接続ごとに貸し出す。MaAI は読み込まない。"""

import queue
import threading

import numpy as np
import pytest

from engine.sensors.maai import SharedMaai

pytestmark = pytest.mark.unit


class FakeModel:
    """MOCK: process の回数とリセットの回数を数える。"""

    audio_samples_per_frame = 1280

    def __init__(self):
        self.result_dict_queue = queue.Queue()
        self.processed = 0
        self.resets = 0
        self.stopped = False

    def process(self, ch1, ch2):
        self.processed += 1
        self.result_dict_queue.put({"vap": {}})

    def reset_runtime_state(self):
        self.resets += 1

    def stop(self, wait=True):
        self.stopped = True


def test_shared_model_is_warmed_once_and_reset_for_each_lease():
    model = FakeModel()
    shared = SharedMaai(model, warm_frames=3)
    assert model.processed == 3 and model.result_dict_queue.empty()
    lease = shared.lease()
    assert lease.audio_samples_per_frame == 1280
    lease.process(np.zeros(1280, dtype=np.float32), np.zeros(1280, dtype=np.float32))
    lease.stop()
    lease.stop()  # 2 度返しても壊れない
    assert not model.stopped  # 返すだけで、モデルは止めない
    assert model.result_dict_queue.empty()  # 残った結果は次の接続に渡さない


def test_only_one_connection_holds_the_model_at_a_time():
    shared = SharedMaai(FakeModel(), warm_frames=0)
    first = shared.lease()
    got = []
    t = threading.Thread(target=lambda: got.append(shared.lease()))
    t.start()
    t.join(0.1)
    assert not got  # 前の接続が返すまで待つ
    first.stop()
    t.join(1.0)
    assert len(got) == 1
    got[0].stop()


def test_a_model_that_failed_is_not_lent_again():
    model = FakeModel()

    def fail(ch1, ch2):
        raise RuntimeError("boom")

    shared = SharedMaai(model, warm_frames=0)
    lease = shared.lease()
    model.process = fail
    with pytest.raises(RuntimeError):
        lease.process(np.zeros(1280, dtype=np.float32), np.zeros(1280, dtype=np.float32))
    lease.stop()
    with pytest.raises(RuntimeError):
        shared.lease()


def test_waiting_for_a_busy_model_gives_up():
    shared = SharedMaai(FakeModel(), warm_frames=0, lease_timeout_s=0.05)
    first = shared.lease()
    with pytest.raises(TimeoutError):
        shared.lease()
    first.stop()
