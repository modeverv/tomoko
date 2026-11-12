#!/bin/bash
# P3: MaAI を 1 つずつ積んで測る。台本の第 2 版（work/inputs/small-v2、1 回合成）で、55 件 × 3 回ずつ。
#   bash scripts/p3_maai_queue.sh [最初の段] [最後の段]
# 段: 1 = MaAI なし（第 2 版の基準）、2 = 相づち、3 = + 言い終わりの判定、4 = + 相づちで止まらない
# 構成は p3-9-lan（LLM と STT は 3060、TTS は MBP の逐次合成、割り込みで止める）に積む。
# 転送（18090 / 18091 / 50122）は scripts/lan_upkeep.sh で張っている前提。
# 値は、2026-11-13 のオフラインの評価（_docs/data/maai-offline-*.json）と 3 件の試運転で決めた:
#   bc はユーザーの声を ch1 に（逆向きでは p_bc がほぼ 0）。p_bc の閾値 0.4（話している最中の 90% 点 0.36 の上）。
#   相づちはユーザーの無音 0.35 秒で（自然な息継ぎ 0.15〜0.34 秒では打たない）。
#   Mimi の vap は言い終わりと途中の無音を分けられなかったので、相づちの判断に vap を使わない（BC_MAX_SHIFT_P=1.0）。
#   bc_det の閾値 0.25（T4 の割り込みは最大 0.20、T3 の相づちの半分は 0.22 以上）。
set -uo pipefail
export INPUTS=work/inputs/small-v2
FIRST=${1:-1}
LAST=${2:-4}
R_LLM="TOMOKO_LLM_URL=http://127.0.0.1:18090/v1/chat/completions"
LAN="TOMOKO_TTS=voicevox TOMOKO_FIRST_CUT_MIN_CHARS=3 TOMOKO_TTS_PRE_SILENCE_S=0.0"
LAN="$LAN TOMOKO_VOICEVOX_URL=http://127.0.0.1:50122 TOMOKO_VOICEVOX_STREAMING=1"
LAN="$LAN TOMOKO_STT=whispercpp TOMOKO_BARGE_IN=1"
MAAI="TOMOKO_MAAI=1 TOMOKO_MAAI_USER_CHANNEL=${USER_CH:-0} TOMOKO_MAAI_THREADS=4"
BC="TOMOKO_BACKCHANNEL=1 TOMOKO_BC_THRESHOLD=${BC_T:-0.4} TOMOKO_BC_MAX_SHIFT_P=1.0 TOMOKO_BC_QUIET_S=0.35 TOMOKO_BC_ARM_S=0.5"
EP="TOMOKO_MAAI_ENDPOINT=1 TOMOKO_EP_SHIFT_P=${EP_SHIFT:-0.6} TOMOKO_EP_HOLD_P=${EP_HOLD:-0.35}"
GD="TOMOKO_BARGE_GUARD=1 TOMOKO_GUARD_BC_DET=${GUARD:-0.25}"
SKIP=${SKIP:-}  # 飛ばす段（例: SKIP=3）
step() { [ "$1" -ge "$FIRST" ] && [ "$1" -le "$LAST" ] && [[ " $SKIP " != *" $1 "* ]]; }
step 1 && bash scripts/p3_runs.sh p3-10-v2 3 $LAN "$R_LLM"
step 2 && bash scripts/p3_runs.sh p3-11-bc 3 $LAN "$R_LLM" $MAAI TOMOKO_MAAI_MODES=bc $BC
step 3 && bash scripts/p3_runs.sh p3-12-ep 3 $LAN "$R_LLM" $MAAI TOMOKO_MAAI_MODES=vap,bc $BC $EP
step 4 && bash scripts/p3_runs.sh p3-13-guard 3 $LAN "$R_LLM" $MAAI TOMOKO_MAAI_MODES=bc,bc_det $BC $GD
echo "queue finished"
