# V0-1 一次資料の確認

- 確認日: 2026-11-10（以下すべて同日に確認）
- 確認者: Claude（サブエージェント。調査依頼元は duplex-handoff.md §6 V0-1）
- 読み方の区別:
  - 「確認」= 一次資料（HF のモデルカード本文 / HF API の JSON / GitHub の README・ソースコード / arXiv 本文 / Apple の公式ドキュメント JSON）を自分で取得して読んだもの。
  - 「本文要約経由」= arXiv HTML 本文を WebFetch で取得したが、読み取りは要約モデルを通したもの。数値は要約が本文から引用したものだが、自分で全文を通読してはいない。
  - 「未確認（要約のみ）」= 検索結果の要約しか見ていないもの。
- HF の取得方法: `https://huggingface.co/api/models/<id>`（cardData・ファイル一覧）と `https://huggingface.co/<id>/raw/main/README.md`。
- この文書はソフトウェアを導入・実行していない。動作確認は V0-2 以降の仕事。

---

## 1. 土台 LLM の候補（2〜4B 級、日本語）

| モデル | ライセンス（カード記載） | PyTorch(HF transformers) | MLX 重み | 音声入力エンコーダー | 出典・読んだもの |
|---|---|---|---|---|---|
| `google/gemma-4-E2B-it` | **Apache 2.0**（カード冒頭 "License: Apache 2.0"、`license_link` は ai.google.dev/gemma/docs/gemma_4_license。リンク先は Apache 2.0 の本文で追加条項は見当たらない＝本文要約経由） | あり（`library_name: transformers`、`Gemma4ForConditionalGeneration`） | あり: `mlx-community/gemma-4-e2b-it-4bit`（5bit/6bit もある）。`mlx_vlm.convert` で変換 | **内蔵**。Audio Encoder 約 300M。用途は ASR と音声翻訳。**音声は最長 30 秒**（カード "Audio supports a maximum length of 30 seconds"） | モデルカード README、config.json |
| `google/gemma-4-E4B-it` | Apache 2.0（同上） | あり | あり: `mlx-community/gemma-4-e4b-it-4bit` | 内蔵（E2B と同じ約 300M） | モデルカード README（E2B との差分はモデル名のみ） |
| `Qwen/Qwen3-4B` / `Qwen/Qwen3-1.7B` | Apache 2.0 | あり | あり: `mlx-community/Qwen3-4B-4bit`、`mlx-community/Qwen3-1.7B-4bit`（README 取得で存在確認） | なし（テキストのみ） | モデルカード。"100+ languages" と記載。日本語の個別評価はカードに無い |
| `sbintuitions/sarashina2.2-3b-instruct-v0.1` | **MIT** | あり | あり: `mlx-community/sarashina2.2-3b-instruct-v0.1`（4bit / 8bit も） | なし | モデルカード（Language: Japanese、Japanese MT Bench 記載） |
| `llm-jp/llm-jp-3-3.7b-instruct` | Apache 2.0 | あり | あり: `mlx-community/llm-jp-3-3.7b-instruct` | なし | モデルカード |
| 参考: `Qwen/Qwen2.5-Omni-3B` | `qwen-research`（**研究用ライセンス、商用不可の可能性。本文未読**） | あり | 未確認 | 内蔵（Thinker-Talker、ストリーミング応答をうたう） | モデルカード冒頭のみ |

Gemma 4 の内蔵音声エンコーダーをストリーミングで使えるか:
- カードにストリーミング利用の記述は**無い**。用途は「音声区間を丸ごと渡して書き起こす」形の例だけ（30 秒上限）。
- `config.json` の `audio_config`（`model_type: gemma4_audio`）は `attention_chunk_size: 12`、`attention_context_left: 13`、`attention_context_right: 0`、12 層、hidden 1024、`output_proj_dims: 1536`。**右文脈 0 のチャンク注意なので構造上は因果的に回せそうに見えるが、これは設定値からの私の推測**。畳み込み（`conv_kernel_size: 5`）が因果的か、1 フレームが何 ms か、transformers / mlx-vlm に逐次実行の API があるかは未確認。
- `mlx-community/gemma-4-e2b-it-4bit` のカードは `image-text-to-text` タグで、音声入力の使い方は書かれていない。mlx-vlm で音声入力が動くかは未確認。
- 注: `mlx-community/gemma-4-e2b-it-4bit` のカード metadata は `license: gemma` になっているが、元モデルのカードは Apache 2.0。変換カード側の記載が古い/誤りと思われる（元カードを優先）。

