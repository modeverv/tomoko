# Tomoko v3 — full-duplex 単一モデル化

聞きながら「いつ・何を話すか」を自分で決める 1 つのモデルへ Tomoko を作り替える世代。
設計の経緯は repo 直下の [`duplex.md`](../duplex.md)、作業の起点は [`duplex-handoff.md`](../duplex-handoff.md)。

- [PLAN.md](PLAN.md) — 段階とチェックボックス
- [LOG.md](LOG.md) — 作業の時系列（追記のみ）
- [MEMORY.md](MEMORY.md) — 確定した判断・未解決の疑問・気づき（追記のみ）
- `_docs/v0/` — V0（学習なしの測定）の生の数値（JSON）と所見（Markdown）
- `tomoko_v3/` — 共通部品（測定など）
- `scripts/v0/` — V0 の測定スクリプト

`v1/`・`v2/` は読むだけ。変更しない。

## 環境

```bash
cd v3
uv sync
uv run pytest -m unit -q
```

Python は `.python-version` で 3.12 に固定している（システムの python3 は 3.14）。
