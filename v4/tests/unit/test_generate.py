from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from bench.audio import read_wav, write_wav
from bench.generate import __main__ as generate
from bench.generate import tts
from bench.generate.__main__ import build
from bench.scenario import Manifest, Scenario, Step

pytestmark = pytest.mark.unit


def fake_synth(text: str, voice: str, cache: Path) -> np.ndarray:
    return np.full(1600 * len(text), 0.1, dtype=np.float32)  # 1 文字 0.1 秒


def test_build_writes_audio_and_annotation_with_pauses(tmp_path: Path) -> None:
    sc = Scenario("t2-x", "T2", (Step("あいう[pause 1.5]えお", at=1.0),))
    m = build(sc, tmp_path / "t2-x", tmp_path / "cache", synth=fake_synth)
    a = m.audio[0]
    assert a.duration == pytest.approx(2.0)
    assert a.voiced == [[0.0, 0.3], [1.8, 2.0]]
    assert a.pauses == [[0.3, 1.8]]
    samples, sr = read_wav(tmp_path / "t2-x" / a.wav)
    assert sr == 16000 and len(samples) == 32000
    assert np.all(samples[4800:28800] == 0)
    assert Manifest.load(tmp_path / "t2-x") == m


def waveform(*sections: tuple[float, float]) -> np.ndarray:
    """長さ（秒）と振幅を指定した、境界が既知の合成テスト音。"""
    return np.concatenate([
        np.full(round(seconds * 16000), amplitude, dtype=np.float32)
        for seconds, amplitude in sections
    ])


@pytest.mark.parametrize(("parts", "expected"), [
    (["あの", 0.7, "それで", 1.2346, "続き"], "あの[[slnc 700]]それで[[slnc 1235]]続き"),
    (["句点。", 0.7, " 次。"], "句点。[[slnc 700]] 次。"),
    (["区切りなし"], "区切りなし"),
    (["あ", 0.1, 0.2, "い"], "あ[[slnc 100]][[slnc 200]]い"),
])
def test_one_pass_embeds_pauses_and_synthesizes_once(
    tmp_path: Path, parts: list[str | float], expected: str,
) -> None:
    calls = []
    samples = waveform((0.2, 0.1))

    def synth(text: str, voice: str, cache: Path) -> np.ndarray:
        calls.append((text, voice, cache))
        return samples

    result = tts.synthesize_one_pass(parts, "Kyoko", tmp_path, synth=synth)
    assert calls == [(expected, "Kyoko", tmp_path)]
    np.testing.assert_array_equal(result, samples)


def test_one_pass_cache_is_separate_and_preserves_audio_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    text = "あ[[slnc 700]]い"
    raw = waveform((0.05, 0), (0.1, 0.2), (0.7, 0), (0.2, -0.1), (0.05, 0))

    def say(args: list[str], *, check: bool) -> None:
        assert check is True
        calls.append(args)
        write_wav(Path(args[args.index("-o") + 1]), raw, 16000)

    monkeypatch.setattr(tts.subprocess, "run", say)
    old = tts.synthesize(text, "Kyoko", tmp_path)
    first = tts.synthesize_one_pass(["あ", 0.7, "い"], "Kyoko", tmp_path)
    second = tts.synthesize_one_pass(["あ", 0.7, "い"], "Kyoko", tmp_path)
    assert len(calls) == 2
    assert all(args[-1] == text and "--data-format=LEI16@16000" in args for args in calls)
    old_key = hashlib.sha1(f"Kyoko|{text}".encode()).hexdigest()[:20]
    assert (tmp_path / f"{old_key}.wav").exists()
    assert len(list(tmp_path.glob("*.wav"))) == 2
    np.testing.assert_array_equal(first, old)
    np.testing.assert_array_equal(second, first)
    assert len(first) == 16000
    assert abs(first[0]) > 0 and abs(first[-1]) > 0
    assert np.max(np.abs(first)) == pytest.approx(0.5)


