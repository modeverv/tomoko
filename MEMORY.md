# MEMORY.md

## 確定した判断

### v2 は v1 を継続実装せず root に作り直す
v1 の実装・テスト・ログ・知見は `v1/` を参照専用として保持し、v2 は root の
`server/` / `client/` / `tests/` / `scripts/` に新しい境界で実装する。

### PostgreSQL と id-only NOTIFY を source of truth にする
v2 の process 間連携は DB row を本体にし、`LISTEN/NOTIFY` payload は UUID 文字列だけにする。
payload に JSON や本文を載せる v1/実験的経路は v2 本線では採用しない。

### hot path は gate を持たない
`hot-path-process` は音声入出力、STT observation、LLM/TTS 実行だけを担当する。
話してよいか、どの prompt を実行するか、古い request を破棄するかは `tomoko-process`
の deterministic decision と prompt lifecycle が所有する。

### v2 初期は production scaffold を優先する
Apple Speech / VOICEVOX / Calendar / OCR / live conversation は外部実機依存を持つため、
まず interface、DB contract、unit-testable な deterministic model、smoke hook を作る。
実機 smoke の結果は `_docs/latency.md` と `LOG.md` に追記して昇格判断する。

### v1 から継承する判断
- VAD idle pre-roll は発話冒頭欠落対策として保持する。
- VAP は VAD 置換ではなく `p_yielding` 由来の silence ms side-channel として使う。
- VOICEVOX chunked は complete WAV chunk 境界を維持し、raw PCM は client に流さない。
- browser の audio device selector は client-only UI に閉じる。
- raw screenshot / camera frame は online prompt に直接入れない。
- prompt は安定 context を前半、current utterance を明示位置、volatile recall を後半に置く。

### v2 scaffold は Phase V2.0-V2.20 の境界を先に実装する
2026-06-18 の実装では、root v2 を production runtime へ直接つなぐ前に、全 Phase の boundary、
DTO、DB schema、deterministic model、process CLI、report hook、unit tests を先に揃えた。
外部 runtime を必要とする Apple Speech / VOICEVOX / LLM / OCR は interface と readiness hook に閉じ、
unit tests は fake backend または純関数で contract を固定する。

### v2 DB schema は additive な `100_v2_core.sql` に集約する
v2 用 table は既存 v1 schema を変更せず、`docker/postgres/init/100_v2_core.sql` に additive に作る。
NOTIFY は `v2_notify_id(channel_name, event_id)` を経由し、許可 channel と UUID payload だけを受け付ける。

### browser shell は `/client` static と `/ws` だけにする
root hot-path FastAPI は `/` で `client/index.html`、`/client/*` で静的ファイルを返し、runtime 通信は
`/ws` のみを使う。client は mic bytes 送信、audio stop command、JSON event 表示に留める。

## 未解決の疑問（人間への確認待ち）

### [2026-06-18] live acceptance の実機検証タイミング
V2.20 の 10 分 live conversation smoke は Apple Speech / VOICEVOX / LLM runtime / OCR の
実機状態に依存する。scaffold と readiness check は実装するが、実測は runtime 起動後に別途行う。

## 気づき

### root `MEMORY.md` は Phase V2.0 で作成された
作業開始時点では root `MEMORY.md` が無く、v1 の `MEMORY.md` と root `LOG.md` の前回記録を参照した。