URL:
- https://huggingface.co/google/gemma-4-E2B-it / https://huggingface.co/google/gemma-4-E4B-it
- https://huggingface.co/google/gemma-4-E2B-it/raw/main/config.json
- https://ai.google.dev/gemma/docs/gemma_4_license（本文要約経由）
- https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit / https://huggingface.co/mlx-community/gemma-4-e4b-it-4bit
- https://huggingface.co/Qwen/Qwen3-4B / https://huggingface.co/Qwen/Qwen3-1.7B
- https://huggingface.co/sbintuitions/sarashina2.2-3b-instruct-v0.1
- https://huggingface.co/llm-jp/llm-jp-3-3.7b-instruct
- https://huggingface.co/Qwen/Qwen2.5-Omni-3B

---

## 2. Mimi（Kyutai の音声コーデック）

| 項目 | 内容 | 出典 |
|---|---|---|
| ライセンス | `kyutai/mimi` カード: **CC-BY 4.0**。moshi リポジトリ README: 重みは CC-BY 4.0、Python コードは MIT、Rust バックエンドは Apache | HF カード、GitHub README "License" 節 |
| フレームレート | **12.5 Hz**、24 kHz 入力、1.1 kbps（カード）。MLX 実装 `mimi_202407()` でも `frame_rate=12.5`、`sample_rate=24000` | カード、`moshi_mlx/models/mimi.py` |
| コードブック | MLX 設定で `quantizer_bins=2048`、`quantizer_dim=256`。本数は呼び出し側で指定（Moshi は 8 本を使用。`local.py` は `[:, :8]`） | `mimi.py`、`local.py` |
| MLX での実行 | (a) `moshi_mlx` パッケージ内の `models/mimi.py`（`Mimi.encode` / `encode_step` / `decode_step`）。(b) `rustymimi`（Rust 実装の Python バインディング。`moshi_mlx.local` は `rustymimi.StreamTokenizer(mimi_file)` でトークン化している）。(c) 第三者の `mlx-community/mimi-encoder-mlx`（エンコーダーのみ、fp32、Swift 向け、CC-BY 4.0。出力は 16 コードブックの index） | moshi README、`local.py`、HF カード |
| 量子化前の連続特徴 | **取り出せる**。PyTorch 版 `moshi/models/compression.py` に `encode_to_latent(x, quantize=False)` があり「unquantized embeddings」を返す。MLX 版には専用 API は無いが、`encode_step` の中身が `encoder.step → encoder_transformer → downsample.step → quantizer.encode` なので、`quantizer.encode` の直前で止めれば得られる（コードを読んでの判断、未実行） | `compression.py` L338–404、`mimi.py` L142–165 |
| 連続特徴の次元 | **512 次元 / 12.5 Hz**（SEANet `dimension=512`、量子化器の `input_dimension = 512`、内部の量子化次元は 256）。rustymimi の `StreamTokenizer` はトークン（整数）しか返さない使われ方で、連続特徴の取り出しはできないと思われる（rustymimi の API 文書は未読） | `loaders.py` `_seanet_kwargs`、`mimi.py` |

URL:
- https://huggingface.co/kyutai/mimi
- https://github.com/kyutai-labs/moshi （README）
- https://github.com/kyutai-labs/moshi/blob/main/moshi_mlx/moshi_mlx/models/mimi.py
- https://github.com/kyutai-labs/moshi/blob/main/moshi/moshi/models/compression.py
- https://github.com/kyutai-labs/moshi/blob/main/moshi/moshi/models/loaders.py
- https://huggingface.co/mlx-community/mimi-encoder-mlx

---

## 3. 日本語のストリーミング ASR エンコーダー（ローカル、学習利用可）

| 候補 | ライセンス | ストリーミング | MLX | 備考 | 出典 |
|---|---|---|---|---|---|
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | `openmdw-1.1`（カード "This model is ready for commercial use."。**OpenMDW 本文は未読**） | **あり**。Cache-Aware FastConformer-RNNT、24 層、チャンク 80/160/320/560/1120 ms を実行時に選択。ja-JP は "Transcription-ready" 19 ロケールに含まれる | 第三者変換あり（`mlx-community/nemotron-3.5-asr-streaming-0.6b` ほか。中身未読） | 多言語 40 ロケール。日本語単独の精度は未読 | HF カード |
| `ayousanz/kodama-ja-streaming-small` | Apache 2.0（元 `moonshine-ai/moonshine-streaming-small` は MIT とカードに記載） | **あり**（Moonshine Streaming、enc/dec 各 10 層） | 未確認（ONNX 資産を同梱） | ReazonSpeech v2 全量 35,000 時間でフル FT と記載。個人公開。ストリーミングで CER +0.054 悪化と自己申告 | HF カード |
| `reazon-research/reazonspeech-nemo-v2` | Apache 2.0 | **カードに記載なし**。Longformer 注意（局所 256 + 大域トークン 1）で長尺一括処理向け | なし | 619M、RNN-T | HF カード |
| `reazon-research/reazonspeech-k2-v2` | Apache 2.0 | **カードに記載なし**。「~30 秒まで」 | なし | Zipformer 159M、RNN-T | HF カード |
| `nvidia/parakeet-tdt_ctc-0.6b-ja` | CC-BY-4.0 | **カードに記載なし**（FastConformer-TDT-CTC。カード中の "Streaming" は NeMo/Riva の宣伝文のみ） | なし | ReazonSpeech v2.0（35k 時間）で学習 | HF カード |
| `kotoba-tech/kotoba-whisper-v2.0` | Apache 2.0 | 非ストリーミング（Whisper 蒸留） | 未確認 | 参考 | HF カード |
| `kyutai/stt-1b-en_fr` 等 | CC-BY 4.0 | あり | あり | **英仏のみ。日本語モデルは kyutai の HF に無い**（`author=kyutai` の一覧で確認） | HF カード・API |

