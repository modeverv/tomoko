#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.."

mkdir -p logs

TMUX_SESSION_NAME="${DFLASH_TMUX_SESSION:-dflash-runtime}"
TMUX_EMBED="${DFLASH_TMUX_EMBED:-0}"
TMUX_MOUSE="${DFLASH_TMUX_MOUSE:-on}"

WINDOW_31B="llm-31b"
WINDOW_26B="llm-26b"

command -v tmux >/dev/null || {
  echo "tmux is required"
  echo "install with: brew install tmux"
  exit 1
}

window_exists() {
  local session="$1"
  local window="$2"
  tmux list-windows -t "${session}" -F '#W' 2>/dev/null | grep -qx "${window}"
}

start_window() {
  local session="$1"
  local window="$2"
  local command="$3"

  if window_exists "${session}" "${window}"; then
    echo "already running: ${session}:${window}"
    return 0
  fi

  tmux new-window -t "${session}:" -n "${window}" "${command}"
  echo "started: ${session}:${window}"
}

command_31b='cd "'"$(pwd)"'" && echo "[start] dflash-gemma-31b at $(date)" && dflash serve --chat-template-args '\''{"enable_thinking": false}'\'' --model mlx-community/gemma-4-31b-it-4bit --draft z-lab/gemma-4-31B-it-DFlash --port 8081 2>&1 | tee -a logs/dflash-31b.log'
command_26b='cd "'"$(pwd)"'" && echo "[start] dflash-gemma-26b at $(date)" && dflash serve --chat-template-args '\''{"enable_thinking": false}'\'' --model loras/lora/fused_model --draft z-lab/gemma-4-26B-A4B-it-DFlash --port 8082 2>&1 | tee -a logs/dflash-26b.log'

if tmux has-session -t "${TMUX_SESSION_NAME}" 2>/dev/null; then
  tmux set-option -t "${TMUX_SESSION_NAME}" mouse "${TMUX_MOUSE}" >/dev/null
else
  if [ "${TMUX_EMBED}" = "1" ]; then
    echo "tmux session not found: ${TMUX_SESSION_NAME}"
    exit 1
  fi
  tmux new-session -d -s "${TMUX_SESSION_NAME}" -n "${WINDOW_31B}" "${command_31b}"
  tmux set-option -t "${TMUX_SESSION_NAME}" mouse "${TMUX_MOUSE}" >/dev/null
  echo "started: ${TMUX_SESSION_NAME}:${WINDOW_31B}"
fi

start_window "${TMUX_SESSION_NAME}" "${WINDOW_31B}" "${command_31b}"
start_window "${TMUX_SESSION_NAME}" "${WINDOW_26B}" "${command_26b}"

#  dflash serve \
#    --chat-template-args '\''{"enable_thinking": false}'\'' \
#    --model mlx-community/gemma-4-26b-a4b-it-4bit \
#    --draft z-lab/gemma-4-26B-A4B-it-DFlash \
#    --port 8082 \
#    2>&1 | tee -a logs/dflash-26b.log


echo
tmux list-windows -t "${TMUX_SESSION_NAME}"

echo
echo "logs:"
echo "  tail -f logs/dflash-31b.log"
echo "  tail -f logs/dflash-26b.log"

echo
echo "attach:"
echo "  tmux attach -t ${TMUX_SESSION_NAME}"

exit 0
