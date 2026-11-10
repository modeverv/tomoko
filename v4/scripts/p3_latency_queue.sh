#!/bin/bash
# P3 の遅れだけの変更を、1 つずつ積んで測る。T1 と C2 の 20 件（work/inputs/latency）を各 3 回。
#   bash scripts/p3_latency_queue.sh
# 3060 の LLM（18090）と STT（18091）は、SSH の転送で手元に見えている前提。
set -uo pipefail
export INPUTS=work/inputs/latency
R_LLM="TOMOKO_LLM_URL=http://127.0.0.1:18090/v1/chat/completions"
bash scripts/p3_runs.sh p3-1-voicevox 3 TOMOKO_TTS=voicevox
bash scripts/p3_runs.sh p3-2-firstcut 3 TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3
bash scripts/p3_runs.sh p3-3-nosilence 3 TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3 TOMOKO_TTS_PRE_SILENCE_S=0.0
bash scripts/p3_runs.sh p3-4-llm3060 3 TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3 TOMOKO_TTS_PRE_SILENCE_S=0.0 "$R_LLM"
bash scripts/p3_runs.sh p3-5-stt3060 3 TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3 TOMOKO_TTS_PRE_SILENCE_S=0.0 "$R_LLM" TOMOKO_STT=whispercpp
