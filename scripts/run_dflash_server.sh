#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.."
mkdir -p logs

SERVER_NAME="${DFLASH_SERVER_NAME:-dflash}"
MODEL="${DFLASH_MODEL:?DFLASH_MODEL is required}"
DRAFT="${DFLASH_DRAFT:-}"
HOST="${DFLASH_HOST:-0.0.0.0}"
CONNECT_HOST="${DFLASH_CONNECT_HOST:-127.0.0.1}"
PORT="${DFLASH_PORT:?DFLASH_PORT is required}"
LOG_FILE="${DFLASH_LOG_FILE:-logs/${SERVER_NAME}.log}"
READY_URL="${DFLASH_READY_URL:-http://${CONNECT_HOST}:${PORT}/v1/models}"
START_RETRIES="${DFLASH_START_RETRIES:-2}"
RESTART_DELAY_SEC="${DFLASH_RESTART_DELAY_SEC:-5}"
READY_MAX_TIME_SEC="${DFLASH_READY_MAX_TIME_SEC:-2}"
KEEPALIVE_IF_READY="${DFLASH_KEEPALIVE_IF_READY:-1}"
DFLASH_BIN="${DFLASH_BIN:-dflash}"
CHAT_TEMPLATE_ARGS="${DFLASH_CHAT_TEMPLATE_ARGS:-{\"enable_thinking\": false}}"

command -v curl >/dev/null || {
  echo "curl is required"
  exit 1
}

is_ready() {
  curl -fsS --max-time "${READY_MAX_TIME_SEC}" "${READY_URL}" >/dev/null 2>&1
}

if is_ready; then
  echo "[ready] ${SERVER_NAME} already serving: ${READY_URL}"
  if [ "${KEEPALIVE_IF_READY}" != "1" ]; then
    exit 0
  fi
fi

command -v "${DFLASH_BIN}" >/dev/null || {
  echo "${DFLASH_BIN} is required"
  exit 1
}

monitor_ready_server() {
  if [ "${KEEPALIVE_IF_READY}" != "1" ]; then
    return 0
  fi
  while is_ready; do
    sleep 30
  done
  echo "[lost] ${SERVER_NAME} no longer ready: ${READY_URL}"
}

run_dflash_once() {
  local command=(
    "${DFLASH_BIN}"
    serve
    --chat-template-args "${CHAT_TEMPLATE_ARGS}"
    --model "${MODEL}"
    --host "${HOST}"
    --port "${PORT}"
  )
  if [ -n "${DRAFT}" ]; then
    command+=(--draft "${DRAFT}")
  fi

  echo "[start] ${SERVER_NAME} at $(date)"
  echo "[start] model=${MODEL} draft=${DRAFT:-none} host=${HOST} port=${PORT}"
  "${command[@]}" 2>&1 | tee -a "${LOG_FILE}"
  return "${PIPESTATUS[0]}"
}

attempt=1
while [ "${attempt}" -le "${START_RETRIES}" ]; do
  if is_ready; then
    echo "[ready] ${SERVER_NAME} already serving: ${READY_URL}"
    monitor_ready_server
    attempt=$((attempt + 1))
    continue
  fi

  run_dflash_once
  status=$?
  if is_ready; then
    echo "[ready] ${SERVER_NAME} serving after exit status ${status}: ${READY_URL}"
    monitor_ready_server
    attempt=$((attempt + 1))
    continue
  fi

  if [ "${attempt}" -ge "${START_RETRIES}" ]; then
    echo "[failed] ${SERVER_NAME} exited with status ${status} after ${attempt} attempt(s)"
    exit "${status}"
  fi

  echo "[retry] ${SERVER_NAME} exited with status ${status}; retrying in ${RESTART_DELAY_SEC}s"
  sleep "${RESTART_DELAY_SEC}"
  attempt=$((attempt + 1))
done

exit 1
