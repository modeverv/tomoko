import pytest

from bench.scenario import Scenario, Step

pytestmark = pytest.mark.unit


def test_parts_split_text_and_pauses():
    s = Step("来週の[pause 1.2]金曜なんだけど", at=1.0)
    assert s.parts() == ["来週の", 1.2, "金曜なんだけど"]
    assert s.text() == "来週の金曜なんだけど"


def test_step_needs_exactly_one_trigger():
    with pytest.raises(ValueError):
        Step("a")
    with pytest.raises(ValueError):
        Step("a", at=1.0, after_reply=1.0)


def test_interrupt_scenario_must_use_tomoko_trigger():
    with pytest.raises(ValueError):
        Scenario("x", "T4", (Step("a", at=1.0), Step("b", at=5.0)))
    Scenario("x", "T4", (Step("a", at=1.0), Step("b", after_tomoko_speaks=2.0)))


def test_first_step_must_be_timed():
    with pytest.raises(ValueError):
        Scenario("x", "T1", (Step("a", after_reply=1.0),))
