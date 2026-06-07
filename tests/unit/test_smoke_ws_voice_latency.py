from __future__ import annotations

import numpy as np
import pytest

from _tools.smoke_ws_voice_latency import (
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    WsLatencyRecorder,
    build_audio_chunks,
    build_metrics_ms,
)


@pytest.mark.unit
def test_build_audio_chunks_appends_trailing_silence_chunks() -> None:
    voice = np.ones(CHUNK_SAMPLES + 10, dtype=np.float32)

    chunks = build_audio_chunks(voice, silence_ms=1000)

    voice_chunks = [chunk for chunk in chunks if chunk.is_voice]
    silence_chunks = [chunk for chunk in chunks if not chunk.is_voice]
    assert len(voice_chunks) == 2
    assert len(silence_chunks) == 32
    assert voice_chunks[0].samples.size == CHUNK_SAMPLES
    assert voice_chunks[1].samples.size == CHUNK_SAMPLES
    assert voice_chunks[1].samples[10:].max() == 0.0
    assert all(chunk.samples.size == CHUNK_SAMPLES for chunk in silence_chunks)
    assert all(float(chunk.samples.max()) == 0.0 for chunk in silence_chunks)


@pytest.mark.unit
def test_build_metrics_ms_uses_last_voice_chunk_as_human_wait_start() -> None:
    timestamps = {
        "audio_send_started": 10.0,
        "last_voice_chunk_sent": 11.0,
        "silence_send_completed": 12.2,
        "transcript_final": 12.5,
        "first_reply_text": 13.0,
        "first_binary_audio": 13.4,
    }

    metrics = build_metrics_ms(timestamps)

    assert metrics["voice_end_to_transcript_final"] == 1500.0
    assert metrics["voice_end_to_first_reply_text"] == 2000.0
    assert metrics["voice_end_to_first_binary_audio"] == 2400.0
    assert metrics["transcript_to_first_reply_text"] == 500.0
    assert metrics["transcript_to_first_binary_audio"] == 900.0
    assert metrics["audio_send_start_to_first_binary_audio"] == 3400.0
    assert metrics["silence_done_to_first_binary_audio"] == 1200.0


@pytest.mark.unit
def test_recorder_captures_transcript_reply_and_first_binary_audio_once() -> None:
    recorder = WsLatencyRecorder(started_at=100.0)
    recorder.mark("last_voice_chunk_sent", 101.0)

    recorder.observe_json({"type": "transcript_final", "text": "トモコ、聞こえる？"}, now=101.4)
    recorder.observe_json({"type": "reply_text", "delta": "うん"}, now=101.8)
    recorder.observe_json({"type": "reply_text", "delta": "、聞こえるよ。"}, now=102.0)
    recorder.observe_binary_audio(b"RIFFfake", now=102.2)
    recorder.observe_binary_audio(b"more", now=102.5)
    recorder.observe_json({"type": "reply_done"}, now=103.0)

    assert recorder.transcript_text == "トモコ、聞こえる？"
    assert recorder.reply_text == "うん、聞こえるよ。"
    assert recorder.binary_audio_chunks == 2
    assert recorder.binary_audio_bytes == len(b"RIFFfakemore")
    assert recorder.timestamps["first_reply_text"] == pytest.approx(101.8)
    assert recorder.timestamps["first_binary_audio"] == pytest.approx(102.2)
    assert recorder.metrics_ms()["voice_end_to_first_binary_audio"] == 1200.0


@pytest.mark.unit
def test_silence_chunk_default_matches_browser_sample_rate_contract() -> None:
    chunks = build_audio_chunks(np.zeros(1, dtype=np.float32), silence_ms=1200)

    assert SAMPLE_RATE == 16000
    assert CHUNK_SAMPLES == 512
    assert sum(1 for chunk in chunks if not chunk.is_voice) == 38
