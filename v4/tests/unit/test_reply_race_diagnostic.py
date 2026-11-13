"""MOCK: 時刻制御診断で、初回音声の保留と既発声の停止を区別する。"""

from __future__ import annotations

import json

import pytest

from engine import baseline
from scripts.diagnose_reply_race import main, run_diagnostic

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("case,discard,stop,speaking", [
    ("before_resume", False, True, False),
    ("during_grace", True, False, None),
    ("after_sustained", True, False, None),
])
async def test_records_the_current_three_release_orders(
    case: str, discard: bool, stop: bool, speaking: bool | None,
) -> None:
    report = await run_diagnostic((case,))
    row = report["cases"][0]
    assert report["mock"] is True
    assert row["case"] == case
    assert row["discard_reply"] is discard
    assert row["stop"] is stop
    assert row["first_send_user_speaking"] is speaking
    assert row["first_vad_end_s"] == pytest.approx(1.408)
    assert row["resume_s"] == pytest.approx(1.728)
    assert row["resumed_sustained_s"] == pytest.approx(2.24)
    if discard:
        assert row["first_send_s"] is None
        if case == "after_sustained":
            assert row["first_tts_audio_s"] is None
        else:
            assert row["first_tts_audio_s"] == pytest.approx(row["tts_release_s"])
    else:
        assert row["first_send_s"] == pytest.approx(row["tts_release_s"])
        assert row["first_tts_audio_s"] == pytest.approx(row["tts_release_s"])


async def test_grace_case_never_sends_first_reply_before_sustained_discards_it() -> None:
    row = (await run_diagnostic(("during_grace",)))["cases"][0]
    assert (row["first_vad_end_s"] < row["resume_s"] < row["tts_release_s"]
            < row["resumed_sustained_s"])
    sends = [e for e in row["events"] if e["event"] == "send_audio" and e["generation"] == 0]
    stops = [e for e in row["events"] if e["event"] == "stop"]
    assert not sends and not stops
    assert row["discard_reply"] is True
    assert row["first_tts_audio_s"] == pytest.approx(row["tts_release_s"])
    assert row["first_send_s"] is None


async def test_late_release_cancels_first_tts_and_carries_the_original_utterance() -> None:
    rows = (await run_diagnostic(("during_grace", "after_sustained")))["cases"]
    grace, late = rows
    cancelled = [e for e in late["events"] if e["event"] == "mock_tts_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["mic_s"] < late["tts_release_s"]
    assert late["stt_input_samples"] == grace["stt_input_samples"]
    assert not [e for e in late["events"] if e["event"] == "send_audio" and e["generation"] == 0]
    assert [e for e in late["events"] if e["event"] == "send_audio" and e["generation"] == 1]


async def test_diagnostic_is_deterministic_and_restores_the_session_clock() -> None:
    original = baseline.asyncio
    first = await run_diagnostic()
    assert baseline.asyncio is original
    assert first == await run_diagnostic()
    assert baseline.asyncio is original
    for case in first["cases"]:
        for event in case["events"]:
            assert all(isinstance(value, (int, float, bool)) or value is None
                       for key, value in event.items() if key != "event")


async def test_unknown_case_is_rejected_without_changing_the_session_clock() -> None:
    original = baseline.asyncio
    with pytest.raises(ValueError, match="unknown release case"):
        await run_diagnostic(("not-a-case",))
    assert baseline.asyncio is original


def test_cli_writes_numeric_results_to_json(tmp_path, capsys) -> None:
    output = tmp_path / "diagnostic.json"
    main(["--case", "during_grace", "--out", str(output)])
    report = json.loads(output.read_text())
    assert report["schema_version"] == 1
    assert report["cases"][0]["first_send_user_speaking"] is None
    assert report["cases"][0]["discard_reply"] is True
    assert report["config"]["maai"] is False
    assert report["config"]["barge_in"] is True
    assert capsys.readouterr().out == ""
