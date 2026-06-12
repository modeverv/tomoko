#!/usr/bin/env bash
set -u

TIMEOUT_SEC="${TOMOKO_RUNTIME_WAIT_TIMEOUT_SEC:-600}"
INTERVAL_SEC="${TOMOKO_RUNTIME_WAIT_INTERVAL_SEC:-2}"
LLM_READY_URLS="${TOMOKO_RUNTIME_LLM_READY_URLS:-http://127.0.0.1:8081/v1/models http://127.0.0.1:8082/v1/models}"
VOICEVOX_READY_URL="${TOMOKO_RUNTIME_VOICEVOX_READY_URL:-http://127.0.0.1:50122/version}"

command -v curl >/dev/null || {
  echo "curl is required"
  exit 1
}

is_ready() {
  local url="$1"
  curl -fsS --max-time 2 "$url" >/dev/null 2>&1
}

wait_url() {
  local name="$1"
  local url="$2"
  local deadline

  deadline=$((SECONDS + TIMEOUT_SEC))
  echo "[wait] ${name}: ${url}"
  while [ "$SECONDS" -lt "$deadline" ]; do
    if is_ready "$url"; then
      echo "[ready] ${name}: ${url}"
      return 0
    fi
    sleep "$INTERVAL_SEC"
  done

  echo "[timeout] ${name}: ${url}"
  return 1
}

failed=0
for url in $LLM_READY_URLS; do
  wait_url "llm" "$url" || failed=1
done

wait_url "voicevox" "$VOICEVOX_READY_URL" || failed=1

exit "$failed"
