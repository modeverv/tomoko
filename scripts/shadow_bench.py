#!/usr/bin/env python3
"""
shadow_bench.py — Turn-taking v2 shadow worker timing benchmark
---------------------------------------------------------------

シャドウワーカーがメイン経路よりどれだけ早く LLM 推論を開始できるかを、
say コマンドによる音声合成 + STT partial シミュレーションで計測する。

タイムラインモデル（1発話、すべて発話開始からの相対ms）:

    0 ─────────── D ──────── D+400 ──── D+550
    │   音声再生   │ VAD silence │ final確定 │→ main LLM 開始
    │              │  ウィンドウ  │  +150ms   │
    partial(遅延200ms)が逐次到着 ──┘

  - main:   D + VAD_SILENCE_MS(400) + STT_FINALIZE_MS(150) で LLM 開始
  - shadow: partial 到着ごとに評価。発火ポリシーで開始時刻が変わる

発火ポリシー（同一 run で3種を同時計測）:
  prod   - 現行実装。vad_state=listening 中は発火しない（v2_evaluator のゲート）
           → final 確定時にしか発火できず lead=0
  energy - 提案。意味完了 + 音声エネルギーが静か(-45dB以下)なら listening 中でも発火
           → VAD silence ウィンドウ内（D+lag 時点）で発火でき、
             VAD残り時間 + final確定遅延 ≒ 350ms 程度を詰められる
  eager  - 意味完了のみで発火（音声がまだ鳴っていても発火）
           → 最大リードだが、ユーザーがまだ話している間の誤発火リスクあり

誤発火（premature）: 発火時刻 < 音声終了(D) - 50ms
  = ユーザーがまだ話している最中に LLM を開始してしまったケース

シード: scripts/seeds/utterances.tsv（label TAB text）
  complete   完結単文（早期発火できるべき）
  multi      複数文（途中に完結に見える点がある eager トラップ）
  incomplete 言いさし（発火してはいけない）
  filler     相槌（判定対象外であるべき）

使い方:
  python scripts/shadow_bench.py              # 20本でテスト
  python scripts/shadow_bench.py -n 40        # 40本
  python scripts/shadow_bench.py --seed 7     # 乱数シード固定
  python scripts/shadow_bench.py --analyze    # 最新ログを再分析
  python scripts/shadow_bench.py --play       # 音声も再生する

依存: Python 3.11+, macOS say/afinfo コマンド
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

# プロジェクトルートを sys.path に入れて server モジュールを使えるようにする
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from server.gateway.turn_taking.v2_evaluator import (
    TranscriptValidity,
    StablePrefixExtractor,
    SemanticFinishJudge,
    SpeechMotivationEvaluator,
)

# ============================================================
# 設定
# ============================================================
SEEDS_FILE = _ROOT / "scripts" / "seeds" / "utterances.tsv"
LOG_DIR = _ROOT / "logs"
SAY_VOICE = os.environ.get("SAY_VOICE", "Kyoko")

VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "400"))     # VAD 沈黙判定ウィンドウ
STT_LAG_MS = int(os.environ.get("STT_LAG_MS", "200"))             # partial 転写の遅延
STT_FINALIZE_MS = int(os.environ.get("STT_FINALIZE_MS", "150"))   # VAD発火→final転写確定の遅延

SPEAKING_DB = -25.0   # 発話中の音声レベル
QUIET_DB = -60.0      # 無音時の音声レベル
QUIET_GATE_DB = -45.0  # energy ポリシーが「静か」とみなす閾値

PARTIAL_INTERVAL_CHARS = 3   # 何文字ごとに partial を生成するか
PREMATURE_MARGIN_MS = 50.0   # 音声終了の何ms前までを「まだ話している」とみなすか

# 意味完了の発火条件（v2_evaluator の would_start_inference と同じ semantic 条件）
SEM_SATURATION_MIN = 0.75
SEM_CONFIDENCE_MIN = 0.5
SEM_SPLIT_RISK_MAX = 0.5

# fusion ポリシー: energy と eager の合成。加重スコアで発火を判定する。
#   sem_core = saturation * confidence * (1 - split_risk)
#   score    = W_SEM * sem_core + W_QUIET * quiet + W_STABLE * tail_stable
#   fire     = score >= THETA
# quiet      = 音声エネルギーが静か（or VAD発火後）
# tail_stable= 現テキストが stable prefix と一致（再送で確定済み = 揺らぎなし）
# デフォルト値は --tune のグリッドサーチで決定（TT-v2.9, N=120, seed=42, VAP動的VAD:
# 実効lead 615ms / miss 0 / 誤発火 0 / 言いさし発火 0）。
# 「静か + テキスト確定(tail_stable) + 十分な意味完了(sem_core)」を基本に、
# VAP のターン譲渡確率が高いときはやや低い意味完了度でも発火を許す。
FUSION_W_SEM = float(os.environ.get("FUSION_W_SEM", "0.5"))
FUSION_W_QUIET = float(os.environ.get("FUSION_W_QUIET", "0.1"))
FUSION_W_STABLE = float(os.environ.get("FUSION_W_STABLE", "0.2"))
FUSION_W_VAP = float(os.environ.get("FUSION_W_VAP", "0.1"))
FUSION_THETA = float(os.environ.get("FUSION_THETA", "0.6"))

# --- Phase TT-v2.9: VAP（maai）統合 ---
# 本番（TT-v2.5）と同じ VAP ハイブリッド動的無音制御をメイン側のモデルに使う。
# recommended_silence_ms = min + (1 - p_yielding) * delta（p >= threshold のとき）、それ以外は max
VAP_HYBRID = os.environ.get("VAP_HYBRID", "1") == "1"
VAP_MIN_SILENCE_MS = float(os.environ.get("VAP_MIN_SILENCE_MS", "150"))
VAP_DELTA_SILENCE_MS = float(os.environ.get("VAP_DELTA_SILENCE_MS", "650"))
VAP_MAX_SILENCE_MS = float(os.environ.get("VAP_MAX_SILENCE_MS", "800"))
VAP_THRESHOLD_P = float(os.environ.get("VAP_THRESHOLD_P", "0.90"))

POLICIES = ("prod", "energy", "fusion", "eager")

# --- Phase TT-v2.8: prefill 経済モデルのパラメータ ---
# TTFT（デコード開始可能までの時間）= prefill 完了時刻。prefill は KV キャッシュとして
# 再利用できるため、partial 到着ごとの差分 prefill で final 時の残り計算をほぼゼロにできる。
TOK_PER_CHAR = float(os.environ.get("TOK_PER_CHAR", "0.8"))            # 日本語: 文字→トークン換算
PREFILL_TOK_PER_SEC = float(os.environ.get("PREFILL_TOK_PER_SEC", "500"))  # ローカルMLXの想定prefill速度
CONTEXT_TOKENS = int(os.environ.get("CONTEXT_TOKENS", "1500"))         # システムプロンプト+履歴
PREFILL_OVERHEAD_MS = float(os.environ.get("PREFILL_OVERHEAD_MS", "30"))   # 1回のprefill呼び出し固定費


# ============================================================
# データクラス
# ============================================================
@dataclass
class PartialEvent:
    """STT から来る partial 転写イベント"""
    revision: int
    text: str                 # 表示テキスト（ノイズで末尾が揺れることがある）
    chars_covered: int        # 元テキストの何文字目まで発話済みか
    spoken_at_ms: float       # その文字が発話された時刻
    arrival_ms: float         # shadow に届いた時刻（= spoken_at + STT_LAG）
    audio_level_db: float     # 到着時点の音声レベル
    vad_state: str            # 到着時点の VAD 状態 ("listening" / "processing")
    is_final: bool = False    # True = VAD発火後の final 転写
    p_yielding: float | None = None  # 到着時点の VAP ターン譲渡確率（maai 不在時 None）


@dataclass
class ShadowAdvisory:
    """Shadow worker が1 partial に対して出す評価結果"""
    revision: int
    arrival_ms: float
    stable_text: str
    eval_text: str               # 実際に評価対象にしたテキスト
    semantic_saturation: float
    remaining_info_risk: float
    semantic_split_risk: float
    confidence: float
    speech_decision_score: float
    proposal: str
    quiet: bool                  # 到着時点で音声エネルギーが静か（or VAD発火後）
    tail_stable: bool            # 現テキスト == stable prefix（再送で確定済み）
    p_yielding: float | None     # 到着時点の VAP ターン譲渡確率
    fire_prod: bool     # 現行ゲート（listening 中は発火不可）
    fire_energy: bool   # 意味完了 + 音声が静かなら発火
    fire_fusion: bool   # 加重スコア（energy と eager の合成）で発火
    fire_eager: bool    # 意味完了のみで発火
    reason: str


@dataclass
class BenchTurn:
    """1発話のベンチマーク結果"""
    utterance_id: int
    label: str                       # complete / multi / incomplete / filler
    original_text: str
    presented_text: str              # ぶつ切り後に実際に音声化したテキスト
    cut_point_ratio: float           # 1.0 = カットなし
    effective_complete: bool         # ラベルが complete/multi かつ未カット
    audio_duration_ms: float
    vad_wait_ms: float               # メイン VAD の無音待ち時間（VAP動的 or 固定）
    p_yield_end: float | None        # 発話終了直後の p_yielding
    main_llm_start_ms: float         # メイン経路の LLM 開始時刻
    partials: list[PartialEvent]
    shadow_advisories: list[ShadowAdvisory]
    # ポリシーごとの最初の発火時刻 / リード / 誤発火 / hit
    # hit = 発火時の評価テキストが最終テキストと一致（句読点を除く）。
    #       miss は投機失敗 = final で再推論が必要（実効リード0 + 無駄なLLM呼び出し）
    prod_first_ms: float | None
    prod_lead_ms: float | None
    prod_hit: bool
    energy_first_ms: float | None
    energy_lead_ms: float | None
    energy_premature: bool
    energy_hit: bool
    fusion_first_ms: float | None
    fusion_lead_ms: float | None
    fusion_premature: bool
    fusion_hit: bool
    eager_first_ms: float | None
    eager_lead_ms: float | None
    eager_premature: bool
    eager_hit: bool


def norm_text(s: str) -> str:
    """hit/miss 判定用の正規化: 末尾の句読点・記号と空白を除く。"""
    return s.strip().rstrip("。．.？?！!、,…〜～ ")


# ============================================================
# シード発話ロード
# ============================================================
def load_utterances(path: Path) -> list[tuple[str, str]]:
    """(label, text) のリストを返す。"""
    items: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "\t" in line:
                label, text = line.split("\t", 1)
            else:
                label, text = "unknown", line
            items.append((label.strip(), text.strip()))
    return items


# ============================================================
# say コマンドで音声ファイル生成 & duration 取得
# ============================================================
SAY_SAMPLE_RATE = 16000  # maai VAP の入力レートに合わせる


def synthesize_say(text: str, out_path: Path, voice: str = SAY_VOICE) -> None:
    cmd = ["say", "-v", voice, "-o", str(out_path),
           "--file-format=WAVE", f"--data-format=LEI16@{SAY_SAMPLE_RATE}", text]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"say failed: {result.stderr}")


def load_wav_float32(wav_path: Path) -> "object":
    """16bit mono WAV を float32 配列で返す（要 numpy。VAP 用）。"""
    import wave
    import numpy as np
    with wave.open(str(wav_path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def get_audio_duration_ms(wav_path: Path) -> float:
    import wave
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / w.getframerate() * 1000.0


def play_audio(wav_path: Path) -> None:
    subprocess.run(["afplay", str(wav_path)], check=True)


# ============================================================
# Phase TT-v2.9: maai VAP トレーサー
# ============================================================
class VapTracer:
    """say 合成音声に対して maai VAP をオフライン実行し p_yielding 軌跡を返す。

    1インスタンスを使い回し、発話間は無音をフラッシュしてコンテキストを薄める。
    frame_rate=10Hz → 結果 i は音声時刻 (i+1)*100ms に対応。
    """
    FRAME = SAY_SAMPLE_RATE // 10  # 100ms

    def __init__(self, device: str = "cpu") -> None:
        import queue as _queue
        import numpy as np
        import maai
        self._np = np
        self._queue_mod = _queue
        self._ch1 = maai.MaaiInput.Chunk()
        self._ch2 = maai.MaaiInput.Chunk()
        self._maai = maai.Maai(
            mode="vap", lang="jp", frame_rate=10, context_len_sec=20,
            audio_ch1=self._ch1, audio_ch2=self._ch2, device=device,
        )
        self._maai.start()
        self._q = self._maai.result_dict_queue

    def _drain(self, grace_sec: float = 0.3) -> None:
        deadline = time.time() + grace_sec
        while time.time() < deadline:
            try:
                self._q.get(timeout=0.1)
                deadline = time.time() + grace_sec
            except self._queue_mod.Empty:
                pass

    def trace(self, wav_path: Path, total_ms: float) -> list[float]:
        np = self._np
        audio = load_wav_float32(wav_path)
        need = int(total_ms / 1000.0 * SAY_SAMPLE_RATE)
        if len(audio) < need:
            audio = np.concatenate([audio, np.zeros(need - len(audio), dtype=np.float32)])
        n_frames = len(audio) // self.FRAME
        zeros = np.zeros(self.FRAME, dtype=np.float32)

        self._drain(0.2)  # 前回フラッシュの残骸を捨てる
        for i in range(n_frames):
            self._ch1.put_chunk(audio[i * self.FRAME:(i + 1) * self.FRAME])
            self._ch2.put_chunk(zeros)

        out: list[float] = []
        deadline = time.time() + 5.0 + n_frames * 0.05
        while len(out) < n_frames and time.time() < deadline:
            try:
                r = self._q.get(timeout=0.5)
            except self._queue_mod.Empty:
                break
            pf = r.get("p_future") if isinstance(r, dict) else None
            out.append(float(pf[1]) if isinstance(pf, list) and len(pf) > 1 else 0.0)

        # コンテキストフラッシュ（2秒の無音。結果は次回 trace 冒頭の drain で破棄）
        for _ in range(20):
            self._ch1.put_chunk(zeros)
            self._ch2.put_chunk(zeros)
        return out


def make_vap_tracer() -> VapTracer | None:
    if not VAP_HYBRID:
        return None
    try:
        return VapTracer()
    except Exception as e:
        print(f"[WARN] maai VAP を初期化できないため固定VAD({VAD_SILENCE_MS}ms)で実行します: {e}",
              file=sys.stderr)
        return None


def p_yielding_at(trace: list[float] | None, t_ms: float) -> float | None:
    """p_yielding 軌跡から時刻 t_ms の値を取る（フレーム i = (i+1)*100ms）。"""
    if not trace:
        return None
    idx = int(t_ms / 100.0) - 1
    return trace[max(0, min(idx, len(trace) - 1))]


def vap_dynamic_vad_wait_ms(trace: list[float] | None, audio_end_ms: float) -> float:
    """本番の VAP ハイブリッド制御を再現:
    沈黙の経過とともに p_yielding が更新され、経過時間 >= recommended になった時点で発火。
    """
    if not trace:
        return float(VAD_SILENCE_MS)
    max_wait = VAP_MAX_SILENCE_MS
    tau = 50.0
    while tau < max_wait:
        p = p_yielding_at(trace, audio_end_ms + tau)
        if p is not None and p >= VAP_THRESHOLD_P:
            rec = VAP_MIN_SILENCE_MS + (1.0 - p) * VAP_DELTA_SILENCE_MS
        else:
            rec = VAP_MIN_SILENCE_MS + VAP_DELTA_SILENCE_MS
        rec = min(rec, max_wait)
        if tau >= rec:
            return tau
        tau += 50.0
    return max_wait


# ============================================================
# ぶつ切りシミュレーション
# ============================================================
def make_cut_point(text: str, mode: str = "full") -> tuple[str, float]:
    """
    mode:
      'full'     - カットなし（シードの incomplete ラベルが言いさしを担うため基本これ）
      'random'   - ランダム位置でカット（30%〜100%）
      'semantic' - 接続助詞の直後など意味的に悪い位置でカット
    """
    if mode == "full" or len(text) <= 3:
        return text, 1.0

    if mode == "semantic":
        cut_candidates = []
        connector_endings = ("て", "で", "が", "と", "から", "ので", "けど", "たら", "なら", "ながら")
        for i in range(3, len(text) - 1):
            tail = text[max(0, i - 4):i + 1]
            if any(tail.endswith(e) for e in connector_endings):
                cut_candidates.append(i + 1)
        if cut_candidates:
            cut_pos = random.choice(cut_candidates)
        else:
            cut_pos = random.randint(len(text) // 3, len(text) - 1)
    else:
        min_pos = max(1, len(text) * 3 // 10)
        cut_pos = random.randint(min_pos, len(text))

    return text[:cut_pos], cut_pos / len(text)


# ============================================================
# Partial 転写列シミュレーション
# ============================================================
def simulate_partials(
    text: str,
    audio_duration_ms: float,
    vad_wait_ms: float,
    noise_probability: float = 0.15,
) -> list[PartialEvent]:
    """
    STT が PARTIAL_INTERVAL_CHARS 文字ごとに partial を出すと仮定し、
    STT_LAG_MS 遅れで shadow に到着するイベント列を返す。

    到着時点で音声がまだ鳴っているか（audio_level）と VAD 状態を付与する。
    最後に final 転写（VAD発火 + 確定遅延後）を追加する。
    """
    if not text:
        return []

    n_chars = len(text)
    char_time = lambda i: (i + 1) / n_chars * audio_duration_ms  # noqa: E731

    partials: list[PartialEvent] = []
    prev_text = ""
    revision = 0

    step = max(1, PARTIAL_INTERVAL_CHARS)
    for i in range(step, n_chars + 1, step):
        chunk_text = text[:i]
        spoken_at = char_time(i - 1)
        arrival = spoken_at + STT_LAG_MS

        # ノイズ: 確率で末尾1〜2文字が欠ける（STTの揺らぎ）
        displayed_text = chunk_text
        if random.random() < noise_probability and len(chunk_text) > 2:
            trim = random.randint(1, min(2, len(chunk_text) - 1))
            displayed_text = chunk_text[:-trim]

        if displayed_text == prev_text:
            continue

        audio_level = SPEAKING_DB if arrival < audio_duration_ms else QUIET_DB
        vad_state = "listening" if arrival < audio_duration_ms + vad_wait_ms else "processing"
        partials.append(PartialEvent(
            revision=revision,
            text=displayed_text,
            chars_covered=i,
            spoken_at_ms=spoken_at,
            arrival_ms=arrival,
            audio_level_db=audio_level,
            vad_state=vad_state,
            is_final=False,
        ))
        prev_text = displayed_text
        revision += 1

    # 最後の文字までの完全な partial が必ず出るようにする（ノイズ欠けも補正）
    if not partials or partials[-1].text != text:
        spoken_at = audio_duration_ms
        arrival = max(spoken_at + STT_LAG_MS,
                      partials[-1].arrival_ms + 1 if partials else 0)
        partials.append(PartialEvent(
            revision=revision,
            text=text,
            chars_covered=n_chars,
            spoken_at_ms=spoken_at,
            arrival_ms=arrival,
            audio_level_db=QUIET_DB if arrival >= audio_duration_ms else SPEAKING_DB,
            vad_state="listening" if arrival < audio_duration_ms + vad_wait_ms else "processing",
            is_final=False,
        ))
        revision += 1

    # STT の再送: silence 中も同一テキストの partial が周期的に再送される。
    # 直前 revision と同じテキストが2回続くことで StablePrefixExtractor の
    # stable prefix が full テキストに追いつき、文末マーカー込みで評価できるようになる。
    resend_arrival = partials[-1].arrival_ms + 150.0
    if resend_arrival < audio_duration_ms + vad_wait_ms - 10.0:
        partials.append(PartialEvent(
            revision=revision,
            text=text,
            chars_covered=n_chars,
            spoken_at_ms=audio_duration_ms,
            arrival_ms=resend_arrival,
            audio_level_db=QUIET_DB,
            vad_state="listening",
            is_final=False,
        ))
        revision += 1

    # final 転写: VAD発火(D+vad_wait) + 確定遅延
    final_arrival = audio_duration_ms + vad_wait_ms + STT_FINALIZE_MS
    partials.append(PartialEvent(
        revision=revision,
        text=text,
        chars_covered=n_chars,
        spoken_at_ms=audio_duration_ms,
        arrival_ms=final_arrival,
        audio_level_db=QUIET_DB,
        vad_state="processing",
        is_final=True,
    ))
    return partials


# ============================================================
# Shadow Worker 評価（DB なし・同期版）
# ============================================================
def run_shadow_evaluator(
    partial: PartialEvent,
    history_texts: list[str],
) -> ShadowAdvisory:
    """1 つの partial に対して shadow worker 相当の評価 + 3ポリシーの発火判定を行う。"""
    is_valid = TranscriptValidity.evaluate(partial.text)
    if not is_valid:
        return ShadowAdvisory(
            revision=partial.revision,
            arrival_ms=partial.arrival_ms,
            stable_text="",
            eval_text="",
            semantic_saturation=0.0,
            remaining_info_risk=1.0,
            semantic_split_risk=0.0,
            confidence=0.0,
            speech_decision_score=0.0,
            proposal="silence",
            quiet=False,
            tail_stable=False,
            p_yielding=partial.p_yielding,
            fire_prod=False,
            fire_energy=False,
            fire_fusion=False,
            fire_eager=False,
            reason="hallucination_or_noise",
        )

    stable_text, _ = StablePrefixExtractor.split_stable_unstable(
        history_texts, partial.text
    )
    eval_text = stable_text if stable_text else partial.text

    sem = SemanticFinishJudge.evaluate(eval_text)

    # 本番の SpeechMotivationEvaluator（score/proposal 用。prod の発火判定もここから）
    mot = SpeechMotivationEvaluator.evaluate(
        semantic_saturation=sem["semantic_saturation"],
        remaining_info_risk=sem["remaining_info_risk"],
        semantic_split_risk=sem["semantic_split_risk"],
        confidence=sem["confidence"],
        vad_state=partial.vad_state,
        attention_mode="engaged",
        audio_level_db=partial.audio_level_db,
    )

    # 意味完了条件（しきい値型。prod/energy/eager 共通）
    sem_ok = (
        sem["semantic_saturation"] >= SEM_SATURATION_MIN
        and sem["confidence"] >= SEM_CONFIDENCE_MIN
        and sem["semantic_split_risk"] < SEM_SPLIT_RISK_MAX
    )

    quiet = (
        partial.vad_state != "listening"
        or partial.audio_level_db <= QUIET_GATE_DB
    )
    tail_stable = bool(stable_text) and stable_text == partial.text

    fire_prod = bool(mot["would_start_inference"])  # listening 中は常に False
    fire_energy = sem_ok and quiet
    fire_eager = sem_ok

    # fusion: 加重スコア（連続値）で判定
    sem_core = (
        float(sem["semantic_saturation"])
        * float(sem["confidence"])
        * (1.0 - float(sem["semantic_split_risk"]))
    )
    fusion_score = (
        FUSION_W_SEM * sem_core
        + FUSION_W_QUIET * (1.0 if quiet else 0.0)
        + FUSION_W_STABLE * (1.0 if tail_stable else 0.0)
        + FUSION_W_VAP * (partial.p_yielding or 0.0)
    )
    fire_fusion = fusion_score >= FUSION_THETA

    return ShadowAdvisory(
        revision=partial.revision,
        arrival_ms=partial.arrival_ms,
        stable_text=stable_text,
        eval_text=eval_text,
        semantic_saturation=float(sem["semantic_saturation"]),
        remaining_info_risk=float(sem["remaining_info_risk"]),
        semantic_split_risk=float(sem["semantic_split_risk"]),
        confidence=float(sem["confidence"]),
        speech_decision_score=float(mot["speech_decision_score"]),
        proposal=str(mot["proposal"]),
        quiet=quiet,
        tail_stable=tail_stable,
        p_yielding=partial.p_yielding,
        fire_prod=fire_prod,
        fire_energy=fire_energy,
        fire_fusion=fire_fusion,
        fire_eager=fire_eager,
        reason="rule_based",
    )


# ============================================================
# 1発話のベンチマーク実行
# ============================================================
def bench_one_utterance(
    utterance_id: int,
    label: str,
    text: str,
    tmp_dir: Path,
    cut_mode: str = "full",
    play: bool = False,
    verbose: bool = False,
    vap_tracer: VapTracer | None = None,
) -> BenchTurn:
    presented_text, cut_ratio = make_cut_point(text, mode=cut_mode)

    if verbose:
        print(f"  [{utterance_id}] ({label}) presented: {presented_text!r} (ratio={cut_ratio:.2f})")

    wav_path = tmp_dir / f"utt_{utterance_id:04d}.wav"
    try:
        synthesize_say(presented_text, wav_path)
        audio_duration_ms = get_audio_duration_ms(wav_path)
    except Exception as e:
        print(f"  [WARN] say failed for utterance {utterance_id}: {e}", file=sys.stderr)
        audio_duration_ms = len(presented_text) / 6.0 * 1000.0  # 6文字/秒で推定

    if play:
        print(f"  → 再生中: {wav_path.name}")
        try:
            play_audio(wav_path)
        except Exception as e:
            print(f"  [WARN] afplay failed: {e}", file=sys.stderr)

    # VAP トレース（音声終了後 max VAD + final 確定分まで p_yielding を取る）
    vap_trace: list[float] | None = None
    if vap_tracer is not None and wav_path.exists():
        try:
            vap_trace = vap_tracer.trace(
                wav_path,
                total_ms=audio_duration_ms + VAP_MAX_SILENCE_MS + STT_FINALIZE_MS + 200.0,
            )
        except Exception as e:
            print(f"  [WARN] VAP trace failed for utterance {utterance_id}: {e}", file=sys.stderr)

    vad_wait_ms = vap_dynamic_vad_wait_ms(vap_trace, audio_duration_ms)
    p_yield_end = p_yielding_at(vap_trace, audio_duration_ms + 100.0)

    partials = simulate_partials(presented_text, audio_duration_ms, vad_wait_ms)
    for p in partials:
        p.p_yielding = p_yielding_at(vap_trace, p.arrival_ms)

    shadow_advisories: list[ShadowAdvisory] = []
    history_texts: list[str] = []
    first_fire: dict[str, float | None] = {p: None for p in POLICIES}
    fire_text: dict[str, str] = {p: "" for p in POLICIES}

    for partial in partials:
        advisory = run_shadow_evaluator(partial, history_texts)
        shadow_advisories.append(advisory)
        history_texts.append(partial.text)

        for policy, fired in (
            ("prod", advisory.fire_prod),
            ("energy", advisory.fire_energy),
            ("fusion", advisory.fire_fusion),
            ("eager", advisory.fire_eager),
        ):
            if fired and first_fire[policy] is None:
                first_fire[policy] = advisory.arrival_ms
                fire_text[policy] = advisory.eval_text
                if verbose:
                    print(f"  [{utterance_id}] {policy} fire @ {advisory.arrival_ms:.0f}ms: {advisory.eval_text!r}")

    main_llm_start_ms = audio_duration_ms + vad_wait_ms + STT_FINALIZE_MS

    def lead(first_ms: float | None) -> float | None:
        return None if first_ms is None else main_llm_start_ms - first_ms

    def premature(first_ms: float | None) -> bool:
        return first_ms is not None and first_ms < audio_duration_ms - PREMATURE_MARGIN_MS

    def hit(policy: str) -> bool:
        if first_fire[policy] is None:
            return False
        return norm_text(fire_text[policy]) == norm_text(presented_text)

    effective_complete = label in ("complete", "multi") and cut_ratio >= 0.999

    if verbose:
        for p in POLICIES:
            f = first_fire[p]
            print(f"  [{utterance_id}] {p:7s}: first={'—' if f is None else f'{f:.0f}ms'}  "
                  f"lead={'—' if f is None else f'{main_llm_start_ms - f:+.0f}ms'}")

    return BenchTurn(
        utterance_id=utterance_id,
        label=label,
        original_text=text,
        presented_text=presented_text,
        cut_point_ratio=cut_ratio,
        effective_complete=effective_complete,
        audio_duration_ms=audio_duration_ms,
        vad_wait_ms=vad_wait_ms,
        p_yield_end=p_yield_end,
        main_llm_start_ms=main_llm_start_ms,
        partials=partials,
        shadow_advisories=shadow_advisories,
        prod_first_ms=first_fire["prod"],
        prod_lead_ms=lead(first_fire["prod"]),
        prod_hit=hit("prod"),
        energy_first_ms=first_fire["energy"],
        energy_lead_ms=lead(first_fire["energy"]),
        energy_premature=premature(first_fire["energy"]),
        energy_hit=hit("energy"),
        fusion_first_ms=first_fire["fusion"],
        fusion_lead_ms=lead(first_fire["fusion"]),
        fusion_premature=premature(first_fire["fusion"]),
        fusion_hit=hit("fusion"),
        eager_first_ms=first_fire["eager"],
        eager_lead_ms=lead(first_fire["eager"]),
        eager_premature=premature(first_fire["eager"]),
        eager_hit=hit("eager"),
    )


# ============================================================
# JSONL 保存 / ロード
# ============================================================
def save_jsonl(turns: list[BenchTurn], out_path: Path) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for turn in turns:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")
    print(f"→ ログ保存: {out_path}")


def load_jsonl(path: Path) -> list[BenchTurn]:
    turns = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            partials = [PartialEvent(**p) for p in d.pop("partials")]
            advisories = [ShadowAdvisory(**a) for a in d.pop("shadow_advisories")]
            turns.append(BenchTurn(**d, partials=partials, shadow_advisories=advisories))
    return turns


# ============================================================
# テキスト分析レポート
# ============================================================
def _policy_stats(turns: list[BenchTurn], policy: str) -> dict:
    firsts = {t.utterance_id: getattr(t, f"{policy}_first_ms") for t in turns}
    leads = [getattr(t, f"{policy}_lead_ms") for t in turns if getattr(t, f"{policy}_lead_ms") is not None]
    complete_turns = [t for t in turns if t.effective_complete]
    complete_leads = [getattr(t, f"{policy}_lead_ms") for t in complete_turns
                      if getattr(t, f"{policy}_lead_ms") is not None]
    incomplete_turns = [t for t in turns if not t.effective_complete]
    # 誤発火: 音声がまだ鳴っている間の発火（prod は構造上起きない）
    prem_key = f"{policy}_premature" if policy != "prod" else None
    prematures = [t for t in turns if prem_key and getattr(t, prem_key)]
    # incomplete 発話への発火（=ユーザーが続けるつもりだった発話に発火）
    fired_on_incomplete = [t for t in incomplete_turns if firsts[t.utterance_id] is not None
                           and firsts[t.utterance_id] < t.main_llm_start_ms]
    # 実効リード: hit なら lead、miss なら 0（final で再推論が必要なため）。
    # 完結発話全体の平均（発火しなかった発話も 0 として含める）
    effective_leads = []
    misses = []
    for t in turns:
        first = getattr(t, f"{policy}_first_ms")
        if first is None:
            continue
        if getattr(t, f"{policy}_hit"):
            pass
        else:
            misses.append(t)
    for t in complete_turns:
        lead = getattr(t, f"{policy}_lead_ms")
        is_hit = getattr(t, f"{policy}_hit")
        effective_leads.append(lead if (lead is not None and is_hit) else 0.0)
    return {
        "fired": len(leads),
        "complete_total": len(complete_turns),
        "complete_fired": len(complete_leads),
        "complete_leads": complete_leads,
        "effective_leads": effective_leads,
        "misses": len(misses),
        "premature": len(prematures),
        "fired_on_incomplete": len(fired_on_incomplete),
        "incomplete_total": len(incomplete_turns),
    }


def analyze_turns(turns: list[BenchTurn]) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("Shadow Bench 分析レポート（ポリシー比較）")
    waits = [t.vad_wait_ms for t in turns]
    wait_note = (f"VAD待ち 平均{sum(waits)/len(waits):.0f}ms"
                 f"(min {min(waits):.0f} / max {max(waits):.0f})" if waits else "")
    lines.append(f"実行発話数: {len(turns)}  "
                 f"({wait_note}, STT遅延={STT_LAG_MS}ms, final確定={STT_FINALIZE_MS}ms)")
    lines.append("=" * 72)

    label_counts: dict[str, int] = {}
    for t in turns:
        label_counts[t.label] = label_counts.get(t.label, 0) + 1
    lines.append(f"\n■ ラベル内訳: " + "  ".join(f"{k}={v}" for k, v in sorted(label_counts.items())))

    lines.append(f"\n■ ポリシー比較サマリー")
    lines.append(f"  {'policy':>7} | {'完結発話の早期発火':>16} | {'実効lead':>9} | {'生lead平均':>9} | {'最大':>7} | {'miss':>5} | {'誤発火(発話中)':>12} | {'言いさしに発火':>12}")
    lines.append(f"  {'-'*7} | {'-'*16} | {'-'*9} | {'-'*9} | {'-'*7} | {'-'*5} | {'-'*12} | {'-'*12}")
    for policy in POLICIES:
        s = _policy_stats(turns, policy)
        cl = s["complete_leads"]
        el = s["effective_leads"]
        eff = f"{sum(el)/len(el):.0f}ms" if el else "—"
        avg = f"{sum(cl)/len(cl):.0f}ms" if cl else "—"
        mx = f"{max(cl):.0f}ms" if cl else "—"
        lines.append(
            f"  {policy:>7} | {s['complete_fired']:>7}/{s['complete_total']:<8} | {eff:>9} | {avg:>9} | {mx:>7} | {s['misses']:>5} | "
            f"{s['premature']:>12} | {s['fired_on_incomplete']:>4}/{s['incomplete_total']:<7}"
        )
    lines.append("\n  実効lead = hit（発火時テキスト==最終テキスト）なら lead、miss/未発火は 0 として完結発話で平均")
    lines.append("  miss = 投機失敗（発火時テキストが最終と不一致 → final で再推論が必要 + 無駄なLLM呼び出し）")
    lines.append("  誤発火 = ユーザーがまだ話している最中（音声終了-50ms以前）の発火")
    lines.append("  言いさしに発火 = incomplete/カット発話に main より早く発火（ユーザー継続時は無駄打ちになる）")

    # energy ポリシーのリード分布
    energy_leads = [t.energy_lead_ms for t in turns
                    if t.effective_complete and t.energy_lead_ms is not None]
    if energy_leads:
        lines.append(f"\n■ energy ポリシーのリードタイム分布（完結発話のみ）")
        buckets = [
            ("0〜200ms", 0, 200), ("200〜400ms", 200, 400),
            ("400〜600ms", 400, 600), ("600〜1000ms", 600, 1000), ("1000ms以上", 1000, float("inf")),
        ]
        for name, lo, hi in buckets:
            items = [x for x in energy_leads if lo <= x < hi]
            lines.append(f"  {name:12s}: {len(items):3d} 本  {'█' * len(items)}")

    lines.append(f"\n■ 個別結果")
    lines.append(f"  {'ID':>3}  {'label':>10}  {'音声ms':>6}  {'main_ms':>7}  {'prod':>6}  {'energy':>8}  {'fusion':>8}  {'eager':>8}  発話")
    lines.append(f"  {'-'*3}  {'-'*10}  {'-'*6}  {'-'*7}  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*22}")
    for t in turns:
        def fmt(policy: str) -> str:
            l = getattr(t, f"{policy}_lead_ms")
            if l is None:
                return "—"
            mark = ""
            if policy != "prod" and getattr(t, f"{policy}_premature"):
                mark += "!"
            if not getattr(t, f"{policy}_hit"):
                mark += "*"
            return f"+{l:.0f}{mark}"
        text_short = t.presented_text[:22] + ("…" if len(t.presented_text) > 22 else "")
        lines.append(
            f"  {t.utterance_id:>3}  {t.label:>10}  {t.audio_duration_ms:>6.0f}  {t.main_llm_start_ms:>7.0f}  "
            f"{fmt('prod'):>6}  {fmt('energy'):>8}  {fmt('fusion'):>8}  {fmt('eager'):>8}  {text_short}"
        )
    lines.append("\n  （数値はリードms、! は発話中の誤発火、* は miss（投機失敗）、— は発火せず）")

    lines.append(prefill_economics(turns))

    return "\n".join(lines)


# ============================================================
# Phase TT-v2.8: prefill 経済モデル
# ============================================================
def _prefill_ms(tokens: float) -> float:
    if tokens <= 0:
        return 0.0
    return tokens / PREFILL_TOK_PER_SEC * 1000.0 + PREFILL_OVERHEAD_MS


def _lcp_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def prefill_economics(turns: list[BenchTurn]) -> str:
    """
    4方式の「音声終了からデコード開始可能までの遅延」を比較する。

      naive       : final 転写後に context + 発話テキストを全部 prefill（従来）
      ctx_prefill : ターン開始時に context を prefill 開始、final 後に発話分を prefill
      incremental : ctx_prefill + partial 到着ごとに差分 prefill（final 時は残りほぼゼロ）
      incr+spec   : incremental + fusion 発火（hit時）でデコードも先行開始

    すべて「デコード開始可能時刻 - 音声終了時刻」を ms で出す（小さいほど良い）。
    """
    rows = {k: [] for k in ("naive", "ctx", "incr", "spec")}

    for t in turns:
        d = t.audio_duration_ms
        main = t.main_llm_start_ms
        f_tok = len(t.presented_text) * TOK_PER_CHAR

        # naive: final 後に全部 prefill
        naive_ready = main + _prefill_ms(CONTEXT_TOKENS + f_tok)

        # ctx_prefill: context はターン開始(t=0)から prefill（発話中に終わることが多い）
        ctx_done = _prefill_ms(CONTEXT_TOKENS)
        ctx_ready = max(main, ctx_done) + _prefill_ms(f_tok)

        # incremental: partial 到着ごとに増分 prefill。
        # ワーカーがビジー中に複数 partial が溜まったら最新だけ処理する（coalesce）。
        events = []
        prev_len = 0
        prev_text = ""
        for p in t.partials:
            if len(p.text) <= prev_len:
                continue
            events.append((p.arrival_ms, p.text))
            prev_len = len(p.text)
        done = ctx_done
        processed_text = ""
        idx = 0
        while idx < len(events):
            # 現時刻 done までに到着している最新のイベントへスキップ
            j = idx
            while j + 1 < len(events) and events[j + 1][0] <= done:
                j += 1
            arrival, text = events[j]
            start = max(arrival, done)
            new_tok = (len(text) - len(processed_text)) * TOK_PER_CHAR
            done = start + _prefill_ms(new_tok)
            processed_text = text
            idx = j + 1
        # final 時の残り（揺らぎで不一致だった分は再prefill）
        matched = _lcp_len(processed_text, t.presented_text)
        tail_tok = (len(t.presented_text) - matched) * TOK_PER_CHAR
        incr_ready = max(main, done) + _prefill_ms(tail_tok)

        # incr+spec: fusion が hit で発火していたらその時点からデコード開始できる
        if t.fusion_first_ms is not None and t.fusion_hit:
            spec_ready = max(t.fusion_first_ms, done)
        else:
            spec_ready = incr_ready

        rows["naive"].append(naive_ready - d)
        rows["ctx"].append(ctx_ready - d)
        rows["incr"].append(incr_ready - d)
        rows["spec"].append(spec_ready - d)

    lines = []
    lines.append(f"\n■ prefill 経済モデル（音声終了 → デコード開始可能までの遅延）")
    lines.append(f"  前提: context={CONTEXT_TOKENS}tok, {TOK_PER_CHAR}tok/字, prefill={PREFILL_TOK_PER_SEC:.0f}tok/s, "
                 f"呼び出し固定費={PREFILL_OVERHEAD_MS:.0f}ms")
    labels = {
        "naive": "naive（final後に全prefill・従来）",
        "ctx":   "ctx-prefill（ターン開始時に履歴をprefill）",
        "incr":  "incremental（+partial毎に差分prefill）",
        "spec":  "incr+spec（+fusion発火でデコード先行）",
    }
    base = sum(rows["naive"]) / len(rows["naive"]) if rows["naive"] else 0.0
    for key, label in labels.items():
        vals = rows[key]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        med = sorted(vals)[len(vals) // 2]
        lines.append(f"  {label:42s}: 平均 {avg:7.0f}ms  中央値 {med:7.0f}ms  (naive比 -{base - avg:.0f}ms)")
    lines.append("  → TTFT の支配項はターンテイキングのリードではなく prefill。")
    lines.append("    incremental prefill は実装コストに対して効果が桁違いに大きい。")
    lines.append("    本番への示唆: inference router に KV prefill API を追加し、")
    lines.append("    provisional inference (TT-v2.4) を「全文生成・破棄」から「差分prefill+発火時デコード」へ。")
    return "\n".join(lines)


# ============================================================
# fusion 重みのグリッドサーチ（ログから advisory を再評価。再合成不要）
# ============================================================
def tune_weights(turns: list[BenchTurn]) -> str:
    import itertools

    grid_w_sem = [0.4, 0.5, 0.6, 0.7, 0.8]
    grid_w_quiet = [0.1, 0.2, 0.3, 0.4]
    grid_w_stable = [0.0, 0.1, 0.2]
    grid_w_vap = [0.0, 0.1, 0.2, 0.3]
    grid_theta = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

    n_complete = sum(1 for t in turns if t.effective_complete)
    results = []

    for ws, wq, wst, wv, th in itertools.product(
            grid_w_sem, grid_w_quiet, grid_w_stable, grid_w_vap, grid_theta):
        eff_leads: list[float] = []
        misses = 0
        prematures = 0
        fired_inc = 0
        for t in turns:
            first_ms = None
            ftext = ""
            for a in t.shadow_advisories:
                sem_core = a.semantic_saturation * a.confidence * (1.0 - a.semantic_split_risk)
                score = (ws * sem_core + wq * (1.0 if a.quiet else 0.0)
                         + wst * (1.0 if a.tail_stable else 0.0)
                         + wv * (a.p_yielding or 0.0))
                if score >= th:
                    first_ms = a.arrival_ms
                    ftext = a.eval_text
                    break
            if first_ms is None:
                if t.effective_complete:
                    eff_leads.append(0.0)
                continue
            is_hit = norm_text(ftext) == norm_text(t.presented_text)
            if not is_hit:
                misses += 1
            if first_ms < t.audio_duration_ms - PREMATURE_MARGIN_MS:
                prematures += 1
            if not t.effective_complete and first_ms < t.main_llm_start_ms:
                fired_inc += 1
            if t.effective_complete:
                eff_leads.append((t.main_llm_start_ms - first_ms) if is_hit else 0.0)

        mean_eff = sum(eff_leads) / len(eff_leads) if eff_leads else 0.0
        results.append({
            "w_sem": ws, "w_quiet": wq, "w_stable": wst, "w_vap": wv, "theta": th,
            "eff_lead": mean_eff, "misses": misses,
            "prematures": prematures, "fired_inc": fired_inc,
        })

    lines = []
    lines.append("=" * 72)
    lines.append(f"fusion 重みグリッドサーチ（{len(results)} 構成 / 完結発話 {n_complete} 本）")
    lines.append("  目的: 実効lead（hit時のみカウント, miss=0）の最大化")
    lines.append("=" * 72)

    def fmt_rows(rows, title):
        out = [f"\n■ {title}"]
        out.append(f"  {'w_sem':>5} {'w_qui':>5} {'w_stb':>5} {'w_vap':>5} {'theta':>5} | {'実効lead':>8} | {'miss':>4} | {'誤発火':>5} | {'言いさし':>6}")
        for r in rows:
            out.append(
                f"  {r['w_sem']:>5.2f} {r['w_quiet']:>5.2f} {r['w_stable']:>5.2f} {r['w_vap']:>5.2f} {r['theta']:>5.2f} | "
                f"{r['eff_lead']:>6.0f}ms | {r['misses']:>4} | {r['prematures']:>5} | {r['fired_inc']:>6}"
            )
        return out

    by_eff = sorted(results, key=lambda r: -r["eff_lead"])
    lines += fmt_rows(by_eff[:8], "実効lead 上位（制約なし）")

    zero_waste = [r for r in by_eff if r["misses"] == 0 and r["prematures"] == 0]
    lines += fmt_rows(zero_waste[:5], "miss=0 かつ 誤発火=0 の中で実効lead上位（安全運用向け）")

    low_waste = [r for r in by_eff if r["misses"] <= max(2, len(turns) // 20)]
    lines += fmt_rows(low_waste[:5], f"miss ≦ {max(2, len(turns) // 20)} の中で実効lead上位（少量の投機を許容）")

    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow worker timing benchmark")
    parser.add_argument("-n", "--num", type=int, default=20, help="テストする発話数 (default: 20)")
    parser.add_argument("--play", action="store_true", help="音声を再生する")
    parser.add_argument("--cut-mode", choices=["full", "random", "semantic", "mix"], default="full",
                        help="ぶつ切りモード (default: full; incomplete はシードのラベルが担う)")
    parser.add_argument("--seed", type=int, default=None, help="乱数シード")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    parser.add_argument("--analyze", action="store_true", help="既存のログを分析して終了")
    parser.add_argument("--tune", action="store_true", help="既存のログで fusion 重みをグリッドサーチして終了")
    parser.add_argument("--log", type=Path, default=None, help="ログファイルパスを指定")
    parser.add_argument("--keep-audio", action="store_true", help="音声ファイルを残す")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if args.analyze or args.tune:
        log_path = args.log
        if log_path is None:
            candidates = sorted(LOG_DIR.glob("shadow-bench-*.jsonl"), reverse=True)
            if not candidates:
                print("ログファイルが見つかりません。先にベンチを実行してください。")
                sys.exit(1)
            log_path = candidates[0]
        print(f"対象ログ: {log_path}")
        turns = load_jsonl(log_path)
        if args.tune:
            print(tune_weights(turns))
        else:
            print(analyze_turns(turns))
        return

    utterances = load_utterances(SEEDS_FILE)
    if not utterances:
        print(f"エラー: {SEEDS_FILE} に発話が見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    n = min(args.num, len(utterances))
    selected = random.sample(utterances, n)
    print(f"発話数: {n} 本 / {len(utterances)} 本 from {SEEDS_FILE.name}")
    print(f"STT遅延: {STT_LAG_MS}ms  final確定: {STT_FINALIZE_MS}ms  "
          f"voice: {SAY_VOICE}  cut-mode: {args.cut_mode}")

    vap_tracer = make_vap_tracer()
    if vap_tracer is not None:
        print(f"VAD: VAP hybrid (min {VAP_MIN_SILENCE_MS:.0f} / delta {VAP_DELTA_SILENCE_MS:.0f} / "
              f"max {VAP_MAX_SILENCE_MS:.0f} / threshold {VAP_THRESHOLD_P})")
    else:
        print(f"VAD: 固定 {VAD_SILENCE_MS}ms（maai VAP 無効）")
    print()

    import tempfile
    with tempfile.TemporaryDirectory(prefix="shadow_bench_") as tmp_str:
        tmp_dir = Path(tmp_str)

        turns: list[BenchTurn] = []
        for i, (label, text) in enumerate(selected):
            if args.cut_mode == "mix":
                mode = random.choices(["full", "random", "semantic"], weights=[0.5, 0.25, 0.25])[0]
            else:
                mode = args.cut_mode

            print(f"[{i+1}/{n}] ({label}) {text[:36]!r}{'...' if len(text) > 36 else ''}")
            t0 = time.perf_counter()
            turn = bench_one_utterance(
                utterance_id=i + 1,
                label=label,
                text=text,
                tmp_dir=tmp_dir,
                cut_mode=mode,
                play=args.play,
                verbose=args.verbose,
                vap_tracer=vap_tracer,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            lead_parts = []
            for p in POLICIES:
                lv = getattr(turn, f"{p}_lead_ms")
                lead_parts.append(f"{p}=—" if lv is None else f"{p}=+{lv:.0f}ms")
            leads = "  ".join(lead_parts)
            print(f"  → {leads}  audio={turn.audio_duration_ms:.0f}ms  ({elapsed:.0f}ms)")
            turns.append(turn)

        if args.keep_audio:
            audio_out = LOG_DIR / "shadow_bench_audio"
            audio_out.mkdir(parents=True, exist_ok=True)
            import shutil
            for f in tmp_dir.glob("*.aiff"):
                shutil.copy(f, audio_out / f.name)
            print(f"→ 音声ファイル保存先: {audio_out}")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = args.log or (LOG_DIR / f"shadow-bench-{ts}.jsonl")
    save_jsonl(turns, log_path)

    print()
    print(analyze_turns(turns))


if __name__ == "__main__":
    main()