注意:
- ReazonSpeech 系は**モデル**が Apache 2.0 / CC-BY 4.0 でも、学習データ ReazonSpeech コーパス自体の利用条件は別（本調査ではコーパスの条件は未読）。
- 学習目的で「エンコーダーを凍結して特徴量だけ使う」ならモデルのライセンスが効く。上の候補はどれもカード上は学習利用を禁じていない（OpenMDW は本文未読）。
- ストリーミング設計が明記されているのは Nemotron 3.5 と kodama の 2 つだけ。

URL:
- https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b
- https://huggingface.co/ayousanz/kodama-ja-streaming-small
- https://huggingface.co/reazon-research/reazonspeech-nemo-v2
- https://huggingface.co/reazon-research/reazonspeech-k2-v2
- https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja
- https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0
- https://huggingface.co/kyutai/stt-1b-en_fr

---

## 4. テキスト出力型 / 凍結 LLM 型 full-duplex の先行研究

全件 arXiv HTML を WebFetch で取得（本文要約経由）。版は arXiv API で確認。

| 論文 | 土台 LLM | 音声入力 | 出力 | 学習段階 | データ量 | pad/無音の損失重み | 話し始めラベルの作り方 |
|---|---|---|---|---|---|---|---|
| **SALM-Duplex** arXiv:2505.15670v4（NVIDIA） | TinyLlama-1.1B-chat（**凍結しない**） | 100M のストリーミング CTC エンコーダー（右文脈 80 ms、80 ms/frame）+ アダプター。**連続特徴** | **テキストと音声コーデック（NanoCodec 12.5 Hz、4 FSQ）を並列に出力**。別の TTS は無い | 主学習は 1 本（エンコーダーと LLM を両方学習、音声テキスト事前学習は省略）。声の個人化は別段階で 21k 発話 | 合計 約 26.7k 時間（ASR-QA 20k、UltraChat 3k、内製 SFT 3k、他）。大半は TTS（KoelTTS）合成 | 記載なし。テキスト損失 3 : 音声損失 1 | データ構成から作る。相手側に無音を入れ、エージェント発話の前に **0.64 秒の無音**を挿入。割り込みはユーザーが切った後 0.64 秒でエージェント側を無音化 |
| **Freeze-Omni** arXiv:2411.00774v5 | Qwen2-7B-Instruct（**完全凍結**） | チャンク単位ストリーミングエンコーダー（24 層、約 350M、12.5 Hz）+ アダプター | テキスト → 別の AR/NAR 音声デコーダー（TiCodec 40 Hz） | 入力側 3 段: (1) エンコーダーを CTC で ASR 学習 (2) エンコーダー+アダプターを凍結 LLM に接続 (3) 多ターン QA で prompt 埋め込みと**状態予測ヘッド**のみ学習 | (1)(2) 内製 ASR 110,000 時間、(3) 60,000 件の多ターン QA（質問は TTS 合成） | 記載なし | 外部 VAD（Silero）で発話開始を検出し、LLM 最終層の後の 3 クラス分類（0=聞き続ける / 1=割り込み・新規生成 / 2=話し終わり）。**ラベル生成方法は記載なし** |
| **MinMo** arXiv:2501.06282v1（Alibaba） | Qwen2.5-7B-Instruct（整列段は凍結、SFT で LoRA） | SenseVoice-Large エンコーダー（約 636M） | テキスト + 別の音声デコーダー（CosyVoice 2 由来、テキスト 5 トークンごとに音声 15 トークン） | 4 段: 音声→テキスト整列 / テキスト→音声整列 / 音声→音声整列 / duplex 整列 | 約 1.2M 時間（S2T）、170k 時間（TTS）、約 10k 時間（S2S）、**duplex 4,000 時間**（人間同士の長尺会話。内製約 3,000 h + 合成約 1,000 h） | 記載なし | LLM 隠れ状態を入力にする 1 層 Transformer の duplex 予測器（約 18M）。**ヒューリスティック**: ユーザー発話の終わりでアシスタントの番、アシスタント発話後 T〜N(0.6, 0.4²) 秒でユーザーの番、割り込めない区間を相槌 |
| 参考: **Moshi** arXiv:2410.00037（Kyutai） | 自前 7B | Mimi 離散トークン | テキスト（Inner Monologue）+ 音声トークン | 事前学習 → 多ストリーム → Fisher → 指示調整 | Fisher 2,000 h、教師付き多ストリーム 170 h、合成指示データ 20k h 超 | 事前学習で pad の重みを 50% 減と本文にある（本文要約経由）。`moshi-finetune` の既定は `text_padding_weight: .5` | 単語アラインメントは whisper-timestamped で作成 |

