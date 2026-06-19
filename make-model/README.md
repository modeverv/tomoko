# make-model

Gemma 4 26B MLX 4bit を教師にして、Tomoko v2 の semantic saturation
(`0.0..1.0`) を返す軽量 scorer を作るためのオフライン作業場。

runtime 本線には接続しない。ここで作る artifact は、十分に評価してから
`server/tomoko/semantic.py` の backend 候補として昇格する。

## 何を作るか

```text
conversation corpus
  -> partial prefix examples
  -> Gemma 4 26B teacher labels: SATURATION=0.0..1.0
  -> distilled hash-ridge saturation scorer
  -> JSON artifact
```

初期の学生モデルは hashed character n-gram + ridge regression。
生成モデルではなく、1発話 prefix から saturation を返す専用回帰モデルとして扱う。
JSON だけで保存できるので、推論・比較・差し替えが軽い。

## 入力コーパス形式

テキストファイル:

```text
今日の予定を教えて
ただ、やっぱり明日で
聞こえますか
```

JSONL:

```jsonl
{"text":"今日の予定を教えて","conversation_id":"c1","turn_index":1}
{"utterance":"ただ、やっぱり明日で","conversation_id":"c1","turn_index":2}
```

JSONL の発話本文キーは `text` / `utterance` / `transcript` / `content` を読む。

## Japanese Daily Dialogue を使う

Japanese Daily Dialogue は CC BY-NC-ND 4.0 / 非商用研究目的のデータセット。
raw data、変換済み corpus、teacher labels、model artifact は git に入れない。
この repo では `make-model/data/` と `make-model/artifacts/` を `.gitignore` している。

download と変換を一度に行う:

```bash
uv run python make-model/prepare_japanese_daily_dialogue.py \
  --min-chars 1 \
  --stride-chars 1
```

出力:

```text
make-model/data/external/japanese-daily-dialogue/      # cloned source, ignored
make-model/data/japanese-daily-dialogue/corpus.jsonl   # utterance rows, ignored
make-model/data/japanese-daily-dialogue/prefixes.jsonl # teacher input, ignored
make-model/data/japanese-daily-dialogue/manifest.json  # license/source manifest, ignored
```

すでに clone 済みの source だけを変換したい場合:

```bash
uv run python make-model/prepare_japanese_daily_dialogue.py \
  --source-dir make-model/data/external/japanese-daily-dialogue \
  --no-download
```

大きすぎる場合は prefix を粗くする:

```bash
uv run python make-model/prepare_japanese_daily_dialogue.py \
  --min-chars 2 \
  --stride-chars 2 \
  --max-prefixes-per-utterance 80
```

この後、Gemma 4 26B teacher label を作る:

```bash
uv run python make-model/generate_teacher_labels.py \
  --prefixes make-model/data/japanese-daily-dialogue/prefixes.jsonl \
  --out make-model/data/japanese-daily-dialogue/teacher-labels.jsonl \
  --url http://127.0.0.1:8082 \
  --model mlx-community/gemma-4-26b-a4b-it-4bit
```

### 1000件で teacher label 作成から評価まで通す

まず Gemma 4 26B endpoint が見えることを確認する。

```bash
curl http://127.0.0.1:8082/v1/models
```

JDD 全体からランダムに 1000件だけ teacher label を作る。
`--sample-seed` を固定すると、同じ 1000件を再現できる。

```bash
uv run python make-model/generate_teacher_labels.py \
  --prefixes make-model/data/japanese-daily-dialogue/prefixes.jsonl \
  --out make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl \
  --sample-size 1000 \
  --sample-seed 20260619 \
  --url http://127.0.0.1:8082 \
  --model mlx-community/gemma-4-26b-a4b-it-4bit
```

1000件ラベルで saturation scorer を train する。

```bash
uv run python make-model/train_saturation_model.py \
  --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl \
  --out make-model/artifacts/jdd-gemma26b-1000-saturation-model.json \
  --metrics-out make-model/artifacts/jdd-gemma26b-1000-train-metrics.json
```

同じ 1000件ラベルで teacher 再現性を評価する。

```bash
uv run python make-model/evaluate_saturation_model.py \
  --model make-model/artifacts/jdd-gemma26b-1000-saturation-model.json \
  --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl \
  --threshold 0.75
```

単発推論の確認:

```bash
uv run python make-model/predict_saturation.py \
  --model make-model/artifacts/jdd-gemma26b-1000-saturation-model.json \
  "今日の予定を教えて"
```

2026-06-19 の旧 `--limit 1000` 実行結果:

```text
teacher labels: 1000 rows, all label_source=teacher_llm
teacher label time: about 19 minutes
train/evaluate:
  binary_accuracy: 0.817
  mae: 0.13467446691436838
  rmse: 0.17769236473241526
predict "今日の予定を教えて":
  SATURATION=0.1294
```

