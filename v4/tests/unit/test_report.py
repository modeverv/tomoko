import pytest

from bench.report import render, summarize
from bench.score.timing import StepTime, score_timing

pytestmark = pytest.mark.unit


def run(axis, tomoko, steps=None, failed=False, error=None, content=None, stale=0):
    steps = steps or [StepTime(1.0, 3.0, ((1.0, 3.0),), ())]
    r = {"id": "x", "axis": axis, "error": error, "precondition_failed": failed,
         "stale_chunks": stale}
    if not error:
        r["timing"] = score_timing(axis, steps, tomoko, failed)
    if content:
        r["content"] = content
    return r


def test_silent_runs_stay_in_the_denominator():
    s = summarize([run("T1", [(3.5, 5.5)]), run("T1", [])])["T1"]
    assert s["n"] == 2 and s["silent"] == 1
    assert s["response_rate"] == 0.5
    assert s["latency_p50"] == pytest.approx(0.5)


def test_precondition_failures_are_counted_apart():
    two = [StepTime(1.0, 3.0, ((1.0, 3.0),), ()), StepTime(6.0, 8.0, ((6.0, 8.0),), ())]
    s = summarize([run("T4", [(3.5, 6.3), (8.6, 11.0)], two), run("T4", [], two[:1], failed=True)])["T4"]
    assert s["n"] == 2 and s["valid"] == 1 and s["precondition_failed"] == 1
    assert s["stop_rate"] == 1.0 and s["response_rate"] == 1.0


def test_connection_errors_are_reported():
    s = summarize([run("T1", [], error="boom"), run("T1", [(3.5, 5.5)])])["T1"]
    assert s["n"] == 2 and s["errors"] == 1 and s["response_rate"] == 1.0


def test_content_and_invariants():
    runs = [
        run("T1", [(3.5, 5.5)], content={"judged": "ok", "score": 2, "correct": False}),
        run("T1", [], content={"judged": "no_reply", "score": 0, "correct": False}),
        run("C2", [(3.5, 5.5), (8.0, 10.0)], content={"judged": "ok", "score": 2, "correct": True}, stale=3),
        run("C2", [], content={"judged": "skipped_private"}),
    ]
    s = summarize(runs)
    assert s["C1"] == {"judged": 2, "score_mean": 1.0}
    assert s["C2"]["accuracy"] == 1.0 and s["C2"]["judged"] == 1
    assert s["R1"] == {"stale_chunks": 3, "double_answers": 1}


def test_render_shows_difference_from_previous():
    now = {"T1": {"response_rate": 0.9, "n": 10}}
    text = render(now, {"T1": {"response_rate": 0.5, "n": 10}})
    assert "| T1 | 応答率 | 0.900 | 0.500 | +0.400 |" in text
    assert "| T1 | 件数 | 10 | 10 |  |" in text


def test_added_regression_metrics_in_summary_and_render():
    two = [StepTime(1.0, 4.0, ((1.0, 4.0),), ()), StepTime(7.0, 9.0, ((7.0, 9.0),), ())]
    talk = [StepTime(0.0, 30.0, ((0.0, 30.0),), ())]
    runs = [
        # T1: 相づちのみで本応答なし
        run("T1", [(1.5, 1.9)]),
        # C2: 1 件目は最初の発話で早すぎ＋短い声 2 回、2 件目は正常応答
        run("C2", [(1.5, 1.8), (2.2, 3.5), (7.5, 7.8), (9.5, 11.5)], two),
        run("C2", [(4.5, 6.0), (9.5, 11.5)], two),
        # T5: 1 件目は最後に本応答あり（遅れ 0.6s）、2 件目は相づちのみ
        run("T5", [(5.0, 5.4), (30.6, 32.5)], talk),
        run("T5", [(5.0, 5.4)], talk),
    ]
    s = summarize(runs)
    assert s["T1"]["silent"] == 0 and s["T1"]["no_main"] == 1
    assert s["T1"]["shorts_during_user"] == 1
    assert s["C2"]["early_first_rate"] == pytest.approx(0.5)
    assert s["C2"]["shorts_during_user"] == 2
    assert s["T5"]["no_main"] == 1
    assert s["T5"]["final_response_rate"] == pytest.approx(0.5)
    assert s["T5"]["final_latency_p50"] == pytest.approx(0.6)

    text = render(s)
    for label in ("本応答なしの回", "最初の発話で早すぎた割合", "発話中の短い反応",
                  "最後の応答率", "最後の遅れ 中央値(秒)"):
        assert label in text

