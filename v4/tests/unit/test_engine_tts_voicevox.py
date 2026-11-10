import io
import json
import wave

import httpx
import numpy as np
import pytest

from engine.front_llm import PhraseSplitter
from engine.speaker.tts_voicevox import VoicevoxTts

pytestmark = pytest.mark.unit


def wav_bytes(samples: np.ndarray, rate: int = 24_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def fake_engine(seen: list, audio: np.ndarray) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params), request.content))
        if request.url.path == "/audio_query":
            return httpx.Response(200, json={"prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
                                             "speedScale": 1.0, "outputSamplingRate": 48000,
                                             "outputStereo": True})
        return httpx.Response(200, content=wav_bytes(audio))
    return httpx.MockTransport(handle)


async def test_voicevox_asks_for_a_query_then_synthesizes_24k_mono():
    seen, audio = [], np.linspace(-0.5, 0.5, 2400, dtype=np.float32)
    async with httpx.AsyncClient(transport=fake_engine(seen, audio)) as client:
        tts = VoicevoxTts(client, "http://vv", speaker=8)
        out = await tts.synthesize("こんにちは。")
    assert [s[0] for s in seen] == ["/audio_query", "/synthesis"]
    assert seen[0][1] == {"text": "こんにちは。", "speaker": "8"}
    query = json.loads(seen[1][2])
    assert query["outputSamplingRate"] == 24_000 and query["outputStereo"] is False
    assert query["prePhonemeLength"] == 0.1  # 指定しなければ、エンジンの値のまま
    assert out.dtype == np.float32 and len(out) == 2400
    assert np.allclose(out, audio, atol=1e-3)


async def test_voicevox_sets_the_silences_when_asked():
    seen = []
    async with httpx.AsyncClient(transport=fake_engine(seen, np.zeros(10, dtype=np.float32))) as client:
        tts = VoicevoxTts(client, "http://vv", speaker=8, pre_silence_s=0.0, post_silence_s=0.05)
        await tts.synthesize("はい。")
    query = json.loads(seen[1][2])
    assert query["prePhonemeLength"] == 0.0 and query["postPhonemeLength"] == 0.05


# ---- 最初の句を短くする ----

def test_splitter_by_default_cuts_only_at_sentence_ends():
    s = PhraseSplitter()
    assert s.feed("そうですね、明日は晴れるみたいですよ。傘は") == ["そうですね、明日は晴れるみたいですよ。"]


def test_splitter_can_cut_the_first_phrase_at_the_first_comma():
    s = PhraseSplitter(first_cut_min_chars=4)
    assert s.feed("そうですね、明日は") == ["そうですね、"]
    # 2 つ目からは、文の切れ目まで待つ
    assert s.feed("晴れるみたいで、傘はいらないですよ。") == ["明日は晴れるみたいで、傘はいらないですよ。"]


def test_splitter_does_not_cut_a_first_phrase_that_is_too_short():
    s = PhraseSplitter(first_cut_min_chars=4)
    assert s.feed("あ、そうなんですね。") == ["あ、そうなんですね。"]


def test_splitter_first_cut_is_once_per_reply():
    s = PhraseSplitter(first_cut_min_chars=4)
    assert s.feed("はい。それなら、大丈夫です。") == ["はい。", "それなら、大丈夫です。"]


def test_config_reads_overrides_from_the_environment(monkeypatch):
    from engine.dto import EngineConfig

    monkeypatch.setenv("TOMOKO_TTS", "kokoro")
    monkeypatch.setenv("TOMOKO_FIRST_CUT_MIN_CHARS", "4")
    monkeypatch.setenv("TOMOKO_TTS_PRE_SILENCE_S", "0.0")
    monkeypatch.setenv("TOMOKO_VAD_SILENCE_S", "0.3")
    monkeypatch.setenv("TOMOKO_TRACE_DIR", "work/traces/x")
    cfg = EngineConfig.from_env()
    assert (cfg.tts, cfg.first_cut_min_chars, cfg.tts_pre_silence_s, cfg.vad_silence_s) == ("kokoro", 4, 0.0, 0.3)
    assert str(cfg.trace_dir) == "work/traces/x"
    assert EngineConfig().first_cut_min_chars is None