def test_one_pass_annotates_actual_voice_and_longest_internal_gaps(tmp_path: Path) -> None:
    sc = Scenario("t2-one", "T2", (Step("あ[pause 0.7]い[pause 1.0]う", at=1.0),))
    samples = waveform(
        (0.2, 0.1), (0.1, 0.001), (0.2, 0.1),  # 短い音量不足のすき間をつなぐ
        (0.2, 0), (0.3, 0.1), (0.7, 0), (0.3, 0.1),
        (0.15, 0), (0.2, 0.1), (1.0, 0), (0.3, 0.1),
    )
    calls = []

    def synth(text: str, voice: str, cache: Path) -> np.ndarray:
        calls.append(text)
        return samples

    m = build(sc, tmp_path / sc.id, tmp_path / "cache", synth=synth, one_pass=True)
    a = m.audio[0]
    assert calls == ["あ[[slnc 700]]い[[slnc 1000]]う"]
    assert a.duration == 3.65
    assert a.voiced == [[0.0, 0.5], [0.7, 1.0], [1.7, 2.0], [2.15, 2.35], [3.35, 3.65]]
    assert a.pauses == [[1.0, 1.7], [2.35, 3.35]]
    assert m.one_pass is True
    assert Manifest.load(tmp_path / sc.id) == m
    saved, sr = read_wav(tmp_path / sc.id / a.wav)
    assert sr == 16000
    np.testing.assert_allclose(saved, samples, atol=1 / 32768)


def test_one_pass_shortens_each_pause_to_the_requested_length(tmp_path: Path) -> None:
    # 声の自然な間が足されて長くなった区切りを、台本の長さに縮める（v1 と無音の長さをそろえる）
    sc = Scenario("t5-short", "T5", (Step("あ。[pause 0.7]い、[pause 0.7]う", at=1.0),))
    samples = waveform((0.3, 0.1), (1.4, 0), (0.3, 0.1), (0.7, 0), (0.3, 0.1))
    m = build(sc, tmp_path / sc.id, tmp_path / "cache", synth=lambda *_: samples, one_pass=True)
    a = m.audio[0]
    assert a.pauses == [[0.3, 1.0], [1.3, 2.0]]
    assert a.voiced == [[0.0, 0.3], [1.0, 1.3], [2.0, 2.3]]
    assert a.duration == pytest.approx(2.3)
    saved, _ = read_wav(tmp_path / sc.id / a.wav)
    assert len(saved) == round(2.3 * 16000)
    assert np.all(saved[round(0.3 * 16000):round(1.0 * 16000)] == 0)


def test_one_pass_keeps_a_pause_shorter_than_requested() -> None:
    samples = waveform((0.3, 0.1), (0.6, 0), (0.3, 0.1))
    out, pauses = tts.shorten_pauses(samples, [[0.3, 0.9]], [0.7])
    np.testing.assert_array_equal(out, samples)
    assert pauses == [[0.3, 0.9]]


@pytest.mark.parametrize("gap_samples", [2399, 2400])
def test_one_pass_voice_merge_is_strictly_below_150ms(tmp_path: Path, gap_samples: int) -> None:
    sc = Scenario("t1-gap", "T1", (Step("自然な間", at=1.0),))
    samples = waveform((0.2, 0.1), (gap_samples / 16000, 0), (0.2, 0.1))
    m = build(sc, tmp_path / sc.id, tmp_path / "cache",
              synth=lambda *_: samples, one_pass=True)
    assert len(m.audio[0].voiced) == (1 if gap_samples < 2400 else 2)
    assert m.audio[0].pauses == []


@pytest.mark.parametrize("gap_samples", [12800, 12799])
def test_one_pass_validates_pause_before_rounding_with_context(
    tmp_path: Path, gap_samples: int,
) -> None:
    sc = Scenario("t2-short", "T2", (
        Step("最初", at=1.0),
        Step("あ[pause 0.7]い[pause 1.0]う", after_reply=1.0),
    ))
    samples = waveform((0.2, 0.1), (0.7, 0), (0.2, 0.1),
                       (gap_samples / 16000, 0), (0.2, 0.1))

    def synth(text: str, voice: str, cache: Path) -> np.ndarray:
        return waveform((0.2, 0.1)) if text == "最初" else samples

    if gap_samples < 12800:
        with pytest.raises(ValueError, match=r"t2-short.*step.?2.*pause.?2.*1\.0"):
            build(sc, tmp_path / sc.id, tmp_path / "cache", synth=synth, one_pass=True)
    else:
        m = build(sc, tmp_path / sc.id, tmp_path / "cache", synth=synth, one_pass=True)
        assert m.audio[1].pauses == [[0.2, 0.9], [1.1, 1.9]]


def test_one_pass_reports_missing_pause(tmp_path: Path) -> None:
    sc = Scenario("t2-missing", "T2", (Step("あ[pause 0.7]い", at=1.0),))
    with pytest.raises(ValueError, match=r"t2-missing.*step.?1.*pause.?1"):
        build(sc, tmp_path / sc.id, tmp_path / "cache",
              synth=lambda *_: waveform((0.3, 0.1)), one_pass=True)


