#!/bin/bash
# P3 の続き（2026-11-13 未明）。3060 が起きたので、測れなかった構成を測る。
#   bash scripts/p3_night_queue.sh
# 1. p3-5b: p3-4 に STT を 3060 に出すのを足す（20 件 × 3。p3-5 は 3060 が寝て無効になった）
# 2. p3-9-lan: LLM と STT は 3060、TTS は MBP の逐次合成、割り込みで止める（55 件 × 3）
# 転送（18090 / 18091 / 50122）は scripts/lan_upkeep.sh で張っている前提。
set -uo pipefail
R_LLM="TOMOKO_LLM_URL=http://127.0.0.1:18090/v1/chat/completions"
STEPS="TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3 TOMOKO_TTS_PRE_SILENCE_S=0.0"
STREAM="TOMOKO_VOICEVOX_URL=http://127.0.0.1:50122 TOMOKO_VOICEVOX_STREAMING=1"
INPUTS=work/inputs/latency bash scripts/p3_runs.sh p3-5b-stt3060 3 $STEPS "$R_LLM" TOMOKO_STT=whispercpp
INPUTS=work/inputs/small bash scripts/p3_runs.sh p3-9-lan 3 $STEPS $STREAM "$R_LLM" TOMOKO_STT=whispercpp TOMOKO_BARGE_IN=1
