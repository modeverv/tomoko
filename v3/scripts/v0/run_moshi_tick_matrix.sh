#!/usr/bin/env bash
# V0-2: J-Moshi の tick 時間を、量子化 × 負荷条件ごとに 5 分ずつ測る。
# 負荷は別プロセス（uvx の別環境）で掛け、測定の前後で止める。
# 使い方: bash scripts/v0/run_moshi_tick_matrix.sh "4 8" "alone e4b e4b+stt"
set -u
cd "$(dirname "$0")/../.."
QS="${1:-4 8}"
CONDS="${2:-alone e4b e4b+stt}"
SECONDS_RUN="${SECONDS_RUN:-300}"
LOGDIR=_docs/v0/data/load
mkdir -p "$LOGDIR"

start_load() {
  local kind="$1" tag="$2"
  case "$kind" in
    e4b) uvx --python 3.12 --from mlx-lm python scripts/v0/load_llm.py --model mlx-community/gemma-4-E4B-it-4bit \
           --seconds $((SECONDS_RUN + 120)) --log "$LOGDIR/$tag-e4b.jsonl" >/dev/null 2>&1 & ;;
    26b) uv run python scripts/v0/load_openai.py --port 8082 --model gemma-4-26b-a4b-it-mlx \
           --seconds $((SECONDS_RUN + 120)) --log "$LOGDIR/$tag-26b.jsonl" >/dev/null 2>&1 & ;;
    stt) uvx --python 3.12 --from mlx-whisper python scripts/v0/load_stt.py \
           --seconds $((SECONDS_RUN + 120)) --log "$LOGDIR/$tag-stt.jsonl" >/dev/null 2>&1 & ;;
  esac
  echo $!
}

for q in $QS; do
  for cond in $CONDS; do
    tag="q$q-$cond-$(date +%Y%m%dT%H%M%S)"
    pids=()
    [[ "$cond" == *e4b* ]] && pids+=("$(start_load e4b "$tag")")
    [[ "$cond" == *26b* ]] && pids+=("$(start_load 26b "$tag")")
    [[ "$cond" == *stt* ]] && pids+=("$(start_load stt "$tag")")
    [ ${#pids[@]} -gt 0 ] && sleep 45  # 負荷側のモデル読み込みを待つ
    echo "== $tag"
    uv run --group moshi python scripts/v0/moshi_tick_bench.py -q "$q" --mimi mlx --seconds "$SECONDS_RUN" \
      --condition "$cond" 2>&1 | grep -v -i warn | tail -7
    for p in "${pids[@]:-}"; do [ -n "$p" ] && pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done
    sleep 5
  done
done
