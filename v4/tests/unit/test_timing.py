"""時刻の採点。区間の一覧だけで試す（VAD も音声も使わない）。"""

import pytest

from bench.score.timing import StepTime, score_timing

pytestmark = pytest.mark.unit


def step(start, end, pauses=()):
    voiced, t = [], start
    for a, b in pauses:
        voiced.append((t, a))
        t = b
    voiced.append((t, end))
    return StepTime(start, end, tuple(voiced), tuple(pauses))


# ---- T1 ----

def test_t1_clockwork_reply():
    r = score_timing("T1", [step(1.0, 3.0)], [(3.5, 5.5)])
    assert r["responded"] and not r["early"] and not r["silent"]
    assert r["latency"] == pytest.approx(0.5)


def test_t1_silent_run_is_a_failure_not_a_skip():
    r = score_timing("T1", [step(1.0, 3.0)], [])
    assert r["silent"] and not r["responded"] and r["latency"] is None


def test_t1_short_reaction_is_not_a_reply():
    r = score_timing("T1", [step(1.0, 3.0)], [(3.2, 3.6)])
    assert not r["responded"] and not r["silent"]


def test_t1_reply_too_late_is_not_a_reply():
    r = score_timing("T1", [step(1.0, 3.0)], [(8.5, 10.5)])
    assert not r["responded"]


def test_t1_talking_over_the_user_is_early_and_has_no_latency():
    r = score_timing("T1", [step(1.0, 3.0)], [(2.0, 6.0)])
    assert r["early"] and r["responded"] and r["latency"] is None


def test_t1_chatterbox_talks_over_the_user_which_is_not_the_same_as_early():
    r = score_timing("T1", [step(1.0, 4.0)], [(0.0, 30.0)])
    assert r["talked_over"] and not r["early"] and r["responded"] and r["latency"] is None


def test_t1_yielding_soon_after_the_user_starts_is_neither_early_nor_talking_over():
    # 前の返事の途中でユーザーが話し始め、0.3 秒で譲った
    r = score_timing("T1", [step(5.0, 8.0)], [(1.0, 5.3), (8.5, 11.0)])
    assert not r["early"] and not r["talked_over"] and r["latency"] == pytest.approx(0.5)


def test_t1_onset_just_before_the_end_counts_as_reply_with_zero_latency():
    r = score_timing("T1", [step(1.0, 3.0)], [(2.9, 5.0)])
    assert not r["early"] and r["latency"] == 0.0


def test_t1_short_backchannel_during_user_speech_is_not_early():
    r = score_timing("T1", [step(1.0, 5.0)], [(2.0, 2.4), (5.6, 8.0)])
    assert not r["early"] and r["latency"] == pytest.approx(0.6)


def test_turn_metrics_use_the_last_step():
    steps = [step(1.0, 3.0), step(8.0, 10.0)]
    r = score_timing("C2", steps, [(3.5, 6.0), (10.7, 13.0)])
    assert r["latency"] == pytest.approx(0.7) and not r["early"]


# ---- T2 ----

def test_t2_waiting_through_the_pause():
    r = score_timing("T2", [step(1.0, 6.0, pauses=[(2.5, 4.0)])], [(6.5, 9.0)])
    assert not r["interrupted"] and r["responded"]


def test_t2_main_utterance_in_the_pause_is_an_interruption():
    r = score_timing("T2", [step(1.0, 6.0, pauses=[(2.5, 4.0)])], [(3.0, 5.5)])
    assert r["interrupted"]


def test_t2_main_utterance_starting_late_in_the_pause_is_an_interruption():
    r = score_timing("T2", [step(1.0, 6.0, pauses=[(2.5, 4.0)])], [(3.9, 6.5)])
    assert r["interrupted"]


def test_t2_backchannel_in_the_pause_is_fine():
    r = score_timing("T2", [step(1.0, 6.0, pauses=[(2.5, 4.0)])], [(3.0, 3.4), (6.5, 9.0)])
    assert not r["interrupted"]


def test_t2_chatterbox_interrupts():
    r = score_timing("T2", [step(1.0, 6.0, pauses=[(2.5, 4.0)])], [(0.0, 30.0)])
    assert r["interrupted"]


# ---- T3 ----

def t3(tomoko, failed=False):
    return score_timing("T3", [step(1.0, 3.0), step(6.0, 6.4)], tomoko, precondition_failed=failed)


def test_t3_keeps_talking():
    r = t3([(3.5, 12.0)])
    assert r["precondition"] and not r["interrupted"]


def test_t3_goes_silent_and_stays_silent():
    r = t3([(3.5, 6.5), (8.0, 10.0)])
    assert r["precondition"] and r["interrupted"]


def test_t3_brief_dip_then_continues_is_not_an_interruption():
    r = t3([(3.5, 6.5), (7.0, 10.0)])
    assert not r["interrupted"]


def test_t3_stops_later_than_the_window_is_not_an_interruption():
    r = t3([(3.5, 9.0)])
    assert not r["interrupted"]


def test_t3_tomoko_not_speaking_at_the_backchannel_is_precondition_failure():
    assert not t3([(3.5, 5.0)])["precondition"]
    assert not t3([], failed=True)["precondition"]


# ---- T4 ----

def t4(tomoko, failed=False):
    return score_timing("T4", [step(1.0, 3.0), step(6.0, 8.0)], tomoko, precondition_failed=failed)


def test_t4_stops_quickly_then_answers():
    r = t4([(3.5, 6.3), (8.6, 11.0)])
    assert r["precondition"] and r["stopped"]
    assert r["stop_latency"] == pytest.approx(0.3)
    assert r["responded"] and r["latency"] == pytest.approx(0.6)


def test_t4_never_stops():
    r = t4([(3.5, 20.0)])
    assert r["precondition"] and not r["stopped"] and not r["responded"]
    assert r["stop_latency"] == pytest.approx(14.0)


def test_t4_stops_but_does_not_answer():
    r = t4([(3.5, 6.5)])
    assert r["stopped"] and not r["responded"] and r["latency"] is None


def test_t4_precondition_failure():
    assert not t4([(3.5, 5.0)])["precondition"]
    assert not t4([], failed=True)["precondition"]


# ---- T5 ----

def test_t5_counts_short_reactions_and_where_they_fall():
    s = step(0.0, 30.0, pauses=[(5.0, 5.6), (12.0, 12.6), (20.0, 20.6)])
    tomoko = [(5.1, 5.5), (8.0, 8.4), (12.1, 12.4), (12.9, 13.2), (22.0, 25.0)]
    r = score_timing("T5", [s], tomoko)
    assert r["count"] == 4
    assert r["per_minute"] == pytest.approx(8.0)
    assert r["in_gap"] == 2 and r["overlapped"] == 2
    assert r["bursts"] == 1
    assert r["main_interruptions"] == 1


def test_t5_silent_listener():
    r = score_timing("T5", [step(0.0, 30.0)], [])
    assert r["count"] == 0 and r["main_interruptions"] == 0


# ---- R1 ----

def test_double_answer_is_flagged():
    r = score_timing("T1", [step(1.0, 3.0)], [(3.5, 5.5), (8.0, 10.0)])
    assert r["double_answer"]


def test_one_answer_with_a_short_breath_is_not_double():
    r = score_timing("T1", [step(1.0, 3.0)], [(3.5, 5.5), (6.2, 8.0)])
    assert not r["double_answer"]