所見:
- **SALM-Duplex はテキストだけを出すモデルではない**（音声コーデックも同じ LLM が出す）。T 案（テキスト出力 + 外部 TTS）に近いのは Freeze-Omni と MinMo。
- 「いつ話すか」を LLM 本体のテキスト列で決める（制御トークン型）形は、上の中では SALM-Duplex（データ構成で暗黙に学ぶ）が最も近い。Freeze-Omni / MinMo は**別ヘッド**で判定する。
- pad / 無音トークンの損失重みを明記しているのは Moshi 系（0.5）だけ。

URL:
- https://arxiv.org/abs/2505.15670 （HTML: https://arxiv.org/html/2505.15670）
- https://arxiv.org/abs/2411.00774
- https://arxiv.org/abs/2501.06282 （本文 127k 字のうち先頭 100k 字までを要約経由で読んだ）
- https://arxiv.org/abs/2410.00037 （v2 HTML の先頭 100k 字までを要約経由）

---

## 5. Apple の音声合成（常駐プロセスから）

すべて Apple 公式ドキュメントの JSON（`developer.apple.com/tutorials/data/documentation/avfaudio/...json`）を取得して読んだ。

| API | 内容 | 対応 OS |
|---|---|---|
| `AVSpeechSynthesizer.write(_:toBufferCallback:)` | 「Generates speech for the utterance and invokes the callback with the audio buffer」。音声バッファを受け取って保存・加工できる | macOS 10.15+ |
| `AVSpeechSynthesizer.write(_:toBufferCallback:toMarkerCallback:)` | 音声バッファと**マーカー**（メタデータ）を受け取る | **macOS 13.0+** |
| `AVSpeechSynthesisMarker` | `mark`（種類）、`textRange`（元テキストの範囲）、`byteSampleOffset`（**音声バッファ内のバイト位置**）、`phoneme`、`bookmarkName`。種類は `word` / `sentence` / `paragraph` / `phoneme` / `bookmark` | macOS 13.0+ |
| `AVSpeechSynthesizerDelegate.speechSynthesizer(_:willSpeakRangeOfSpeechString:utterance:)` | 「通常は単語ごと」に、これから話す範囲を通知。文字範囲を返す | macOS 10.14+ |
| `AVSpeechSynthesisVoiceQuality` | `default` / `enhanced`（要ダウンロード）/ `premium`（要ダウンロード） | — |
| `AVSpeechSynthesisVoice.speechVoices()` | 端末にある全音声を返す | macOS 10.14+ |

判断:
- **`write()` で単語位置を得る正規の方法は `toMarkerCallback:` 付きの版**（macOS 13+）。`byteSampleOffset` でバッファ内の位置と文字範囲が対応する。
- 「`write()` の実行中に delegate の `willSpeakRangeOfSpeechString` が呼ばれるか」は**ドキュメントに記載がない**（未確認）。
- 日本語音声で word マーカーが実際に単語単位（形態素単位）で出るかは**ドキュメントに記載なし。V0 で実測が必要**。
- この Mac（macOS 27.0.1）の `say -v '?'` で ja_JP の音声を確認: **Kyoko（2 件。品質違いと思われる）**、Eddy、Flo、Grandma、Grandpa、Reed、Rocko、Sandy、Shelley。**Otoya は未インストール**。
- Otoya が男性の日本語音声であること、Kyoko/Otoya に高品質版があることは記事・検索要約のみ → 未確認（要約のみ）。

URL:
- https://developer.apple.com/documentation/avfaudio/avspeechsynthesizer/write(_:tobuffercallback:)
- https://developer.apple.com/documentation/avfaudio/avspeechsynthesizer/write(_:tobuffercallback:tomarkercallback:)
- https://developer.apple.com/documentation/avfaudio/avspeechsynthesismarker
- https://developer.apple.com/documentation/avfaudio/avspeechsynthesizerdelegate/speechsynthesizer(_:willspeakrangeofspeechstring:utterance:)
- https://developer.apple.com/documentation/avfaudio/avspeechsynthesisvoicequality
- 検索要約のみ: https://news.mynavi.jp/article/osxhack-116/ ほか

---

## 6. `moshi_mlx` で日本語モデルを動かす

### `akkikiki/j-moshi-ext-mlx-q4` / `-q8` / `j-moshi-ext-mlx`（bf16）