@pytest.mark.parametrize("gap_samples", [12800, 12801])
def test_one_pass_does_not_round_requested_minimum_down(gap_samples: int) -> None:
    samples = waveform((0.2, 0.1), (gap_samples / 16000, 0), (0.2, 0.1))
    if gap_samples == 12800:
        with pytest.raises(ValueError, match=r"fractional.*pause.?1"):
            tts.annotate_one_pass(samples, [1.00001], label="fractional step 1")
    else:
        _, pauses = tts.annotate_one_pass(samples, [1.00001], label="fractional step 1")
        assert len(pauses) == 1


def test_one_pass_pause_candidates_exclude_outer_silence() -> None:
    samples = waveform((0.9, 0), (0.2, 0.1), (0.7, 0), (0.2, 0.1), (1.0, 0))
    voiced, pauses = tts.annotate_one_pass(samples, [0.7], label="edges step 1")
    assert voiced == [[0.9, 1.1], [1.8, 2.0]]
    assert pauses == [[1.1, 1.8]]


@pytest.mark.parametrize("requested", [[], [0.7]])
def test_one_pass_rejects_all_silent_audio(requested: list[float]) -> None:
    with pytest.raises(ValueError, match="silent step 1"):
        tts.annotate_one_pass(np.zeros(1600), requested, label="silent step 1")


@pytest.mark.parametrize("explicit_default", [False, True])
def test_default_generation_keeps_legacy_wav_and_annotations(
    tmp_path: Path, explicit_default: bool,
) -> None:
    sc = Scenario("t2-legacy", "T2", (Step("あ[pause 0.7]い", at=1.0),), voice="Kyoko")
    calls = []
    pieces = {"あ": waveform((0.1, 0.1), (0.2, 0), (0.1, 0.2)),
              "い": waveform((0.2, 0.2))}

    def synth(text: str, voice: str, cache: Path) -> np.ndarray:
        calls.append((text, voice, cache))
        return pieces[text]

    kwargs = {"one_pass": False} if explicit_default else {}
    m = build(sc, tmp_path / sc.id, tmp_path / "cache", synth=synth, **kwargs)
    assert calls == [("あ", "Kyoko", tmp_path / "cache"), ("い", "Kyoko", tmp_path / "cache")]
    assert m.audio[0].duration == 1.3
    assert m.audio[0].voiced == [[0.0, 0.4], [1.1, 1.3]]
    assert m.audio[0].pauses == [[0.4, 1.1]]
    reference = tmp_path / "legacy.wav"
    write_wav(reference, np.concatenate([pieces["あ"], np.zeros(11200), pieces["い"]]), 16000)
    assert (tmp_path / sc.id / "step0.wav").read_bytes() == reference.read_bytes()
    raw = json.loads((tmp_path / sc.id / "scenario.json").read_text())
    assert raw["one_pass"] is False


@pytest.mark.parametrize("one_pass", [False, True])
def test_cli_selects_one_pass_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_pass: bool,
) -> None:
    sc = Scenario("t2-cli", "T2", (Step("あ[pause 0.7]い", at=1.0),))
    calls = []

    def synth(text: str, voice: str, cache: Path) -> np.ndarray:
        calls.append(text)
        if "[[slnc" in text:
            return waveform((0.1, 0.1), (0.7, 0), (0.1, 0.1))
        return waveform((0.1, 0.1))

    def fake_build(scenario: Scenario, out: Path, cache: Path, **kwargs: bool) -> Manifest:
        return build(scenario, out, cache, synth=synth, **kwargs)

    monkeypatch.setattr(generate, "build", fake_build)
    monkeypatch.setattr(generate, "load_scenarios", lambda _: [sc])
    argv = ["bench.generate", "unused", str(tmp_path), "--cache", str(tmp_path / "cache")]
    monkeypatch.setattr(sys, "argv", argv + (["--one-pass"] if one_pass else []))
    assert generate.main() == 0
    assert calls == (["あ[[slnc 700]]い"] if one_pass else ["あ", "い"])
    assert Manifest.load(tmp_path / sc.id).one_pass is one_pass


@pytest.mark.parametrize("private", [False, True])
def test_old_manifest_defaults_to_split_synthesis(tmp_path: Path, private: bool) -> None:
    raw = {
        "scenario": {"id": "old", "axis": "T1", "steps": [{"say": "あ", "at": 1.0}],
                     "expect": {}, "voice": "Kyoko"},
        "audio": [{"wav": "step0.wav", "duration": 0.1, "voiced": [[0.0, 0.1]], "pauses": []}],
    }
    if private:
        raw["private"] = True
    (tmp_path / "scenario.json").write_text(json.dumps(raw))
    m = Manifest.load(tmp_path)
    assert m.one_pass is False
    assert m.private is private
    assert Manifest(m.scenario, m.audio, private).private is private
