#!/usr/bin/env bash
set -u

TMUX_SESSIONS="${DFLASH_TMUX_SESSIONS:-dflash-runtime tomoko-runtime}"

echo "[stop] killing dflash serve processes..."

# dflash serve を含むものを拾う。
# ただし grep 自身は除外。
pids="$(ps aux | grep '[d]flash serve' | awk '{print $2}' || true)"

if [ -n "$pids" ]; then
  echo "$pids" | while read -r pid; do
    [ -z "$pid" ] && continue
    echo "kill dflash-related pid=${pid}"
    kill "$pid" 2>/dev/null || true
  done

  sleep 1

  # まだ残っているものを強制終了
  pids="$(ps aux | grep '[d]flash serve' | awk '{print $2}' || true)"
  if [ -n "$pids" ]; then
    echo "$pids" | while read -r pid; do
      [ -z "$pid" ] && continue
      echo "force kill dflash-related pid=${pid}"
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
else
  echo "no dflash serve process found"
fi

echo "[stop] killing tee log processes..."

tee_pids="$(ps aux | grep '[t]ee -a logs/dflash-' | awk '{print $2}' || true)"

if [ -n "$tee_pids" ]; then
  echo "$tee_pids" | while read -r pid; do
    [ -z "$pid" ] && continue
    echo "kill tee pid=${pid}"
    kill "$pid" 2>/dev/null || true
  done
else
  echo "no tee process found"
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
else
  echo "tmux is not installed"
fi

echo "[stop] closing legacy screen dflash sessions..."

if command -v screen >/dev/null; then
  screen -S dflash-gemma-31b -X quit 2>/dev/null || true
  screen -S dflash-gemma-26b -X quit 2>/dev/null || true
  screen -wipe >/dev/null 2>&1 || true
else
  echo "screen is not installed"
fi

echo
echo "[check] remaining dflash processes:"
ps aux | grep '[d]flash' || true

echo
echo "[check] tmux:"
if command -v tmux >/dev/null; then
  tmux list-sessions 2>/dev/null || true
else
  echo "tmux is not installed"
fi

echo
echo "[check] screen:"
if command -v screen >/dev/null; then
  screen -ls || true
else
  echo "screen is not installed"
fi

exit 0
