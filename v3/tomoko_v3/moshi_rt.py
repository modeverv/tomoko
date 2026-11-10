"""J-Moshi（moshi_mlx）を 1 tick = 80 ms ずつ手で回すための部品。

- `MoshiRunner`: Mimi の encode → LM 1 step（本体 + depformer）→ Mimi の decode を 1 tick として回し、
  部品ごとの所要時間を返す。`moshi_mlx.run_inference` のループと同じ手順。
- `ForcingTextSampler`: Moshi のテキスト列（Inner Monologue）を外から与えた token で置き換える。
  depformer は置き換え後の text token を条件に音声 token を出すので、音声もその文を話すかを試せる。
  成立する保証はない実験（duplex-handoff.md §2.2 の 2）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

FRAME = 1920  # 24 kHz の 80 ms
SAMPLE_RATE = 24000
TEXT_PAD = 3  # Moshi のテキスト列で「何も言わない」
TEXT_EPAD = 0  # パディングの終わり（次に語が来る）
SILENT_TEXT = (TEXT_PAD, TEXT_EPAD)


@dataclass
class ForcingState:
    # "words": モデルが語を出そうとした tick だけ置き換える / "contiguous": 毎 tick 置き換える /
    # "paced": モデルが最初の語を出そうとした tick から、外で決めた間隔（schedule）どおりに流す
    mode: str = "words"
    skip_words: int = 0  # 置き換えを始める前に、モデル自身の語を何 token 通すか
    after: str = "pad"  # 置き換え列を出し切った後: "pad" は黙らせる / "free" はモデルに任せる
    armed: bool = False
    queue: deque[int] = field(default_factory=deque)
    holding: bool = False
    passed: int = 0
    log: list[tuple[int, int, int]] = field(default_factory=list)  # (step, sampled, emitted)
    started: bool = False


def paced_schedule(token_ids: list[int], char_counts: list[int], ticks_per_char: float = 1.6) -> list[int]:
    """Moshi のテキスト列の形（語の前に EPAD、語の長さに応じた PAD）で置き換え列を並べる。

    先頭の語は EPAD を付けない（モデル自身が語を出そうとした tick で始めるため、直前に EPAD がある）。
    """
    out: list[int] = []
    for k, (tok, chars) in enumerate(zip(token_ids, char_counts)):
        if k > 0:
            out.append(TEXT_EPAD)
        out.append(tok)
        # 1 語あたり EPAD + 語 の 2 tick を既に使うので、その分を引いて残りを PAD で埋める
        out.extend([TEXT_PAD] * max(0, round(chars * ticks_per_char) - 2))
    return out


def decide(state: ForcingState, sampled: int, step: int) -> int:
    """sampled（モデルが選んだ text token）から、実際に流す token を決める。mlx に依存しない純関数部分。"""
    emitted = sampled
    if state.armed and state.queue:
        is_word = sampled not in SILENT_TEXT
        if state.mode == "contiguous":
            emitted = state.queue.popleft()
        elif state.mode == "paced":
            if state.started or is_word:
                state.started = True
                emitted = state.queue.popleft()
        elif is_word:
            if state.passed < state.skip_words:
                state.passed += 1
            else:
                emitted = state.queue.popleft()
        if not state.queue:
            state.holding = state.after == "pad"
    elif state.holding and sampled not in SILENT_TEXT:
        emitted = TEXT_PAD
    state.log.append((step, sampled, emitted))
    return emitted


class ForcingTextSampler:
    def __init__(self, base, state: ForcingState | None = None) -> None:
        self.base = base
        self.state = state or ForcingState()
        self.step = 0

    def push(self, token_ids: Iterable[int], *, mode: str | None = None, skip_words: int | None = None,
             after: str | None = None) -> None:
        s = self.state
        s.queue.extend(int(t) for t in token_ids)
        if mode is not None:
            s.mode = mode
        if skip_words is not None:
            s.skip_words = skip_words
        if after is not None:
            s.after = after
        s.passed = 0
        s.started = False
        s.holding = False
        s.armed = True

    def release(self) -> None:
        self.state.holding = False
        self.state.armed = False
        self.state.queue.clear()

    def __call__(self, logits):
        import mlx.core as mx

        token, logprobs = self.base(logits)
        sampled = int(token.reshape(-1)[0].item())
        emitted = decide(self.state, sampled, self.step)
        self.step += 1
        if emitted != sampled:
            token = mx.full(token.shape, emitted, dtype=mx.int32)
        return token, logprobs


@dataclass
class TickTiming:
    encode_ms: float
    lm_ms: float
    decode_ms: float

    @property
    def total_ms(self) -> float:
        return self.encode_ms + self.lm_ms + self.decode_ms


class MlxMimi:
    """moshi_mlx の MLX 版 Mimi を rustymimi.Tokenizer と同じ入出力（numpy）で包む。GPU で動く。"""

    def __init__(self, mimi_file: str, num_codebooks: int = 8) -> None:
        import mlx.core as mx
        from moshi_mlx import models

        self._mx = mx
        # 重みは 32 codebook 分ある。読み込みは 32 で行い、encode の出力を num_codebooks で切る
        self.num_codebooks = num_codebooks
        self.mimi = models.mimi.Mimi(models.mimi_202407(32))
        self.mimi.load_pytorch_weights(mimi_file, strict=True)
        self.mimi.warmup()
        self.mimi.reset_all()

    def encode_step(self, pcm):
        mx = self._mx
        codes = self.mimi.encode_step(mx.array(pcm))[:, : self.num_codebooks]
        mx.eval(codes)
        return codes  # (B, K, T)。mx.array のまま返す（numpy 往復を避ける）

    def decode_step(self, codes):
        import numpy as np

        mx = self._mx
        out = self.mimi.decode_step(mx.array(codes.astype(np.int32)))
        return np.array(out)


class MoshiRunner:
    def __init__(self, hf_repo: str = "akkikiki/j-moshi-ext-mlx-q4", quantized: int | None = 4,
                 max_steps: int = 4000, text_temp: float = 0.7, audio_temp: float = 0.8,
                 forcing: bool = False, seed: int = 299792458, mimi_backend: str = "rust") -> None:
        import mlx.core as mx
        import mlx.nn as nn
        import rustymimi
        import sentencepiece
        from huggingface_hub import hf_hub_download
        from moshi_mlx import models, utils

        weight = {4: "model.q4.safetensors", 8: "model.q8.safetensors", None: "model.safetensors"}[quantized]
        self.text_tokenizer = sentencepiece.SentencePieceProcessor(
            hf_hub_download(hf_repo, "tokenizer_spm_32k_3.model"))
        mimi_file = hf_hub_download(hf_repo, "tokenizer-e351c8d8-checkpoint125.safetensors")
        mx.random.seed(seed)
        cfg = models.config_v0_1()
        model = models.Lm(cfg)
        model.set_dtype(mx.bfloat16)
        if quantized is not None:
            nn.quantize(model, bits=quantized, group_size=32 if quantized == 4 else 64)
        model.load_weights(hf_hub_download(hf_repo, weight), strict=True)
        model.warmup()
        self.model = model
        self.other_codebooks = cfg.other_codebooks
        base_text = utils.Sampler(temp=text_temp)
        self.text_sampler = ForcingTextSampler(base_text) if forcing else base_text
        self.gen = models.LmGen(model=model, max_steps=max_steps, text_sampler=self.text_sampler,
                                audio_sampler=utils.Sampler(temp=audio_temp), check=False)
        if mimi_backend == "mlx":
            self.mimi = MlxMimi(mimi_file, num_codebooks=8)
        else:
            self.mimi = rustymimi.Tokenizer(mimi_file, num_codebooks=8)
        self._mx = mx
        self._np = __import__("numpy")

    def encode_text(self, text: str) -> list[int]:
        return list(self.text_tokenizer.encode(text))

    def paced_tokens(self, text: str, ticks_per_char: float = 1.6) -> list[int]:
        ids = self.encode_text(text)
        chars = [max(1, len(self.text_tokenizer.id_to_piece(t).replace("▁", "").strip())) for t in ids]
        return paced_schedule(ids, chars, ticks_per_char)

    def piece(self, token: int) -> str:
        if token in SILENT_TEXT:
            return ""
        return self.text_tokenizer.id_to_piece(token).replace("▁", " ")

    def tick(self, pcm: "object") -> tuple[int, "object | None", TickTiming]:
        """pcm: float32 の (1920,)。戻り値: (text token, 出力 pcm または None, 所要時間)。"""
        mx, np = self._mx, self._np
        t0 = time.perf_counter_ns()
        codes = self.mimi.encode_step(np.asarray(pcm, dtype=np.float32)[None, None, :])
        other = mx.array(codes).transpose(0, 2, 1)[:, :, : self.other_codebooks]
        t1 = time.perf_counter_ns()
        text_token = self.gen.step(other[0])
        text_id = int(text_token[0].item())
        audio_tokens = self.gen.last_audio_tokens()
        if audio_tokens is not None:
            audio_np = np.array(audio_tokens[:, :, None]).astype(np.uint32)
        t2 = time.perf_counter_ns()
        out = None
        if audio_tokens is not None:
            out = self.mimi.decode_step(audio_np)[0, 0]
        t3 = time.perf_counter_ns()
        return text_id, out, TickTiming((t1 - t0) / 1e6, (t2 - t1) / 1e6, (t3 - t2) / 1e6)
