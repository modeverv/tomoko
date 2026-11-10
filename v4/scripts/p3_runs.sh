#!/bin/bash
# P3: 構成を 1 つ決めてエンジンを起こし直し、小の組を N 回流して採点する。
#   bash scripts/p3_runs.sh p3a-voicevox 3 TOMOKO_TTS=voicevox
# 3 つ目からは TOMOKO_<項目名>=値（engine/dto.py の EngineConfig）。トレースは work/traces/<名前>/ に分ける。
# LLM（8090）と VOICEVOX（50021）は起こしておく。
set -euo pipefail
NAME="$1"; N="$2"; shift 2
INPUTS="${INPUTS:-work/inputs/small}"
tmux kill-session -t v4-engine 2>/dev/null || true
for _ in $(seq 1 30); do  # 前のエンジンが口を離すまで待つ
  lsof -nP -iTCP:8700 -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 1
done
rm -rf "work/traces/$NAME"
tmux new -d -s v4-engine "env TOMOKO_TRACE_DIR=work/traces/$NAME $* uv run uvicorn engine.ws:app --host 127.0.0.1 --port 8700 2>&1 | tee work/engine.log"
for _ in $(seq 1 120); do
  curl -s -o /dev/null -m 1 http://127.0.0.1:8700/ && break
  sleep 1
done
curl -s -o /dev/null -m 1 http://127.0.0.1:8700/ || { echo "エンジンが起きない"; tail -5 work/engine.log; exit 1; }
for i in $(seq 1 "$N"); do
  out="work/runs/$NAME-$i"
  rm -rf "$out"
  uv run python -m bench.run ws://127.0.0.1:8700/ws "$INPUTS" "$out" --jobs 1
  uv run python -m bench.score "$out" --content --inputs "$INPUTS" 2>/dev/null
  uv run python -m bench.report "$out" --previous work/runs/baseline-1 > /dev/null
  echo "done $out"
done
count=$(ls "work/traces/$NAME" | wc -l | tr -d ' ')
for i in $(seq 1 "$N"); do
  uv run python scripts/latency_breakdown.py "work/runs/$NAME-$i" --traces "work/traces/$NAME" --last "$count" --index $((i-1))
done
