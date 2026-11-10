import numpy as np
import pytest

from bench.player import Player

pytestmark = pytest.mark.unit
SR = 1000


def ones(n: int) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


def test_chunks_are_placed_back_to_back_when_they_arrive_early():
    p = Player(SR)
    p.on_chunk(1.0, 0, ones(500))
    p.on_chunk(1.1, 0, ones(500))  # 前の断片がまだ鳴っている
    out = p.render()
    assert out[:1000].sum() == 0
    assert out[1000:2000].sum() == 1000
    assert len(out) == 2000


def test_chunk_after_a_gap_is_placed_at_arrival_time():
    p = Player(SR)
    p.on_chunk(0.0, 0, ones(100))
    p.on_chunk(0.5, 0, ones(100))
    out = p.render()
    assert out[100:500].sum() == 0
    assert out[500:600].sum() == 100


def test_stop_cuts_queued_audio_and_drops_later_chunks_of_old_generations():
    p = Player(SR)
    p.on_chunk(0.0, 3, ones(2000))
    p.on_stop(0.5, 3)
    assert p.on_chunk(0.6, 3, ones(100)) is False
    assert p.on_chunk(0.6, 2, ones(100)) is False
    assert p.stale_chunks == 2
    out = p.render(2.0)
    assert out[:500].sum() == 500
    assert out[500:].sum() == 0


def test_new_generation_plays_right_after_stop():
    p = Player(SR)
    p.on_chunk(0.0, 0, ones(2000))
    p.on_stop(0.5, 0)
    assert p.on_chunk(0.7, 1, ones(100)) is True
    out = p.render()
    assert out[500:700].sum() == 0
    assert out[700:800].sum() == 100


def test_stop_does_not_touch_newer_generation():
    p = Player(SR)
    p.on_chunk(0.0, 1, ones(1000))
    p.on_stop(0.2, 0)
    assert p.render().sum() == 1000


def test_level_and_played():
    p = Player(SR)
    assert p.played(0.5) == (None, 0.0)
    p.on_chunk(1.0, 4, ones(1000))
    assert p.level(0.9) == 0.0
    assert p.level(1.5) == pytest.approx(1.0)
    assert p.level(2.5) == 0.0
    assert p.played(1.25) == (4, 0.25)
    assert p.played(5.0) == (4, 1.0)
