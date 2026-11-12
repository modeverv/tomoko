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


# ---- 追加の指標（2026-11-13） ----

def test_no_main_distinguishes_backchannel_only_from_main_reply():
    for axis in ("T1", "T2", "T3", "T4", "T5", "C2", "C3"):
        steps = [step(1.0, 3.0), step(6.0, 8.0)] if axis in ("T3", "T4", "C2") else [step(1.0, 3.0)]
        r_silent = score_timing(axis, steps, [])
        assert r_silent["silent"] and r_silent["no_main"]

        r_short = score_timing(axis, steps, [(1.5, 1.9)])
        assert not r_short["silent"] and r_short["no_main"]

        r_main = score_timing(axis, steps, [(3.5, 5.5)])
        assert not r_main["silent"] and not r_main["no_main"]


def test_c2_early_first_detects_main_started_during_first_step():
    steps = [step(1.0, 4.0, pauses=[(2.0, 2.6)]), step(7.0, 9.0)]
    r_early = score_timing("C2", steps, [(2.2, 3.6), (9.5, 11.5)])
    assert r_early["early_first"] and not r_early["early"] and r_early["responded"]

    # 1 つ目の発話の途中の短い相づちは early_first ではない
    r_bc = score_timing("C2", steps, [(2.2, 2.6), (4.5, 6.2), (9.5, 11.5)])
    assert not r_bc["early_first"] and not r_bc["early"]

    # 1 つ目の言い終わり直前（ONSET_TOLERANCE_S 以内）の本応答は early_first ではない
    r_tol = score_timing("C2", steps, [(3.85, 5.5), (9.5, 11.5)])
    assert not r_tol["early_first"]


def test_early_first_requires_at_least_two_steps_or_multi_step_turn():
    # 1 発話の T1 で早すぎた場合は early のみ True で early_first は False
    r_one = score_timing("T1", [step(1.0, 3.0)], [(2.0, 6.0)])
    assert r_one["early"] and not r_one["early_first"]

    # 2 発話ある T1・C3 では steps[0] の早すぎを early_first で見る
    two = [step(1.0, 3.0), step(6.0, 8.0)]
    assert score_timing("T1", two, [(1.5, 3.0), (8.5, 10.5)])["early_first"]
    assert score_timing("C3", two, [(1.5, 3.0), (8.5, 10.5)])["early_first"]


def test_t5_final_responded_and_final_latency():
    talk = step(0.0, 30.0, pauses=[(5.0, 5.7), (15.0, 15.7)])
    # 相づちのあと、語り終えて 0.8 秒後に本応答
    r_ok = score_timing("T5", [talk], [(5.1, 5.5), (15.1, 15.5), (30.8, 33.0)])
    assert r_ok["final_responded"] and r_ok["final_latency"] == pytest.approx(0.8)
    assert not r_ok["no_main"]

    # 相づちだけで最後に本応答がない
    r_bc_only = score_timing("T5", [talk], [(5.1, 5.5), (15.1, 15.5)])
    assert not r_bc_only["final_responded"] and r_bc_only["final_latency"] is None
    assert not r_bc_only["silent"] and r_bc_only["no_main"]

    # 言い終わり直前（ONSET_TOLERANCE_S 以内）の本応答は遅れ 0.0
    r_tol = score_timing("T5", [talk], [(29.9, 32.0)])
    assert r_tol["final_responded"] and r_tol["final_latency"] == 0.0

    # 窓（5.0 秒）を過ぎてからの本応答は不応答
    r_late = score_timing("T5", [talk], [(35.5, 38.0)])
    assert not r_late["final_responded"] and r_late["final_latency"] is None


def test_shorts_during_user_in_t1_and_c2():
    # T1: 発話中の短い声 2 つ、発話後の短い声 1 つ、本応答 1 つ
    r_t1 = score_timing("T1", [step(1.0, 5.0)], [(1.8, 2.2), (3.2, 3.6), (5.2, 5.5), (5.8, 8.0)])
    assert r_t1["shorts_during_user"] == 2

    # C2: 1 つ目の発話中 1 つ、発話間の短い声 1 つ、2 つ目の発話中 2 つ
    steps = [step(1.0, 4.0), step(7.0, 10.0)]
    tomoko = [(2.0, 2.4), (4.5, 6.2), (6.4, 6.7), (7.5, 7.9), (9.0, 9.3), (10.5, 12.5)]
    r_c2 = score_timing("C2", steps, tomoko)
    assert r_c2["shorts_during_user"] == 3

