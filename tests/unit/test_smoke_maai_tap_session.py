from __future__ import annotations

import json
from pathlib import Path

import pytest

from _tools.smoke_maai_tap_session import run_smoke


@pytest.mark.unit
async def test_smoke_maai_tap_session_routes_say_audio_through_session(
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProc:
        del kwargs
        calls.append(tuple(args))
        output_path = Path(args[args.index("-o") + 1])
        output_path.write_bytes(b"RIFFfakeWAVE")
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    summary = await run_smoke(
        text="うん、聞こえるよ。",
        style="happy",
        voice="Kyoko",
        user_sine_sec=0.064,
    )

    assert summary["say_invoked"] is True
    assert summary["sent_audio_chunks"] == 1
    assert summary["tomoko_tap_chunks"] == 1
    assert summary["tomoko_tap_bytes"] == len(b"RIFFfakeWAVE")
    assert summary["user_tap_chunks"] >= 1
    assert summary["events"][0]["type"] == "audio_start"
    assert calls[0][:4] == ("say", "-v", "Kyoko", "-r")


@pytest.mark.unit
async def test_smoke_maai_tap_session_writes_json_summary(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProc:
        del kwargs
        output_path = Path(args[args.index("-o") + 1])
        output_path.write_bytes(b"RIFFfakeWAVE")
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    output_path = tmp_path / "summary.json"

    summary = await run_smoke(
        text="なるほどね。",
        style="neutral",
        voice="Kyoko",
        output_path=output_path,
    )

    loaded = json.loads(output_path.read_text())
    assert loaded == summary
    assert loaded["tomoko_tap_chunks"] == 1
