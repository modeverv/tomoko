"""層をまたぐ型は、すべてここに置く。"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

IN_SR = 16_000  # マイクの音
OUT_SR = 24_000  # 声


@dataclass(frozen=True, slots=True)
class MaaiFrame:
    """入力フレーム終端の予測。2 要素の値は常に (ユーザー, Tomoko)。欠測は None。"""

    at_s: float
    p_now: tuple[float, float] | None = None
    p_future: tuple[float, float] | None = None
    p_bc: float | None = None
    p_bc_det_user: float | None = None
    vad_user: float | None = None
    vad_tomoko: float | None = None


@dataclass(frozen=True)
class Utterance:
    """VAD が切り出した、ユーザーの 1 発話。時刻は入力の先頭からの秒（入力の時計）。"""

    audio: np.ndarray
    start_s: float
    end_s: float


@dataclass(frozen=True)
class VadEvent:
    kind: str  # "start"、"sustained"（声が続いた）、"end"、"drop"（短すぎて捨てた）
    at_s: float
    utterance: Utterance | None = None


@dataclass(frozen=True)
class EngineConfig:
    llm_url: str = "http://127.0.0.1:8090/v1/chat/completions"
    llm_model: str = "mlx-community/gemma-4-E4B-it-4bit"
    llm_max_tokens: int = 200
    stt: str = "mlx"  # "mlx"（この機械）か "whispercpp"（stt_url のサーバー）
    stt_model: str = "mlx-community/whisper-large-v3-turbo"
    stt_url: str = "http://127.0.0.1:18091"
    tts: str = "voicevox"  # "voicevox" か "kokoro"
    tts_voice: str = "jf_alpha"  # Kokoro の声
    voicevox_url: str = "http://127.0.0.1:50021"
    voicevox_speaker: int = 8  # 春日部つむぎ（ノーマル）
    voicevox_streaming: bool = False  # 逐次に返す口（/streaming_synthesis）を使う
    tts_pre_silence_s: float | None = None  # 声の頭の無音。None はエンジンの値のまま
    first_cut_min_chars: int | None = None  # 最初の句を読点で切る。None は切らない
    vad_threshold: float = 0.5
    vad_silence_s: float = 0.4  # この長さの無音で、言い終わりとする
    vad_preroll_s: float = 0.5  # 発話の頭が欠けないように、手前を残す
    vad_min_voiced_s: float = 0.2  # これより短い音は発話にしない
    barge_in: bool = False  # ユーザーが話し始めたら、返事を止める
    barge_in_voiced_s: float = 0.5  # 声がこの長さ続いたら、割り込みとみなす。これより短い声は相づちとして聞き流す
    send_chunk_s: float = 0.2  # 声を送る断片の長さ
    # MaAI（P3: 言い終わりの判定、相づち、相づちで止まらない）。既定はすべて切。切なら MaAI を読み込まない
    maai: bool = False  # MaAI のセンサーを動かし、予測を記録する。これだけでは動きは変わらない
    maai_modes: str = "vap,bc,bc_det"  # 同じエンコーダー（Mimi、12.5 Hz、文脈 20 秒）で動かすモデル
    maai_vap_cpc: bool = False  # vap を Mimi から除き、別の CPC（10 Hz、文脈 5 秒）で動かす
    maai_user_channel: int = 0  # MaAI の入力で、ユーザーの声を入れるチャンネル（0 = ch1、1 = ch2）
    maai_threads: int = 2  # MaAI の計算のスレッド数
    maai_stale_s: float = 0.25  # これより古い予測は、判断に使わない
    backchannel: bool = False  # 相づちの予測（bc）で、ユーザーが話している最中に短い相づちを打つ
    bc_text: str = "うん"  # 相づちの言葉。起動時に TTS で前もって合成しておく
    bc_threshold: float = 0.5  # p_bc がこれ以上なら、相づちの機会とみなす
    bc_arm_s: float = 0.4  # 機会とみなしてから、ユーザーの短い無音を待つ長さ（p_bc は無音より先に上がる）
    bc_quiet_s: float = 0.064  # ユーザーの無音がこの長さになったら打つ
    bc_cooldown_s: float = 3.0  # 前の相づちから、この長さは打たない
    bc_min_turn_s: float = 1.2  # ユーザーが話し始めてから、この長さは打たない
    bc_max_shift_p: float = 0.6  # vap の Tomoko の番らしさ（p_now）がこれ以上なら打たない（答える場面）
    maai_endpoint: bool = False  # VAD の言い終わりの判定を、vap の予測で早めたり延ばしたりする
    ep_signal: str = "p_now"  # 使う vap の値（"p_now" か "p_future"）
    ep_shift_p: float = 0.6  # Tomoko の番らしさがこれ以上なら、最短の無音で言い終わりとする
    ep_hold_p: float = 0.35  # これ以下なら、最長の無音まで待つ
    ep_min_silence_s: float = 0.224
    ep_max_silence_s: float = 1.0
    barge_guard: bool = False  # 声を出している最中のユーザーの声を、bc_det で相づちと見分け、止めずに聞き流す
    guard_bc_det: float = 0.45  # p_bc_det（ユーザー）がこれ以上なら相づちとみなす（MaAI の勧める値は 0.39〜0.45）
    guard_max_voiced_s: float = 1.3  # 相づちとみなしても、声がこの長さ続いたら止める
    trace_dir: Path = field(default_factory=lambda: Path("work/traces"))
    system_prompt: str = (
        "あなたは Tomoko。ユーザーと声で話している。"
        "話し言葉で、1〜3 文で答える。記号、絵文字、箇条書き、英字の略語は使わない。"
    )

    @classmethod
    def from_env(cls) -> EngineConfig:
        """TOMOKO_<項目名> の環境変数で、項目を上書きする。空の文字は None。測定で構成を切り替えるのに使う。"""
        values = {}
        for f in dataclasses.fields(cls):
            raw = os.environ.get(f"TOMOKO_{f.name.upper()}")
            if raw is None:
                continue
            if raw == "":
                values[f.name] = None
                continue
            kind = type(getattr(cls(), f.name)) if getattr(cls(), f.name) is not None else None
            if kind is None:  # 既定が None の項目は、数として読めれば数にする
                try:
                    values[f.name] = int(raw) if raw.lstrip("-").isdigit() else float(raw)
                except ValueError:
                    values[f.name] = raw
            elif kind is bool:
                values[f.name] = raw.lower() in ("1", "true", "yes", "on")
            else:
                values[f.name] = kind(raw)
        return cls(**values)