- 3 つとも存在（最終更新 2025-02-12）。カード本文はほぼ同じ。
- ライセンス: **CC-BY-NC 4.0**。「その他規約・制限等においてはオリジナルのモデルである `nu-dialogue/j-moshi-ext` 並びに `kyutai/moshi` に準拠」。
- カード記載の起動手順（そのまま引用）:
  ```
  pip install moshi_mlx
  python -m moshi_mlx.local --hf-repo akkikiki/j-moshi-ext-mlx-q4 --quantized 4
  python -m moshi_mlx.local --hf-repo akkikiki/j-moshi-ext-mlx-q8 --quantized 8
  python -m moshi_mlx.local --hf-repo akkikiki/j-moshi-ext-mlx
  ```
- ファイル: q4 は `model.q4.safetensors`、q8 は `model.q8.safetensors`、共通で `tokenizer_spm_32k_3.model`（rinna の日本語 SentencePiece）と `tokenizer-e351c8d8-checkpoint125.safetensors`（Mimi）。
- **必要な `moshi_mlx` の版はカードに書かれていない**。
  - PyPI の最新は `moshi_mlx` **0.3.0**（2025-08-04）。依存: `mlx>=0.26,<0.27`、`rustymimi==0.4.1`、`huggingface-hub<0.29`、Python >=3.10（README は 3.12 推奨。3.12 以外は Rust ツールチェーンが要る場合あり）。
  - 現行 `main` の `moshi_mlx/local.py` を読んだ: `--quantized` 4/8 に応じて `model.q4.safetensors` / `model.q8.safetensors` を、トークナイザーは `tokenizer_spm_32k_3.model`、Mimi は `tokenizer-e351c8d8-checkpoint125.safetensors` を HF から取り、`models.config_v0_1()`（text_card 32000）で読み込む。**ファイル名は akkikiki のリポジトリと一致する**ので、現行版でも読める見込み（未実行）。q4 は group_size 32、q8 は 64 で量子化構造を作ってから `load_weights(strict=True)`。
  - `local.py` は端末で動くローカル版（マイク/スピーカー）。web UI は `moshi_mlx.local_web`（README）。
- 元モデル `nu-dialogue/j-moshi-ext`: **CC-BY-NC 4.0、研究目的の利用を想定**。学習に J-CHAT、日本語 Callhome、CSJ、旅行代理店対話、内製の雑談・相談コーパス、テキスト対話からの合成データ。128×V100 32GB。公式の PyTorch 実行は `pip install moshi<=0.2.2` と 24GB 以上の Linux GPU（「MacOS には対応していません」= PyTorch 版の話）。

URL:
- https://huggingface.co/akkikiki/j-moshi-ext-mlx-q4
- https://huggingface.co/akkikiki/j-moshi-ext-mlx-q8
- https://huggingface.co/akkikiki/j-moshi-ext-mlx
- https://huggingface.co/nu-dialogue/j-moshi-ext
- https://pypi.org/pypi/moshi_mlx/json
- https://github.com/kyutai-labs/moshi/blob/main/moshi_mlx/moshi_mlx/local.py

---

## 7. M 案の確認

### 7.1 LLM-jp-Moshi-v1

- HF id: **`llm-jp/llm-jp-moshi-v1`**（最終更新 2026-10-26、gated ではない）。
- ライセンス: **Apache 2.0**（カード metadata と "Terms of Use" 節。なりすまし・詐欺目的は意図しない旨の注記あり）。
- 土台: `kyutai/moshiko-pytorch-bf16`。学習データ: **J-CHAT（約 69,000 時間）+ LLM-jp-Zoom1（約 1,000 時間、内製の Zoom 雑談、近日公開予定）**。
- 声: カードに声の説明は無い。Mimi は moshiko のもの。（土台 moshiko は男性声とされるが、LLM-jp-Moshi-v1 の声はカードに記載なし → 未確認）
- 論文: IWSDS 2026 "Effects of dialogue corpora properties on fine-tuning a Moshi-based spoken dialogue model"（aclanthology 2026.iwsds-1.10）。本文は未読。
- 公式の実行: `pip install moshi<=0.2.2 sphn==0.1.12` → `python -m moshi.server --hf-repo llm-jp/llm-jp-moshi-v1`。「24GB 以上の VRAM を搭載した Linux GPU マシンが必要。MacOS には対応していません」。
- **MLX 重み: 公開されていない**（HF 検索 `llm-jp-moshi` の結果は本体、`MtCedar/llm-jp-moshi-v1-candle`、`shunby/llm-jp-moshi-v1-q8`（PyTorch int8、Apache 2.0）の 3 件のみ）。
- **変換はできる見込み**: `moshi_lm_kwargs.json` が moshiko と同じ構成（dim 4096、32 層、text_card 32000、n_q 16、dep_q 8、delays も同じ）で、ファイル名も J-Moshi と同じ。Kyutai の moshi リポジトリに `scripts/import_mlx.py`（PyTorch checkpoint → MLX safetensors）と `scripts/quantize_mlx.py`（`--config v0_1`、`--bits` 4/8）がある。変換後は 6 章と同じ `moshi_mlx.local --hf-repo <ローカル/自作repo>` で読める見込み。**いずれも未実行**。

