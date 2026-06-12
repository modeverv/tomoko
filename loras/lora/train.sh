#!/bin/bash

# エラー時にスクリプトを終了する
set -e

# デフォルト設定
MODEL="mlx-community/Qwen2.5-7B-Instruct-4bit"
DATA_DIR="loras/lora/data"
ADAPTER_PATH="loras/lora/adapters"
ITERS=200
BATCH_SIZE=4
LORA_LAYERS=16
LR="1e-5"
SAVE_EVERY=50

# ヘルプメッセージの表示
show_help() {
  echo "Usage: ./train.sh [options]"
  echo ""
  echo "Options:"
  echo "  -m, --model <path>        Base MLX model path or Hugging Face repo ID (default: $MODEL)"
  echo "  -d, --data <path>         Directory containing train.jsonl and valid.jsonl (default: $DATA_DIR)"
  echo "  -a, --adapter <path>      Directory to save LoRA adapters (default: $ADAPTER_PATH)"
  echo "  -i, --iters <num>         Number of training iterations (default: $ITERS)"
  echo "  -b, --batch-size <num>    Batch size for training (default: $BATCH_SIZE)"
  echo "  -l, --layers <num>        Number of layers to fine-tune (default: $LORA_LAYERS)"
  echo "  --lr <rate>               Learning rate (default: $LR)"
  echo "  -h, --help                Show this help message"
}

# 引数のパース
while [[ "$#" -gt 0 ]]; do
  case $1 in
    -m|--model) MODEL="$2"; shift ;;
    -d|--data) DATA_DIR="$2"; shift ;;
    -a|--adapter) ADAPTER_PATH="$2"; shift ;;
    -i|--iters) ITERS="$2"; shift ;;
    -b|--batch-size) BATCH_SIZE="$2"; shift ;;
    -l|--layers) LORA_LAYERS="$2"; shift ;;
    --lr) LR="$2"; shift ;;
    -h|--help) show_help; exit 0 ;;
    *) echo "Unknown parameter passed: $1"; show_help; exit 1 ;;
  esac
  shift
done

echo "========================================="
echo " Starting MLX-LM LoRA Fine-Tuning"
echo "========================================="
echo "Base Model:   $MODEL"
echo "Data Dir:     $DATA_DIR"
echo "Adapter Path: $ADAPTER_PATH"
echo "Iterations:   $ITERS"
echo "Batch Size:   $BATCH_SIZE"
echo "Fine-tune Layers:  $LORA_LAYERS"
echo "Learning Rate: $LR"
echo "========================================="

# データセットの存在確認
if [ ! -f "$DATA_DIR/train.jsonl" ]; then
  echo "Error: $DATA_DIR/train.jsonl not found!"
  echo "Please generate the dataset first using generate_data.py"
  exit 1
fi

# mlx-lm がインストールされているか確認
if ! python3 -c "import mlx_lm" &> /dev/null; then
  echo "Error: mlx-lm is not installed in the current Python environment."
  echo "Please run: pip install mlx-lm"
  exit 1
fi

# LoRAの実行
# mlx_lm.lora は python -m mlx_lm.lora または mlx_lm.lora コマンドで実行可能
python3 -m mlx_lm lora \
  --model "$MODEL" \
  --data "$DATA_DIR" \
  --train \
  --iters "$ITERS" \
  --batch-size "$BATCH_SIZE" \
  --num-layers "$LORA_LAYERS" \
  --learning-rate "$LR" \
  --adapter-path "$ADAPTER_PATH" \
  --save-every "$SAVE_EVERY"

echo ""
echo "========================================="
echo " LoRA Fine-Tuning Completed!"
echo " Adapters saved to: $ADAPTER_PATH"
echo "========================================="
