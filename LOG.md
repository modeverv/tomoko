# LOG.md

実装セッションの時系列ログ。セッションをまたいだ引き継ぎのために書く。

---

## テンプレート

```
## YYYY-MM-DD セッションN

### やったこと
-

### 詰まったこと・解決したこと
-

### 次のセッションでやること
-
```

---

## 初回（設計フェーズ）2026-05-23

### やったこと
- ARCHITECTURE.md / PLAN.md / AGENTS.md / README.md / LICENSE を作成
- マイルストーン M1〜M5 を設定
- 層間 DTO の型定義を設計
- TTS: M1フェーズは say、完了後に kokoro-mlx の方針を確定
- LLM: Ollama → MLX の移行方針を確定

### 確定した主な設計判断
- WebSocket は 1 本
- PostgreSQL が唯一の真実、pub/sub なし
- プールは utterance_candidates 一本、取り出しは SelectionStrategy
- arrival_candidates は使い捨て前提（3分ごとに作り直す）
- VAD ホットループはプリミティブのまま、境界でだけ DTO に包む
- git push origin は人間のみ許可

### 次のセッションでやること
- Phase 0: 環境構築から開始
- MEMORY.md に確定済み判断を整理してから実装開始
