#!/usr/bin/env bash

cd "$(dirname "$0")/.."

mkdir -p logs

SESSION_31B="dflash-gemma-31b"
SESSION_26B="dflash-gemma-26b"

if screen -list | grep -q "\.${SESSION_31B}[[:space:]]"; then
  echo "already running: ${SESSION_31B}"
else
screen -dmS "${SESSION_31B}" bash -lc '
  echo "[start] dflash-gemma-31b at $(date)"
  dflash serve \
    --chat-template-args '\''{"enable_thinking": false}'\'' \
    --model mlx-community/gemma-4-31b-it-4bit \
    --draft z-lab/gemma-4-31B-it-DFlash \
    --port 8081 \
    2>&1 | tee -a logs/dflash-31b.log
'

  echo "started: ${SESSION_31B}"
fi

if screen -list | grep -q "\.${SESSION_26B}[[:space:]]"; then
  echo "already running: ${SESSION_26B}"
else
screen -dmS "${SESSION_26B}" bash -lc '
  echo "[start] dflash-gemma-26b at $(date)"
  dflash serve \
    --chat-template-args '\''{"enable_thinking": false}'\'' \
    --model loras/lora/fused_model \
    --draft z-lab/gemma-4-26B-A4B-it-DFlash \
    --port 8082 \
    2>&1 | tee -a logs/dflash-26b.log
'
  echo "started: ${SESSION_26B}"
fi

#  dflash serve \
#    --chat-template-args '\''{"enable_thinking": false}'\'' \
#    --model mlx-community/gemma-4-26b-a4b-it-4bit \
#    --draft z-lab/gemma-4-26B-A4B-it-DFlash \
#    --port 8082 \
#    2>&1 | tee -a logs/dflash-26b.log


echo
screen -ls

echo
echo "logs:"
echo "  tail -f logs/dflash-31b.log"
echo "  tail -f logs/dflash-26b.log"

echo
echo "attach:"
echo "  screen -r ${SESSION_31B}"
echo "  screen -r ${SESSION_26B}"

exit 0