URL:
- https://huggingface.co/llm-jp/llm-jp-moshi-v1 （README、moshi_lm_kwargs.json）
- https://huggingface.co/shunby/llm-jp-moshi-v1-q8
- https://github.com/kyutai-labs/moshi/tree/main/scripts （import_mlx.py、quantize_mlx.py の引数を読んだ）

### 7.2 KAME（Sakana AI）

- 論文: **arXiv:2510.02327**（v1 2025-09-26、v2 2026-05-25）、ICASSP 2026。So Kuroki, Yotaro Kubo, Takuya Akiba, Yujin Tang。
- コード・重みが**公開されている**:
  - 推論 `SakanaAI/kame`（GitHub、**MIT**）。`kame.server_oracle` は **OpenAI API キー必須、ASR は Google Cloud Speech-to-Text、英語（en-US）設定**、同時 1 セッションのみ（README）。
  - 学習 `SakanaAI/kame_finetune`（GitHub）。ステレオ音声 + 単語タイムスタンプ + 任意の `oracle_raw/*.json` を入力にする前処理〜学習。LLM 呼び出しなしの「Randomized Training Guidance」も用意。
  - 重み `SakanaAI/kame`（HF、`model.safetensors`、config は moshiko と同構成。**カードにライセンス記載なし**）。
- 入力列の足し方（**ソースコードで確認**）: Moshi の 3 ストリームに **4 本目の oracle ストリーム**を追加。`oracle_emb`（text_emb と同じ語彙サイズの埋め込み）を新設し、**`text_emb = text_emb + oracle_emb` と text 埋め込みに加算**（`src/kame/models/lm.py` L404–410）。`oracle_emb` は checkpoint に無ければ **`text_emb` の重みで初期化**（`backfill_oracle_embedding_from_text`）。学習時に text_emb と共有する "tie" モードもある。→ duplex.md の「ゼロ初期化」とは異なる。
- 論文の記述（本文要約経由）: oracle は Inner Monologue と同じトークナイザー、文ごとに境界用特殊トークン。前段は 80 ms ごと、後段 LLM は 100–500 ms ごとに更新、重なりは最新の応答を優先。学習時は一定間隔 + ランダムな揺らぎで oracle の到着を注入。
- 学習データ（本文要約経由）: **合成**。MMLU-Pro / GSM8K / HSSBench の QA を会話調に直し TTS + 単語アライナーで音声化。**計 56,582 対話**（22,800 + 11,742 + 22,040）。**時間数の記載なし**。oracle は「シミュレーター LLM」が完成度 r(t)=n/N に応じたヒント段階 0–5 で生成。
- 計算資源: **記載なし**（「その他のハイパーパラメーターは元の Moshi 論文と同じ」、音声損失重み 1.5 のみ）。

URL:
- https://arxiv.org/abs/2510.02327 （HTML v2 を要約経由で 2 回読んだ）
- https://github.com/SakanaAI/kame （README、`src/kame/models/lm.py`）
- https://github.com/SakanaAI/kame_finetune （README、`models/oracle_embedding_utils.py`、`models/moshi_for_finetuning.py`）
- https://huggingface.co/SakanaAI/kame
- 未確認（要約のみ）: https://pub.sakana.ai/kame/ 、https://sakana.ai/kame-icassp-2026/ （後段を GPT-4.1 / Claude / Gemini に差し替え可能との記述）

### 7.3 `kyutai-labs/moshi-finetune`

- README の推奨設定（LoRA rank 128、`duration_sec: 100`、`batch_size: 16`、`max_steps: 2000`、`gradient_checkpointing: true`）で:
  - **1×H100: ピーク割り当てメモリ 39.6 GB**（≈12k tokens/s）、8×H100: 23.7 GB/GPU。
  - OOM 時は `batch_size` を下げ、それでも駄目なら `duration_sec` を下げる（推論時に早く黙るようになる恐れ、と注意書き）。
- 既定 `example/moshi_7B.yaml`: `first_codebook_weight_multiplier: 100.`、**`text_padding_weight: .5`**、lr 2e-6。
- 公式 Colab ノートブック（`tutorials/moshi_finetune.ipynb`）: 「Google Colab で **A100 GPU** を使って実行できる」と明記。ノートブック内の設定は **`batch_size: 1`、`duration_sec: 100`、`max_steps: 300`**、LoRA rank 128。
- Colab の GPU への当てはめ（README の数値からの**推測**を含む）:

