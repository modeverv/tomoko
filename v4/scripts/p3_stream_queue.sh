#!/bin/bash
# P3 の続き。逐次合成の VOICEVOX（別の機械、SSH の転送で 50122 に見えている前提）を足して測る。
#   bash scripts/p3_stream_queue.sh
# 1. 遅れ: T1 と C2 の 20 件を 3 回（p3-3-nosilence と比べる）
# 2. 小の組 55 件を 3 回（割り込みで止める変更の、比べる相手）
# 3. 割り込みで止めるのを入れて、55 件を 3 回
set -uo pipefail
BASE="TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3 TOMOKO_TTS_PRE_SILENCE_S=0.0 TOMOKO_VOICEVOX_URL=http://127.0.0.1:50122 TOMOKO_VOICEVOX_STREAMING=1"
INPUTS=work/inputs/latency bash scripts/p3_runs.sh p3-6-stream 3 $BASE
INPUTS=work/inputs/small bash scripts/p3_runs.sh p3-7-full 3 $BASE
INPUTS=work/inputs/small bash scripts/p3_runs.sh p3-8-bargein 3 $BASE TOMOKO_BARGE_IN=1
