#!/usr/bin/env bash
set -u

TMUX_SESSIONS="${DFLASH_TMUX_SESSIONS:-dflash-runtime tomoko-v2-runtime tomoko-runtime}"

echo "[stop] killing dflash serve processes..."
pids="$(ps aux | grep '[d]flash serve' | awk '{print $2}' || true)"

if [ -n "$pids" ]; then
  echo "$pids" | while read -r pid; do
    [ -z "$pid" ] && continue
    echo "kill dflash pid=${pid}"
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  pids="$(ps aux | grep '[d]flash serve' | awk '{print $2}' || true)"
  if [ -n "$pids" ]; then
    echo "$pids" | while read -r pid; do
      [ -z "$pid" ] && continue
      echo "force kill dflash pid=${pid}"
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
else
  echo "no dflash serve process found"
fi

echo "[stop] closing tmux dflash windows..."
if command -v tmux >/dev/null; then
  for session in $TMUX_SESSIONS; do
    if tmux has-session -t "$session" 2>/dev/null; then
      tmux kill-window -t "${session}:llm-31b" 2>/dev/null || true
      tmux kill-window -t "${session}:llm-26b" 2>/dev/null || true
      if [ "$session" = "dflash-runtime" ]; then
        tmux kill-session -t "$session" 2>/dev/null || true
      fi
    fi
  done
fi

echo
echo "[check] remaining dflash processes:"
ps aux | grep '[d]flash' || true
