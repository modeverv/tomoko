#!/usr/bin/env bash
set -u

VOICEVOX_COMMAND="${VOICEVOX_COMMAND:-/Users/seijiro/Sync/sync_work/by-llms/async-voicevox/run_streaming_voicevox.command}"

if [ ! -x "${VOICEVOX_COMMAND}" ] && [ ! -f "${VOICEVOX_COMMAND}" ]; then
  echo "VOICEVOX command not found: ${VOICEVOX_COMMAND}"
  exit 1
fi

exec bash "${VOICEVOX_COMMAND}"
