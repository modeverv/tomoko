"""層をまたぐ型は、すべてここに置く。"""

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
    stt_model: str = "mlx-community/whisper-large-v3-turbo"
    tts_voice: str = "jf_alpha"
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