| GPU | 判断 | 根拠 |
|---|---|---|
| H100 80GB | 回る | README の実測（batch 16 で 39.6 GB） |
| A100 80GB | 回る見込み | H100 の値がメモリ的に収まる（速度は未記載） |
| A100 40GB | batch 1 なら回る（公式 Colab が A100 で batch 1）。batch 16 は 39.6 GB でぎりぎり/不可の可能性 | ノートブック。A100 の 40/80 の区別はノートブックに無い |
| L4 24GB | **記載なし。未確認**。bf16 の 7B 本体だけで約 14 GB（duplex.md の値）なので batch 1・短い duration なら可能性はあるが根拠なし | — |
| T4 16GB | **記載なし。おそらく不可**（bf16 7B 本体 約 14 GB + 活性化。T4 は bf16 非対応という一般知識もある） | 推測 |

- データ形式: ステレオ wav（左=モデル側、右=ユーザー側）+ `.jsonl`（path, duration）+ 各 wav の単語タイムスタンプ付き `.json`（`annotate.py` で Whisper により自動生成可）。
- 学習後の推論は PyTorch の `moshi.server --lora-weight ... --config-path ...`。MLX 側には `scripts/import_mlx_lora.py`（LoRA を合わせて MLX safetensors を作る）がある（引数のみ確認、未実行）。
- 日本語派生への適用: nu-dialogue は自前 fork（`nu-dialogue/moshi-finetune`）で J-Moshi を学習したとカードに記載。kyutai 版で `moshi_paths.hf_repo_id` を日本語モデルに向けて使えるかは未確認（トークナイザー名は同じ）。

URL:
- https://github.com/kyutai-labs/moshi-finetune （README、`example/moshi_7B.yaml`、`tutorials/moshi_finetune.ipynb`）

---

## 8. 推論時に Moshi のテキスト列を上書きする（text forcing）

- **Kyutai 自身の MLX 実装に、その仕組みがある（ソースコードで確認）**:
  - `moshi_mlx/models/lm.py` の `_sample()` は、テキストトークンをサンプルした直後に `on_text_hook(text_token)` を呼び、**その後で** depformer（音声トークン生成）に渡す。
  - `moshi_mlx/models/generate.py` の `LmGen` は `on_text_hook` を受け取り、返ってきたテキストトークンを `gen_sequence` に書き、次の tick の入力にする。
  - `moshi_mlx/models/tts.py`（Kyutai TTS）の `_on_text_hook` は `text_tokens[:] = ...` で**サンプル結果をその場で書き換え**ている。モデルが `new_word` を出したら台本の次の単語を差し込み、`pad` を強制する状態機械（`StateMachine.process`）を持つ。
  - つまり「テキスト列をフックで上書きし、その tick の音声生成と次 tick の文脈に反映させる」API が `LmGen` に既にある。`local.py` は `on_text_hook` を渡していないが、同じ `LmGen` を使っているので差し込める形。
- ただし Kyutai TTS は**その用途のために学習したモデル**（テキストが音声より 16 step = 1.28 秒先行、`kyutai/tts-1.6b-en_fr` カード）。対話用 Moshi（テキストと音声の遅れがほぼ 0、delays は [0,0,1,…]）で単語を強制して、その単語どおりに発音するかは**誰も文書化していない**（検索で見つからず）。
- Moshi 論文（本文要約経由）: テキストと音声の遅延を変えることで同じ枠組みからストリーミング ASR / TTS を作れる、TTS では「音声の内容はテキストで決まる」。DSM 論文（arXiv:2509.08753、abstract を確認）が Kyutai STT/TTS の一般化。
- HF transformers の Moshi 実装は `generate()` にテキスト履歴 `input_ids` を渡せる（検索要約のみ。強制で発話内容が変わるかの記述はない）。
- 私の評価: 機構としては数行で試せる（V0-2 の J-Moshi 起動後、`LmGen(on_text_hook=...)` で上書き）。対話モデルは「テキストを先に書いてから同 tick〜1 tick 後に声」なので、上書きした単語が声になる余地はあるが、**語の途中のトークン境界・速度制御・自然さは未知**。KAME が学習で oracle 列を足したのは、上書きだけでは足りないと判断したからと読める（論文にそうは書いていない。私の推測）。

URL:
- https://github.com/kyutai-labs/moshi/blob/main/moshi_mlx/moshi_mlx/models/lm.py （L464–528）
- https://github.com/kyutai-labs/moshi/blob/main/moshi_mlx/moshi_mlx/models/generate.py （L62–135）
- https://github.com/kyutai-labs/moshi/blob/main/moshi_mlx/moshi_mlx/models/tts.py （L136–260、L590–625）
- https://huggingface.co/kyutai/tts-1.6b-en_fr
- https://arxiv.org/abs/2509.08753 （abstract）
- 未確認（要約のみ）: https://huggingface.co/docs/transformers/model_doc/moshi

---

## duplex.md の記述と食い違う可能性がある点

