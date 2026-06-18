#!/usr/bin/env bash
set -u

TIMEOUT_SEC="${TOMOKO_RUNTIME_WAIT_TIMEOUT_SEC:-600}"
INTERVAL_SEC="${TOMOKO_RUNTIME_WAIT_INTERVAL_SEC:-2}"
LLM_READY_URLS="${TOMOKO_V2_LLM_READY_URLS:-http://127.0.0.1:8081/v1/models http://127.0.0.1:8082/v1/models}"
VOICEVOX_READY_URL="${TOMOKO_V2_VOICEVOX_READY_URL:-http://127.0.0.1:50122/version}"
STT_REQUIRED="${TOMOKO_V2_STT_REQUIRED:-0}"
OCR_REQUIRED="${TOMOKO_V2_OCR_REQUIRED:-0}"

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

if [ "$STT_REQUIRED" = "1" ]; then
  command -v swiftc >/dev/null || { echo "[missing] swiftc for Apple Speech STT sidecar"; failed=1; }
  [ -f scripts/apple_speech_stt/AppleSpeechSTT.swift ] || { echo "[missing] AppleSpeechSTT.swift"; failed=1; }
  [ -f scripts/apple_speech_stt/Info.plist ] || { echo "[missing] Apple Speech Info.plist"; failed=1; }
else
  echo "[info] STT optional; current availability:"
  command -v swiftc >/dev/null && echo "  swiftc: yes" || echo "  swiftc: no"
  [ -f scripts/apple_speech_stt/AppleSpeechSTT.swift ] && echo "  AppleSpeechSTT.swift: yes" || echo "  AppleSpeechSTT.swift: no"
fi

if [ "$OCR_REQUIRED" = "1" ]; then
  command -v screencapture >/dev/null || { echo "[missing] screencapture"; failed=1; }
  command -v swiftc >/dev/null || { echo "[missing] swiftc for Vision OCR sidecar"; failed=1; }
  [ -f scripts/vision_ocr/VisionOCR.swift ] || { echo "[missing] VisionOCR.swift"; failed=1; }
  command -v tesseract >/dev/null || { echo "[missing] tesseract"; failed=1; }
  command -v osascript >/dev/null || { echo "[missing] osascript"; failed=1; }
else
  echo "[info] OCR optional; current availability:"
  command -v screencapture >/dev/null && echo "  screencapture: yes" || echo "  screencapture: no"
  command -v swiftc >/dev/null && echo "  swiftc: yes" || echo "  swiftc: no"
  [ -f scripts/vision_ocr/VisionOCR.swift ] && echo "  VisionOCR.swift: yes" || echo "  VisionOCR.swift: no"
  command -v tesseract >/dev/null && echo "  tesseract: yes" || echo "  tesseract: no"
  command -v osascript >/dev/null && echo "  osascript: yes" || echo "  osascript: no"
fi

exit "$failed"
