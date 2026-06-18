#!/bin/zsh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
LOCK_DIR="${TMPDIR:-/tmp}/tomoko-daily.lock"

mkdir -p "$LOG_DIR"
exec >> "$LOG_DIR/daily-launchagent.log" 2>&1

echo "=== tomoko daily launchagent start $(date '+%Y-%m-%d %H:%M:%S %z') ==="

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "tomoko daily already running; lock exists: $LOCK_DIR"
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/.mise/shims:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1

cd "$REPO_ROOT"

if ! command -v mise >/dev/null 2>&1; then
  echo "mise not found on PATH=$PATH"
  exit 127
fi

set +e
/usr/bin/make daily
status=$?
set -e

echo "=== tomoko daily launchagent end status=$status $(date '+%Y-%m-%d %H:%M:%S %z') ==="
exit "$status"
