#!/bin/bash
# P2: 同じ構成で小の組を N 回流し、採点する。エンジン（8700）と LLM（8090）は起こしておく。
#   bash scripts/baseline_runs.sh baseline 3
set -euo pipefail
NAME="${1:-baseline}"
N="${2:-3}"
for i in $(seq 1 "$N"); do
  out="work/runs/$NAME-$i"
  rm -rf "$out"
  uv run python -m bench.run ws://127.0.0.1:8700/ws work/inputs/small "$out" --jobs 1
  uv run python -m bench.score "$out" --content --inputs work/inputs/small 2>/dev/null
  uv run python -m bench.report "$out" > /dev/null
  echo "done $out"
done
