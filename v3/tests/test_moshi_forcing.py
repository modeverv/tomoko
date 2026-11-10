import pytest

from tomoko_v3.moshi_rt import TEXT_EPAD, TEXT_PAD, ForcingState, decide

pytestmark = pytest.mark.unit

W = 500  # モデルが出した「語」の token


def run(state, sampled_seq):
    return [decide(state, s, i) for i, s in enumerate(sampled_seq)]


def test_not_armed_passes_through():
    st = ForcingState()
    assert run(st, [TEXT_PAD, W, TEXT_EPAD]) == [TEXT_PAD, W, TEXT_EPAD]


def test_words_mode_replaces_only_word_ticks_and_keeps_model_timing():
    st = ForcingState(mode="words", armed=True)
    st.queue.extend([11, 12])
    out = run(st, [TEXT_PAD, W, TEXT_PAD, TEXT_EPAD, W, W])
    # 語の位置だけ 11, 12 に置き換わり、出し切った後は after="pad" で黙る
    assert out == [TEXT_PAD, 11, TEXT_PAD, TEXT_EPAD, 12, TEXT_PAD]


def test_skip_words_lets_model_start_then_replaces():
    st = ForcingState(mode="words", skip_words=1, armed=True)
    st.queue.extend([11])
    assert run(st, [W, W, W]) == [W, 11, TEXT_PAD]


def test_contiguous_mode_replaces_every_tick():
    st = ForcingState(mode="contiguous", after="free", armed=True)
    st.queue.extend([11, 12])
    assert run(st, [TEXT_PAD, TEXT_PAD, W]) == [11, 12, W]


def test_paced_schedule_shape():
    from tomoko_v3.moshi_rt import paced_schedule

    # 2 文字の語は 3 tick（語 + PAD 1）、2 語目の前に EPAD
    assert paced_schedule([11, 12], [2, 1], ticks_per_char=1.5) == [11, TEXT_PAD, TEXT_EPAD, 12]


def test_paced_mode_waits_for_model_then_follows_schedule():
    st = ForcingState(mode="paced", armed=True)
    st.queue.extend([11, TEXT_PAD, TEXT_EPAD, 12])
    out = run(st, [TEXT_PAD, TEXT_EPAD, W, W, TEXT_PAD, TEXT_PAD, W])
    assert out == [TEXT_PAD, TEXT_EPAD, 11, TEXT_PAD, TEXT_EPAD, 12, TEXT_PAD]
