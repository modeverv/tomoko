#!/bin/bash
# P3: MaAI のオフライン評価をまとめて流す。台本の版と声の作り方の違い、相づちのモデルの向き、相づちの検出を見る。
#   bash scripts/p3_maai_offline_queue.sh
# MaAI の入った環境（例: p3wt の .venv）で走らせる。遅れの測定と重ならないときに流す（CPU を使う）。
# 結果は _docs/data/maai-offline-<名前>.json。4 本ずつ並べて走らせる（各 torch 2 スレッド）。
set -uo pipefail
PY=${PY:-.venv/bin/python}
OUT=_docs/data
mkdir -p "$OUT" work/maai-offline
run() {
  name=$1
  shift
  "$PY" scripts/maai_offline.py "$@" --out "$OUT/maai-offline-$name.json" > "work/maai-offline/$name.log" 2>&1
  echo "$name exit=$?"
}
# 1. vap（CPC、10 Hz、文脈 5 秒。2026-11-10 の下調べと同じ設定）で、3 つの入力の組を比べる
run v1-parts-vap work/inputs/small --modes vap &
run v1-onepass-vap work/inputs/small-onepass --modes vap &
run v2-onepass-vap work/inputs/small-v2 --modes vap &
# 2. 相づちの検出（Mimi のみ）。T3 の相づちと T4 の割り込み
run v2-bcdet work/inputs/small-v2 --modes bc_det &
wait
# 3. 相づちの種類の予測（bc_2type、CPC のみ）の向きを確かめる。ユーザーの声を外の ch2 に入れる / ch1 に入れる
run v2-bc2type-userch2 work/inputs/small-v2 --modes bc_2type &
run v2-bc2type-userch1 work/inputs/small-v2 --modes bc_2type --bc-user-ch1 &
# 4. 実時間の構成（全部 Mimi、12.5 Hz、文脈 20 秒）の vap と bc。bc の日本語の重みは Mimi だけにある
run v2-mimi-vap-bc-userch2 work/inputs/small-v2 --modes vap,bc --encoder mimi &
run v2-mimi-vap-bc-userch1 work/inputs/small-v2 --modes vap,bc --encoder mimi --bc-user-ch1 &
wait
echo "all done"
