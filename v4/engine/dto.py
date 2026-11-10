"""層をまたぐ型は、すべてここに置く。"""

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

IN_SR = 16_000  # マイクの音
OUT_SR = 24_000  # 声


@dataclass(frozen=True)
class Utterance:
    """VAD が切り出した、ユーザーの 1 発話。時刻は入力の先頭からの秒（入力の時計）。"""

    audio: np.ndarray
    start_s: float
    end_s: float


@dataclass(frozen=True)
class VadEvent:
    kind: str  # "start" か "end"
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
    tts_pre_silence_s: float | None = None  # 声の頭の無音。None はエンジンの値のまま
    first_cut_min_chars: int | None = None  # 最初の句を読点で切る。None は切らない
    vad_threshold: float = 0.5
    vad_silence_s: float = 0.4  # この長さの無音で、言い終わりとする
    vad_preroll_s: float = 0.5  # 発話の頭が欠けないように、手前を残す
    vad_min_voiced_s: float = 0.2  # これより短い音は発話にしない
    send_chunk_s: float = 0.2  # 声を送る断片の長さ
    trace_dir: Path = field(default_factory=lambda: Path("work/traces"))
    system_prompt: str = (
        "あなたは Tomoko。ユーザーと声で話している。"
        "話し言葉で、1〜3 文で答える。記号、絵文字、箇条書き、英字の略語は使わない。"
    )

    @classmethod
    def from_env(cls) -> "EngineConfig":
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
            else:
                values[f.name] = kind(raw)
        return cls(**values)
