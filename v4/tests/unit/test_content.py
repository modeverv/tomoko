import pytest

from bench.score.content import build_prompt, score_content
from bench.score.segments import merge

pytestmark = pytest.mark.unit


def run(private=False, failed=False):
    return {"private": private, "precondition_failed": failed,
            "steps": [{"start": 1.0, "end": 3.0}]}


def never(*a, **k):
    raise AssertionError("呼ばれてはいけない")


def test_private_runs_are_never_sent_to_the_judge():
    r = score_content(run(private=True), ["a"], {}, [(3.5, 5.0)], None, judge=never, transcribe=never)
    assert r == {"judged": "skipped_private"}


def test_no_reply_scores_zero_without_calling_the_judge():
    r = score_content(run(), ["a"], {}, [(0.0, 2.0)], None, judge=never, transcribe=never)
    assert r["judged"] == "no_reply" and r["score"] == 0 and r["correct"] is False


def test_reply_is_transcribed_judged_and_timed():
    seen = {}

    def judge(prompt):
        seen["prompt"] = prompt
        return {"score": 2, "correct": True, "answer_segment": 1, "reason": "合っている"}

    segs = [(0.5, 0.8), (3.4, 4.0), (7.0, 9.0)]
    r = score_content(run(), ["富士山の高さは？"], {"answer": "3776メートル"}, segs, None,
                      judge=judge, transcribe=lambda wav, s: ["調べますね", "3776メートルです"])
    assert "[1] 3776メートルです" in seen["prompt"] and "期待する答え" in seen["prompt"]
    assert r["score"] == 2 and r["correct"]
    assert r["first_reaction"] == pytest.approx(0.4)
    assert r["answer_time"] == pytest.approx(4.0)
    assert r["longest_silence"] == pytest.approx(3.0)


def test_prompt_lists_user_lines_in_order():
    p = build_prompt(["一つ目", "二つ目"], ["返事"], {"topic": "大阪"})
    assert p.index("一つ目") < p.index("二つ目") < p.index("[0] 返事") < p.index("大阪")


def test_merge_joins_short_gaps_only():
    assert merge([(0.0, 1.0), (1.2, 2.0), (2.5, 3.0)], gap=0.4) == [(0.0, 2.0), (2.5, 3.0)]
