# MEMORY.md — Tomoko v3

追記のみ。確定した判断・未解決の疑問・気づきを書く。

## 確定した判断

### [2026-11-10] 作業場所と規則（duplex-handoff.md の仮置きをそのまま採用）
- 作業は `v3/`。`v1/`・`v2/` は読むだけ。
- Python は uv で 3.12 に固定（システムは 3.14。MLX・moshi_mlx の対応を優先）。
- 測定の百分位は nearest-rank 法。失敗試行は `attempts`/`failures` として分母に残す（`tomoko_v3/measure.py`）。
- 出典: `duplex-handoff.md` §2・§3。作成者の仮置きであり、変えるときはユーザーに確認する。

## 未解決の疑問（人間への確認待ち）

（なし）

## 気づき

（なし）