1. **§3「LLM-jp-Moshi-v1: 日本語雑談約 1,000 時間で学習」** → カードでは **J-CHAT 約 69,000 時間 + LLM-jp-Zoom1 約 1,000 時間**。1,000 時間は Zoom 分だけ。
2. **§3「LLM-jp-Moshi-v1 第一候補・MLX 変換済み重みの有無は未確認」** → MLX 重みは**公開されていない**。公式カードは「MacOS 非対応」（PyTorch 版について）。Kyutai の変換スクリプトで自作変換する必要がある（構成は moshiko と同じなので可能な見込み）。
3. **§3「J-Moshi は研究用データ由来で利用条件に制約」** → 正確には**重みのライセンスが CC-BY-NC 4.0（非商用）**。MLX 変換版も同じ。
4. **§5.3「入力列の埋め込みはゼロ初期化。KAME が同種の構成」** → KAME の実装は **text_emb の重みをコピーして初期化**し、text 埋め込みに加算する。ゼロ初期化ではない（どちらが良いかは別問題）。
5. **duplex-handoff §「KAME 未読」「入力列の追加は PyTorch と MLX の両方に入れる」** → KAME は**学習コード・推論コード・重みが公開済み**（PyTorch のみ。MLX 版は無い）。推論サーバーは OpenAI API と Google STT 前提・英語設定。
6. **§1 / A2「記事型 = SALM-Duplex 系、テキスト出力型」** → SALM-Duplex は**音声コーデックトークンも LLM が出す**。テキスト出力 + 外部 TTS に近いのは Freeze-Omni / MinMo（いずれも別ヘッドで話す/止まるを判定）。
7. **A2「(c) 土台 LLM の内蔵エンコーダー（ストリーミング可否は未確認）」** → Gemma 4 E2B/E4B は約 300M の音声エンコーダーを内蔵。公式には**最長 30 秒の一括入力**の使い方のみ。設定値は右文脈 0 のチャンク注意で、逐次化できる可能性はあるが公式の記述・API は無い。
8. **§4「Moshi 7B 重み bf16 約 14 GB…」「MLX で動作確認」** → README の「MacBook Pro M3 で MLX 版をテスト」は確認。容量の数値は今回未確認。
9. **§3「Mimi: 1 フレーム 8 コードブック」** → Moshi が使うのは 8 本だが、Mimi 自体は最大 32 本（`mimi-encoder-mlx` は 16 本、Kyutai TTS は 32 本）。誤りではないが「Moshi では 8 本」と書くのが正確。
10. **duplex-handoff §「7B は 3060 では学習できず、毎回 Colab の大型 GPU」** → 公式 Colab は **A100・batch 1** で回る設定。H100 なら batch 16 で 39.6 GB。L4 / T4 で回るという記述は無い。

## 未確認のまま残ったもの

- Gemma 4 音声エンコーダーの逐次実行の可否（畳み込みの因果性、1 フレームの長さ、transformers / mlx-vlm での API）。mlx-vlm で Gemma 4 の音声入力が動くか。
- Gemma 4 技術報告（arXiv:2607.02770）は未読。
- OpenMDW-1.1（Nemotron 3.5 ASR）のライセンス本文。Qwen2.5-Omni の qwen-research ライセンス本文。ReazonSpeech コーパスの利用条件。
- Nemotron 3.5 ASR / kodama の日本語精度、MLX 変換版の中身。ReazonSpeech / parakeet-ja をストリーミングで使えるか（カードに記載なし）。
- rustymimi から連続特徴を取れるか（API 文書未読）。MLX 版 Mimi で `quantizer.encode` 直前の値を取り出すコードの実動作。
- `write(_:toBufferCallback:)` 実行中に delegate の `willSpeakRange…` が呼ばれるか。日本語音声で word マーカーがどの単位で出るか。Otoya / premium 日本語音声の名前と有無（この Mac には Kyoko のみ）。
- `akkikiki/j-moshi-ext-mlx-q4` が `moshi_mlx` 0.3.0 で実際に起動するか（ファイル名と設定の整合はコードで確認済み、未実行）。
- LLM-jp-Moshi-v1 の声（カードに記載なし）、IWSDS 論文本文、MLX 変換の実作業。
- KAME 論文の学習時間数・計算資源（論文に記載なし）、`SakanaAI/kame` HF 重みのライセンス（カードに記載なし。GitHub コードは MIT）、学習言語（英語と思われるが本文で明示を確認できず）。
- `moshi-finetune` の L4 / T4 / A100 40GB でのメモリ実測（記載なし）。`nu-dialogue/moshi-finetune` fork の差分。
- Moshi の対話モデルでテキスト列を上書きしたとき、その文面どおりに話すか（文書化されたものは見つからず。V0-2 で試せる）。
- Freeze-Omni の状態ラベルの作り方（論文に記載なし）、MinMo 本文の後半 27k 字。
