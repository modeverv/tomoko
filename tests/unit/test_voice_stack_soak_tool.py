from __future__ import annotations

import pytest

from _tools.soak_stt_backends import RunningStats
from _tools.soak_voice_stack_scenarios import (
    ScenarioStats,
    VoiceStackScenario,
    build_default_scenarios,
    summary_payload,
)
from server.shared.config import BackendSpec


@pytest.mark.unit
def test_build_default_scenarios_compares_only_stt_lane() -> None:
    scenarios = build_default_scenarios(
        mlx_stt_backend="local_whisper_mlx_small",
        coreml_stt_backend="local_whisperkit_serve_small",
        tts_backend="supertonic_coreml_f1",
        conversation_backend="local_lfm25_12b_jp_mlx",
    )

    assert [scenario.name for scenario in scenarios] == ["mlx_stt_stack", "coreml_stt_stack"]
    assert scenarios[0].load_key == scenarios[1].load_key
    assert scenarios[0].stt_backend != scenarios[1].stt_backend


@pytest.mark.unit
def test_scenario_stats_tracks_stt_and_load_windows() -> None:
    scenario = VoiceStackScenario(
        name="mlx_stt_stack",
        stt_backend="local_whisper_mlx_small",
        tts_backend="supertonic_coreml_f1",
        conversation_backend="local_lfm25_12b_jp_mlx",
    )
    stats = ScenarioStats(
        scenario=scenario,
        stt_spec=BackendSpec(name=scenario.stt_backend, type="mlx_whisper"),
        started_at="2026-05-25T00:00:00+00:00",
        stt_stats=RunningStats(),
        load_stats=RunningStats(),
        recent_stt_ms=[],
        recent_load_ms=[],
    )

    stats.add_run(100.0, 150.0, "one", recent_limit=2)
    stats.add_run(120.0, 170.0, "two", recent_limit=2)
    stats.add_run(140.0, 190.0, "three", recent_limit=2)
    payload = summary_payload(stats, 10.0)

    assert stats.recent_stt_ms == [120.0, 140.0]
    assert stats.recent_load_ms == [170.0, 190.0]
    assert stats.last_text == "three"
    assert payload["stt_avg_ms"] == 120.0
    assert payload["load_avg_ms"] == 170.0
    assert payload["stt_recent_p95_ms"] == 140.0
    assert payload["load_recent_p95_ms"] == 190.0
