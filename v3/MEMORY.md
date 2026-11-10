# MEMORY.md — Tomoko v3

追記のみ。確定した判断・未解決の疑問・気づきを書く。

## 確定した判断

### [2026-11-10] 作業場所と規則（duplex-handoff.md の仮置きをそのまま採用）
- 作業は `v3/`。`v1/`・`v2/` は読むだけ。
- Python は uv で 3.12 に固定（システムは 3.14。MLX・moshi_mlx の対応を優先）。
- 測定の百分位は nearest-rank 法。失敗試行は `attempts`/`failures` として分母に残す（`tomoko_v3/measure.py`）。
- 出典: `duplex-handoff.md` §2・§3。作成者の仮置きであり、変えるときはユーザーに確認する。

## 未解決の疑問（人間への確認待ち）

（なし）

## 気づき

（なし）

## 2026-11-10 追記（セッション1 途中）

### 確定した判断（ユーザーの回答、2026-11-10 09:1x）
- 26B（v2 の dflash、`v1/loras/lora/fused_model`）と VOICEVOX を v2 の起動手順で起動してよい。v2 のファイルは変更しない。
- RTX 3060 の Windows マシンに llama.cpp と uv + Python を入れてよい（推論なし・逐次ストリームの Gemma と、3060 側の STT の確認のため）。

### 気づき
- LM Studio（3060）の Gemma 4: `/v1/completions` に Gemma 4 の会話形式（`<|turn>user\n…<turn|>\n<|turn>model\n`）を自前で組むと、推論なしの返事が 20/20 で返る。
  ただしこの版の LM Studio は `/v1/completions` の stream を最後にまとめて送る（逐次でない）。`/api/v1/chat` の reasoning=off も本文が推論文で始まり、約 1.1 秒まとめて届いた。
- rustymimi（CPU）の Mimi は encode・decode が各 約 23〜31 ms。MLX 版 Mimi（GPU）は約 5〜11 ms。直列の 1 tick に収めるなら MLX 版を使う。
  MLX 版 Mimi の重みは 32 codebook 分あるので `mimi_202407(32)` で読み、出力を 8 本に切る。
- Moshi のテキスト列は「語の直前に EPAD（0）、長い語の後に PAD（3）」の形で、1 文字あたり約 1.4〜1.9 tick。
- 学習なしのテキスト列上書きでは、狙った文を話させられなかった（`_docs/v0/moshi-text-forcing.md`）。
