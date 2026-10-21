#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.."
mkdir -p logs

TMUX_SESSION_NAME="${DFLASH_TMUX_SESSION:-dflash-runtime}"
TMUX_EMBED="${DFLASH_TMUX_EMBED:-0}"
TMUX_MOUSE="${DFLASH_TMUX_MOUSE:-on}"

WINDOW_31B="${DFLASH_31B_WINDOW:-llm-31b}"
WINDOW_26B="${DFLASH_26B_WINDOW:-llm-26b}"

DFLASH_31B_MODEL="${DFLASH_31B_MODEL:-mlx-community/gemma-4-31b-it-4bit}"
DFLASH_31B_DRAFT="${DFLASH_31B_DRAFT:-z-lab/gemma-4-31B-it-DFlash}"
DFLASH_31B_PORT="${DFLASH_31B_PORT:-8081}"
DFLASH_31B_START_RETRIES="${DFLASH_31B_START_RETRIES:-2}"
DFLASH_26B_MODEL="${DFLASH_26B_MODEL:-v1/loras/lora/fused_model}"
DFLASH_26B_DRAFT="${DFLASH_26B_DRAFT:-z-lab/gemma-4-26B-A4B-it-DFlash}"
DFLASH_26B_PORT="${DFLASH_26B_PORT:-8082}"
DFLASH_26B_START_RETRIES="${DFLASH_26B_START_RETRIES:-2}"
DFLASH_HOST="${DFLASH_HOST:-0.0.0.0}"
DFLASH_CONNECT_HOST="${DFLASH_CONNECT_HOST:-127.0.0.1}"

command -v tmux >/dev/null || {
  echo "tmux is required"
  echo "install with: brew install tmux"
  exit 1
}

command -v curl >/dev/null || {
  echo "curl is required"
  exit 1
}

command -v dflash >/dev/null || {
  echo "dflash is required"
  exit 1
}

service_ready() {
  local port="$1"
  curl -fsS --max-time 2 "http://${DFLASH_CONNECT_HOST}:${port}/v1/models" >/dev/null 2>&1
}

window_exists() {
  local session="$1"
  local window="$2"
  tmux list-windows -t "${session}" -F '#W' 2>/dev/null | grep -qx "${window}"
}

window_has_child_process() {
  local session="$1"
  local window="$2"
  local pane_pid

  pane_pid="$(tmux list-panes -t "${session}:${window}" -F '#{pane_pid}' 2>/dev/null | head -n 1)"
  [ -n "${pane_pid}" ] || return 1
  pgrep -P "${pane_pid}" >/dev/null 2>&1
}

start_window() {
  local session="$1"
  local window="$2"
  local command="$3"
  local port="$4"

  if window_exists "${session}" "${window}"; then
    if service_ready "${port}"; then
      echo "already ready: ${session}:${window} http://${DFLASH_CONNECT_HOST}:${port}/v1/models"
      return 0
    fi
    if window_has_child_process "${session}" "${window}"; then
      echo "already starting: ${session}:${window}"
      return 0
    fi
    tmux respawn-window -k -t "${session}:${window}" "${command}"
    echo "respawned: ${session}:${window}"
    return 0
  fi

  tmux new-window -t "${session}:" -n "${window}" "${command}"
  echo "started: ${session}:${window}"
}

command_31b='cd "'"$(pwd)"'" && DFLASH_SERVER_NAME="dflash-gemma-31b" DFLASH_MODEL="'"${DFLASH_31B_MODEL}"'" DFLASH_DRAFT="'"${DFLASH_31B_DRAFT}"'" DFLASH_HOST="'"${DFLASH_HOST}"'" DFLASH_CONNECT_HOST="'"${DFLASH_CONNECT_HOST}"'" DFLASH_PORT="'"${DFLASH_31B_PORT}"'" DFLASH_START_RETRIES="'"${DFLASH_31B_START_RETRIES}"'" DFLASH_LOG_FILE="logs/dflash-31b.log" exec bash scripts/run_dflash_server.sh'
command_26b='cd "'"$(pwd)"'" && DFLASH_SERVER_NAME="dflash-gemma-26b" DFLASH_MODEL="'"${DFLASH_26B_MODEL}"'" DFLASH_DRAFT="'"${DFLASH_26B_DRAFT}"'" DFLASH_HOST="'"${DFLASH_HOST}"'" DFLASH_CONNECT_HOST="'"${DFLASH_CONNECT_HOST}"'" DFLASH_PORT="'"${DFLASH_26B_PORT}"'" DFLASH_START_RETRIES="'"${DFLASH_26B_START_RETRIES}"'" DFLASH_LOG_FILE="logs/dflash-26b.log" exec bash scripts/run_dflash_server.sh'

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

start_window "${TMUX_SESSION_NAME}" "${WINDOW_31B}" "${command_31b}" "${DFLASH_31B_PORT}"
start_window "${TMUX_SESSION_NAME}" "${WINDOW_26B}" "${command_26b}" "${DFLASH_26B_PORT}"

echo
tmux list-windows -t "${TMUX_SESSION_NAME}"

echo
echo "logs:"
echo "  tail -f logs/dflash-31b.log"
echo "  tail -f logs/dflash-26b.log"

echo
echo "attach:"
echo "  tmux attach -t ${TMUX_SESSION_NAME}"
