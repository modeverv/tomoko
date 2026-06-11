# System Prompt Baking via LoRA (mlx-lm)

このディレクトリは、指定したシステムプロンプト（人格、キャラクター、ルール等）の挙動をベース LLM に「焼き込む（定着させる）」ための LoRA（Low-Rank Adaptation）ファインチューニングプログラム一式を提供します。

システムプロンプトを事前にモデルに焼き込むことで、毎回の推論時に巨大なシステムプロンプトをコンテキストに入力する必要がなくなり、**プレフィルトークン数の削減によるレイテンシーの大幅な高速化（特にローカル推論環境において有効）**と、キャラクターの一貫性向上が見込めます。

## ディレクトリ構成

```text
lora/
├── README.md            # この説明書
├── requirements.txt     # 必要な依存パッケージ
├── generate_data.py     # 学習用データセット（JSONL）の自動生成スクリプト
├── train.sh             # mlx_lm.lora を実行する学習スクリプト
└── evaluate.py          # 学習後モデルの評価・推論確認スクリプト
```

## 事前準備

1. **依存パッケージのインストール**
   本プログラムは Apple Silicon Mac 環境（MLX）を推奨しています。
   ```bash
   pip install -r lora/requirements.txt
   ```
   すでに `tomoko` プロジェクトで `mlx` オプション付きでインストールしている場合は、追加のインストールは基本的に不要です。

2. **ベースモデルの準備**
   デフォルトでは `mlx-community/Qwen2.5-7B-Instruct-4bit` を使用します。必要に応じて他のモデルも利用可能です。

3. **システムプロンプトの準備**
   焼き込みたいシステムプロンプト（マークダウンやテキストファイル）を用意します。
   デフォルトでは、プロジェクト内の `prompts/base_persona.md` を参照します。

---

## 使い方手順

### Step 1: 学習データセットの自動生成 (`generate_data.py`)

指定したシステムプロンプトに従った対話データセットを自動生成（Self-Instruct）し、学習用データを作成します。

ローカルで **Ollama** が起動している状態で、以下のコマンドを実行します：
```bash
python lora/generate_data.py \
  --system-prompt-path prompts/base_persona.md \
  --output-dir lora/data \
  --num-samples 100 \
  --backend ollama \
  --model qwen2.5:7b
```

#### 主なオプション:
- `--system-prompt-path`: 焼き込みたいシステムプロンプトのファイルパス（デフォルト: `prompts/base_persona.md`）
- `--output-dir`: 生成データの保存先（デフォルト: `lora/data`）
- `--num-samples`: 生成する対話データサンプル数（デフォルト: 100）
- `--backend`: データ生成に使用する LLM バックエンド。`ollama` または `mlx`（デフォルト: `ollama`）
- `--model`: 使用するモデル（デフォルト: `qwen2.5:7b`）
- `--split-ratio`: データを train / valid に分ける割合（デフォルト: 0.9 = 90%が学習用、10%が検証用）

生成が完了すると、`lora/data/train.jsonl` および `lora/data/valid.jsonl` が作成されます。

---

### Step 2: LoRA 学習の実行 (`train.sh`)

生成されたデータセットを用いて、モデルの LoRA ファインチューニングを行います。

シェルスクリプトに実行権限を与えて実行します：
```bash
chmod +x lora/train.sh
./lora/train.sh --iters 200 --batch-size 4
```

#### 主なオプション:
- `-m, --model`: ベースとなる MLX モデルフォルダまたは Hugging Face レポ ID（デフォルト: `mlx-community/Qwen2.5-7B-Instruct-4bit`）
- `-d, --data`: `train.jsonl` と `valid.jsonl` があるディレクトリ（デフォルト: `lora/data`）
- `-a, --adapter`: LoRA アダプタ（重み）の保存先（デフォルト: `lora/adapters`）
- `-i, --iters`: 学習のイテレーション（ステップ）数。データセットが小さい（100件程度）場合は `200`〜`500` イテレーションで十分効果が出ます（デフォルト: 200）
- `-b, --batch-size`: バッチサイズ（デフォルト: 4）
- `-l, --layers`: LoRA を適用するレイヤー数（デフォルト: 16）
- `--lr`: 学習率（デフォルト: `1e-5`）

学習が正常に完了すると、`lora/adapters/` ディレクトリの中に `adapters.safetensors` や `adapter_config.json` などの LoRA アダプタファイルが出力されます。

---

### Step 3: 学習済み LoRA モデルの評価 (`evaluate.py`)

学習した LoRA アダプタをベースモデルに適用し、システムプロンプトの挙動が焼き込まれているかテストします。

#### 1. 一括テスト（バッチ評価）モード
あらかじめ用意されたテスト用発話（システムプロンプトなし）に対して、焼き込み後のモデルがどのように応答するかを一括で確認します。
```bash
python lora/evaluate.py \
  --model mlx-community/Qwen2.5-7B-Instruct-4bit \
  --adapter lora/adapters
```

#### 2. インタラクティブチャットモード
ターミナル上で、焼き込み後のモデルと直接会話を試すことができます。
```bash
python lora/evaluate.py \
  --model mlx-community/Qwen2.5-7B-Instruct-4bit \
  --adapter lora/adapters \
  --interactive
```

---

## 焼き込み（Baking）のヒントと注意点
- **システムプロンプトの削除**: 学習した LoRA アダプタを使用する際は、プロンプト構築時に元のシステムプロンプトを省略するか、極めて簡潔なメタ指示のみに縮小することができます。これにより毎回のコンテキスト読み込み時間（Time-To-First-Token）を大きく削減できます。
- **過学習（Overfitting）**: イテレーション数が多すぎると、モデルが生成データと全く同じ文言しか返さなくなる（過学習）可能性があります。その場合は、`--iters` を小さくするか、`--lr`（学習率）を下げてください。
- **データの質**: Self-Instruct で生成されるデータの質が重要です。生成した `train.jsonl` を学習前に一度手動で確認し、意図しない応答や破綻した日本語が含まれていないかチェックすることをお勧めします。
