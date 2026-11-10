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