注意: 旧 `--limit 1000` は先頭から取るため、この実行では 43 utterances 分に偏った。
これは pipeline smoke としては有効だが、本命評価ではない。
以後の 1000件評価は `--sample-size 1000 --sample-seed 20260619` で JDD 全体から取る。

## 1. prefix dataset を作る

ユーザーが言っていた「一文字ずつ入れる」は `--stride-chars 1` で行う。

```bash
uv run python make-model/build_prefix_dataset.py \
  --corpus path/to/conversation_corpus.txt \
  --out make-model/data/prefixes.jsonl \
  --min-chars 1 \
  --stride-chars 1
```

データが巨大すぎる場合は、まず粗めにする。

```bash
uv run python make-model/build_prefix_dataset.py \
  --corpus path/to/conversation_corpus.jsonl \
  --out make-model/data/prefixes.jsonl \
  --min-chars 2 \
  --stride-chars 2 \
  --max-prefixes-per-utterance 80
```

## 2. Gemma 4 26B teacher を起動する

既存 runtime の dflash 26B endpoint を使う場合:

```bash
make llm-run
```

独立した MLX server を使う場合の例:

```bash
uv run python -m mlx_lm.server \
  --model mlx-community/gemma-4-26b-a4b-it-4bit \
  --port 8084
```

モデル名や endpoint は手元の実体に合わせて指定する。
このディレクトリの既定 model は
`mlx-community/gemma-4-26b-a4b-it-4bit`、既定 URL は
`http://127.0.0.1:8082`。

## 3. 教師ラベルを作る

```bash
uv run python make-model/generate_teacher_labels.py \
  --prefixes make-model/data/prefixes.jsonl \
  --out make-model/data/teacher-labels.jsonl \
  --url http://127.0.0.1:8082 \
  --model mlx-community/gemma-4-26b-a4b-it-4bit
```

動作確認だけなら件数を絞る。

```bash
uv run python make-model/generate_teacher_labels.py \
  --prefixes make-model/data/prefixes.jsonl \
  --out make-model/data/teacher-labels-smoke.jsonl \
  --limit 20
```

評価用の subset を作る場合は、先頭 N 件ではなく seed 付きランダム抽出にする。

```bash
uv run python make-model/generate_teacher_labels.py \
  --prefixes make-model/data/prefixes.jsonl \
  --out make-model/data/teacher-labels-random-1000.jsonl \
  --sample-size 1000 \
  --sample-seed 20260619 \
  --url http://127.0.0.1:8082 \
  --model mlx-community/gemma-4-26b-a4b-it-4bit
```

教師には既存の `saturation_prompt()` と `parse_saturation_output()` を使う。
受け付ける出力は次の1行だけ。

```text
SATURATION=0.0..1.0
```

教師が壊れた行を返した場合、既定では deterministic fallback の値を記録し、
`label_source=deterministic_fallback` にする。純粋な教師データだけ欲しい場合は
`--no-fallback` を付ける。

pipeline の形だけ確認したい場合は `--deterministic-only` を使える。
これは smoke 用で、正式な蒸留データには使わない。

## 4. 蒸留モデルを学習する

```bash
uv run python make-model/train_saturation_model.py \
  --labels make-model/data/teacher-labels.jsonl \
  --out make-model/artifacts/saturation-model.json \
  --metrics-out make-model/artifacts/train-metrics.json
```

主な調整値:

- `--hash-size`: n-gram 特徴の次元数。既定 `2048`
- `--ngram-min`: 最小 n-gram。既定 `1`
- `--ngram-max`: 最大 n-gram。既定 `4`
- `--ridge-lambda`: ridge 正則化。既定 `1.0`

## 5. 評価する

```bash
uv run python make-model/evaluate_saturation_model.py \
  --model make-model/artifacts/saturation-model.json \
  --labels make-model/data/teacher-labels.jsonl \
  --threshold 0.75
```

出力:

```json
{
  "binary_accuracy": 0.93,
  "count": 1000.0,
  "mae": 0.04,
  "rmse": 0.08
}
```

## 6. 単発推論

```bash
uv run python make-model/predict_saturation.py \
  --model make-model/artifacts/saturation-model.json \
  "今日の予定を教えて"
```

出力:

```text
SATURATION=0.9123
```

## artifact の意味

`make-model/artifacts/saturation-model.json` は以下を含む。

- `model_type`: `hash_ridge_saturation`
- `config`: hash size / n-gram / regularization
- `weights` / `bias`: 推論に必要な重み
- `metadata`: 学習件数、教師モデル、train metrics

この artifact はまだ runtime 採用ではない。採用前に、既存 Gemma semantic lane と
shadow 比較して false start / missed early-start を見る。
