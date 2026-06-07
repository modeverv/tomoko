#!/usr/bin/env bash
set -u

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

echo "[stop] closing screen sessions..."

screen -S dflash-gemma-31b -X quit 2>/dev/null || true
screen -S dflash-gemma-26b -X quit 2>/dev/null || true
screen -wipe >/dev/null 2>&1 || true

echo
echo "[check] remaining dflash processes:"
ps aux | grep '[d]flash' || true

echo
echo "[check] screen:"
screen -ls || true

exit 0