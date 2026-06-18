from __future__ import annotations

import json
import wave
from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.debug_recording import DebugAudioRecorder
from server.shared.models import SpeechSegment, Transcript


class FakeTranscriber:
    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        return Transcript(
            text="トモコ、今日の予定を確認して",
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-12.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


@pytest.mark.unit
async def test_debug_recorder_writes_wav_and_metadata(tmp_path) -> None:
    recorder = DebugAudioRecorder(root=tmp_path, transcriber=None)

    started = recorder.start(kind="noise", duration_ms=64)
    assert started["type"] == "debug_recording_started"
    assert recorder.add_chunk(np.ones(512, dtype=np.float32).tobytes()) is False
    assert recorder.add_chunk(np.ones(512, dtype=np.float32).tobytes()) is True
    result = await recorder.stop()

    assert result.kind == "noise"
    assert result.sample_count == 1024
    assert result.transcript is None
    with wave.open(str(result.wav_path), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 1024
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["recording_id"] == result.recording_id


@pytest.mark.unit
async def test_debug_recorder_transcribes_read_aloud_recording(tmp_path) -> None:
    recorder = DebugAudioRecorder(root=tmp_path, transcriber=FakeTranscriber())
    expected = "トモコ、今日の予定を確認して。"

    recorder.start(kind="read_aloud", duration_ms=32, expected_text=expected)
    recorder.add_chunk(np.ones(512, dtype=np.float32).tobytes())
    result = await recorder.stop()

    assert result.kind == "read_aloud"
    assert result.expected_text == expected
    assert result.transcript == "トモコ、今日の予定を確認して"
    assert result.stt_elapsed_ms is not None
