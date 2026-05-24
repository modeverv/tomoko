# LOG.md

実装セッションの時系列ログ。セッションをまたいだ引き継ぎのために書く。

---

## 2026-05-24 セッション46

### やること（開始時に書く）
- M3 Phase 9.0: candidate schema / DTO / store を実装する
- `docker/postgres/init/006_candidates.sql`、`server/shared/candidate.py`、`PostgresCandidateStore` を追加する
- candidate DTO round-trip と active/fresh fetch の unit test、PostgreSQL store round-trip の integration test を追加する

### やったこと
- `docker/postgres/init/006_candidates.sql` を追加した
  - `utterance_candidates` / `arrival_candidates` と active / fresh fetch 用 index を追加した
  - `maturity` / `behavior` の CHECK 制約を追加した
  - `spoken_at` と `dismissed_at` が同時に立たない制約を追加した
- `server/shared/candidate.py` を追加した
  - `UtteranceCandidate` / `ArrivalCandidate` / `ArrivalContextSnapshot` DTO を追加した
  - `CandidateMaturity` / `ArrivalBehavior` の許可値を loader で検証するようにした
  - `CandidateStore` protocol、`InMemoryCandidateStore`、`PostgresCandidateStore` を追加した
- `tests/unit/test_phase90_candidates.py` を追加した
  - DTO round-trip
  - expired / spoken / dismissed 除外
  - priority 降順・created_at 昇順
  - fresh arrival fetch を固定した
- `tests/integration/test_phase90_candidates_db.py` を追加し、PostgreSQL DDL 適用後の store round-trip を確認した
- `PLAN.md` の Phase 9.0 チェックボックスと実装結果を更新した
- `MEMORY.md` / `_docs/latency.md` に Phase 9.0 の判断と検証結果を追記した

### 詰まったこと・解決したこと
- arrival candidate の device filter を JSONB だけで行うと store 契約が読みにくくなる
  → `device_id` は検索用の列としても持ち、同じ値を `ArrivalContextSnapshot` にも含める形にした
- Phase 9.0 で LLM evaluator や常駐 loop まで進めると責務が混ざる
  → schema / DTO / store のみで止め、Phase 9.1 以降へ送った

### 次のセッションでやること
- Phase 9.1 に進む場合は、`CandidateSeed` / `ThinkerSourceContext` と deterministic `time_based` source から実装する
- dedupe_key の保存先と一意性は Phase 9.1 で判断・実装する

### 検証
- `mise exec -- uv run ruff check server/shared/candidate.py tests/unit/test_phase90_candidates.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-24 セッション45

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、`PLAN.md` の M3 Phase 9 を実装しやすい粒度へ分解する
- Phase 9 を DB/DTO/store、deterministic source、LLM evaluator、arrival precompute、process loop に分ける
- LLM がテスト先行で迷わず進められる完了条件とテスト観点を追記する

### やったこと
- `PLAN.md` の Phase 9 を 9.0〜9.4 に分解した
  - 9.0: candidate schema / DTO / store
  - 9.1: deterministic source / selection
  - 9.2: LLM evaluator
  - 9.3: arrival precompute
  - 9.4: thinker process loop
- Phase 9 では online `/ws` 経路や `TomoroSession` に接続せず、background 側の候補プール構築だけを担当することを明記した
- 各小 Phase に完了条件、テスト観点、失敗時 fallback、Redis / pub-sub を導入しない境界を追記した

### 詰まったこと・解決したこと
- 元の Phase 9 は部品名は明確だったが、DB lifecycle / DTO / source / evaluator / loop が混ざっており実装判断が入りやすかった
  → DB 契約から順に積む小 Phase に分解し、LLM がテスト先行で進められる形にした

### 次のセッションでやること
- Phase 9.0 実装時は `006_candidates.sql`、`server/shared/candidate.py`、`PostgresCandidateStore`、unit/integration test から着手する

### 検証
- `git diff -- PLAN.md LOG.md`
- `git diff --check`
- `rg -n "Phase 9|Phase 9\\.0|Phase 9\\.1|Phase 9\\.2|Phase 9\\.3|Phase 9\\.4|Phase 9 全体" PLAN.md`

## 2026-05-24 セッション44

### やること（開始時に書く）
- M2 Phase 8.8: ContextSnapshotBuilder 初段の完了状態を確認する
- 実装済みであれば `PLAN.md` の未チェック項目を実態に合わせてチェックする
- ドキュメント更新のみとして、必要な軽量検証後にコミットする

### やったこと
- `MEMORY.md` / `LOG.md` / `PLAN.md` / `README.md` / `ARCHITECTURE.md` と `_reference/` の必読資料を確認した
- `LOG.md` セッション41、`PLAN.md` の実装結果、実装ファイル、unit/perf test から Phase 8.8 初段が実装済みであることを確認した
- `PLAN.md` の Phase 8.8 チェックボックスを完了済みに更新した

### 詰まったこと・解決したこと
- Phase 8.8 直下には「append-only 制約により上のチェックボックスは直接変更しない」と書かれていたが、現在の `AGENTS.md` では `PLAN.md` のチェックボックス状態変更は許可されている
  → 実装結果と後続 Phase 8.8.1 / 8.8.5 の進行に合わせて、チェック状態だけを更新した

### 次のセッションでやること
- 特になし

### 検証
- `rg -n "Phase 8\\.8|ContextSnapshotBuilder|ContextBuild|TomokoContextSnapshot|server/gateway/context.py|test_phase88" PLAN.md`
- `rg -n "class ContextSnapshotBuilder|TomokoContextSnapshot|ContextBuildPolicy|ContextBuildTrace|ContextDepth|context_snapshot" server tests/perf tests/unit`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション43

### やること（開始時に書く）
- M2 Phase 8.8.5: TomoroSession 状態管理の最小足場を実装する
- `SessionEvent` / `TomoroRuntimeState` / `StateEmission` / `SessionCommand` / `TransitionResult` の DTO と `post_event()` / `_reduce()` の入口を追加する
- 既存挙動を壊さず、playback telemetry などの状態変更入口を event-shaped runtime へ寄せる regression test を追加する
- `pytest -m unit` で確認する

### やったこと
- `server/shared/models.py` に `TomoroRuntimeState` / `SessionEvent` / `StateEmission` / `SessionCommand` / `TransitionResult` を追加した
- `TomoroSession.get_now_state()` と `post_event()` / `_reduce()` の最小入口を追加した
- playback telemetry を `post_event()` 経由に寄せ、`handle_playback_telemetry()` は薄い互換入口にした
- transcript finalized の reducer 入口を追加し、active playback 中の echo と hard interrupt を `TransitionResult` として観測できるようにした
- `AudioTurnController` に runtime snapshot 用の read-only property を追加した
- `tests/unit/test_phase885_session_runtime.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 既存の実会話処理を一気に command runner 化すると変更範囲が大きすぎる
  → Phase 8.8.5 では playback telemetry の実処理だけを `post_event()` 経由にし、transcript finalized は reducer で判断を観測できる最小足場に留めた
- `AudioTurnController` の内部状態を runtime snapshot で読む必要があった
  → 状態変更 API は増やさず、read-only property だけを追加した

### 次のセッションでやること
- M3 の自発発話や arrival で競合が増えたら、`post_event()` の先に event queue / drain loop と command runner を追加する
- transcript finalized の既存 async 処理を command 実行に移す場合は、DB write / LLM / TTS / WebSocket send を段階的に `SessionCommand` 化する

### 検証
- `mise exec -- uv run ruff check server/shared/models.py server/gateway/audio_turn.py server/session.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase885_session_runtime.py tests/unit/test_session_concurrency.py tests/unit/test_barge_in.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

## テンプレート

```
## YYYY-MM-DD セッションN

### やったこと
- `_docs/evaluation.md` を追加した
  - 会話体験スコアを responsiveness / attended_feeling / turn_taking / interruption / memory / persona / recovery に分解した
  - 人間評価 JSON と機械ログ JSONL の初期案を置いた
  - 相関だけでなく回帰・特徴量重要度・失敗事例分析で最適化する方針を書いた
- `MEMORY.md` に、会話体験品質は人間評価と機械メトリクスの対応で最適化する判断を追記した

### 詰まったこと・解決したこと
- コード実装ではなく将来の評価設計なので、unit test は追加しなかった
- Markdown 差分に trailing whitespace がないことを `git diff --check` で確認した

### 次のセッションでやること
- 評価実装へ進む場合は、`logs/evals/*.jsonl` の出力 DTO と `turn_id` / `session_id` の join 方針から着手する
```

---

## 2026-05-24 セッション42

### やること（開始時に書く）
- M2 Phase 8.8.1: ContextSnapshotBuilder 運用 hardening を実装する
- process-local TTL cache と cache hit / age / ttl trace を追加する
- 遅い optional source が timeout しても degraded snapshot を返す regression test を追加する
- unit / perf test で ContextSnapshotBuilder の予算運用を確認する

### やったこと
- `ContextSnapshotBuilder` に process-local TTL cache を追加した
  - `same_session_turns` / `recent_turns` / `session_summaries` / `memory_hits` / `lexicon_terms` / `persona_slice` を source 単位で cache する
  - `ContextBuildTrace.cache_entries` に hit / age_ms / ttl_ms を残す
- `ContextBuildPolicy.max_parallel_sources` を追加し、context source の同時実行数を policy で制限できるようにした
- builder log に `cache_hits` と `max_parallel_sources` を含めた
- cache hit、TTL expiry 後の DB fallback、cache miss + DB timeout の regression test を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- cache を authoritative state に広げると状態の正が分散する
  → cache 対象は読み取り専用の context source に限定し、active session / attention / playback は対象外にした
- timeout した source の task が後から prompt に混ざると危険
  → `asyncio.wait(timeout=...)` の pending task を cancel し、done だけを assemble する既存方針を regression test で固定した

### 次のセッションでやること
- 実 DB データ量が増えたら、`normal` / `deep` の DB + embedding 込み perf を追加する
- 応答速度に応じた `fast` / `normal` / `deep` の動的選択を試す場合は、まず `ContextBuildTrace` と turn latency を見て policy を決める

### 検証
- `mise exec -- uv run ruff check server/gateway/context.py server/shared/models.py tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_context_snapshot_latency.py`

## 人間
short/normal/deepとかは応答速度を元に動的に切り替えても良いかも

## 2026-05-24 セッション41

### やること（開始時に書く）
- M2 Phase 8.8: ContextSnapshotBuilder 初段を実装する
- `TomokoContextSnapshot` / `ContextBuildPolicy` / `ContextBuildTrace` DTO と builder を追加する
- 既存 recent turns / session summary / turn memory / persona subset を budget 内で集約し、`TomoroSession` の文脈取得を builder 経由へ寄せる
- unit / perf test を追加し、timeout は degraded context として扱うことを保証する

### やったこと
- `server/shared/models.py` に `ContextDepth` / `ContextBuildPolicy` / `ContextBuildTrace` / `TomokoContextSnapshot` を追加した
- `server/gateway/context.py` に `ContextSnapshotBuilder` を追加した
  - same session recent turns / recent turns / session summaries / turn memory / lexicon / persona slice を source として扱う
  - `max_build_ms` 超過時は pending source を skipped にして degraded snapshot を返す
- `TomoroSession` の reply context 読み込みを builder 経由にした
  - `ThinkingInput.context` / `long_term_memory` / `context_snapshot` を snapshot から作る
- `ThinkFastMode` / `ThinkDeepMode` が `context_snapshot` の lexicon / persona slice を prompt に反映できるようにした
- `server/edge/main.py` で `PostgresPersonaSnapshotStore` を `TomoroSession` に渡すようにした
- `tests/unit/test_phase88_context_snapshot.py` と `tests/perf/test_context_snapshot_latency.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- timeout で pending task を cancel した時、未await coroutine warning が出た
  → source awaitable を `_timed()` 内で遅延生成する factory 方式にして warning を消した
- cache は初段で実体まで入れると authoritative state の境界が曖昧になる
  → `ContextBuildTrace.cache_hits` の境界だけを用意し、実 cache は Phase 8.8.1 に送った

### 次のセッションでやること
- Phase 8.8.1 に進む場合は、process-local TTL cache の実装、normal/deep の実 DB perf、cache age / ttl trace を追加する

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_context_snapshot_latency.py`

## 2026-05-24 セッション40

### やること（開始時に書く）
- M2 Phase 8.7: 用語集ログと人格スナップショットを実装する
- `persona_lexicon_versions` / `persona_state_versions` の JSONB DDL とモデルクラスを追加する
- background worker と round-trip / jsonb query test を追加し、online 経路には乗せない

### やったこと
- `docker/postgres/init/005_persona_snapshots.sql` を追加した
  - `persona_lexicon_versions` / `persona_state_versions` を作成する
  - snapshot / diff JSONB に GIN index を追加する
- `server/shared/models.py` に persona snapshot / diff / prompt subset DTO を追加した
  - `PersonaLexiconSnapshot`
  - `PersonaStateSnapshot`
  - `PersonaVersionDiff`
  - `LexiconTerm`
  - `PersonaPromptSlice`
- `server/shared/persona.py` を追加した
  - 最新 snapshot の読み込み
  - completed session material の読み込み
  - 新 version の JSONB 保存
- `server/background/persona_updater.py` を追加した
  - completed session summary から lexicon / persona state の version を作る
  - LLM extractor は JSON を返し、保存前に DTO loader / validator を通す
- `background-process/update_persona_snapshots.py` を追加し、`Makefile` に `persona-updater` / `persona-updater-once` を追加した
- `tests/unit/test_phase87_persona_snapshots.py` と `tests/integration/test_phase87_persona_snapshots_db.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 生 JSONB をプログラム中で持ち回らないため、DB 境界で必ず `PersonaLexiconSnapshot` / `PersonaStateSnapshot` / `PersonaVersionDiff` に変換する形にした
- 応答生成へ使う場合も snapshot 全量を prompt に入れず、`select_terms_for_prompt()` / `to_prompt_slice()` の subset DTO だけを使う契約にした

### 次のセッションでやること
- Phase 8.8 に進む場合は、`ContextSnapshotBuilder` から latest lexicon / persona snapshot を読み、budget 内で subset DTO に落とす

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/005_persona_snapshots.sql`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase87_persona_snapshots_db.py`

## 2026-05-24 セッション39

### やること（開始時に書く）
- `_tools/summarize_pending_sessions.py` をルートの `background-process/` へ移動する
- Makefile に session summarizer を起動する entry を追加する
- background process として定期実行できる入口を用意する

### やったこと
- `background-process/` ディレクトリを追加し、`summarize_pending_sessions.py` を移動した
- `summarize_pending_sessions.py` に `--watch` と `--interval-sec` を追加した
  - `--watch` なしなら 1 batch だけ処理して終了する
  - `--watch` ありなら pending session を定期 polling する
- `Makefile` に `session-summarizer` と `session-summarizer-once` を追加した
  - `session-summarizer` は `--watch` 付きで起動する
  - `session-summarizer-once` は 1 batch だけ処理する
- `PLAN.md` に background process 入口の配置補正を追記した

### 詰まったこと・解決したこと
- 特になし

### 次のセッションでやること
- `make server` と `make session-summarizer` を別ターミナルで起動して、実会話後の pending session が処理されるか確認する

### 検証
- `mise exec -- uv run python background-process/summarize_pending_sessions.py --help`
- `make -n session-summarizer`
- `make -n session-summarizer-once`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション38

### やること（開始時に書く）
- M2 Phase 8.6: セッション要約索引を実装する
- `summary_status='pending'` の会話セッションを background worker が要約し、要約 embedding とともに `conversation_sessions` へ保存する
- online `TomoroSession` 経路では要約生成を呼ばないことをテストで保証する

### やったこと
- `server/background/session_summarizer.py` を追加した
  - pending session を処理し、要約と summary embedding を `conversation_sessions` に保存する
  - 失敗時は `summary_status='error'` と `summary_error` を残す
- `PostgresConversationSessionSummaryStore` を追加した
  - pending claim、session turn 読み出し、completed/error 更新、summary vector search を実装した
- `_tools/summarize_pending_sessions.py` を追加した
  - background worker 相当として pending session を処理できる
- `InferenceRouter` / `config/central_realtime.toml` に `session_summary` backend を追加した
- `TomoroSession` の deep memory 検索に completed session summary search を追加した
  - 要約生成系メソッドは online 経路で呼ばない
  - 既存 turn-level `conversation_embeddings` 検索は残した
- `conversation_sessions.summary_embedding` の HNSW index を DDL に追加し、ローカル PostgreSQL に適用した
- `tests/unit/test_phase86_session_summary.py` と `tests/integration/test_phase86_session_summary_db.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- integration test の cleanup で、`conversation_logs` が session を参照しているため session 行を先に消せなかった
  → cleanup で該当 `conversation_logs` を先に削除してから `conversation_sessions` を削除するようにした
- `SessionSummarizer` が要約生成後に model 名取得のため router を二度呼びそうになった
  → `_summarize()` が summary text と backend name を一緒に返す形にし、要約 LLM selection は一度だけにした

### 次のセッションでやること
- Phase 8.7 に進む場合は、`persona_lexicon_versions` / `persona_state_versions` の JSONB DDL と model round-trip test から始める

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/004_conversation_sessions.sql`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase86_session_summary_db.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

## 2026-05-24 セッション37

### やること（開始時に書く）
- M2 Phase 8.5: 会話セッション境界を実装する
- `conversation_sessions` と `conversation_logs.conversation_session_id` を追加する
- `TomoroSession` で active conversation session を開始・終了し、同一 session の文脈を優先して読む

### やったこと
- `docker/postgres/init/004_conversation_sessions.sql` を追加した
  - `conversation_sessions` を作成し、summary fields と `summary_embedding vector(384)` を同じ行に持たせた
  - `conversation_logs.conversation_session_id` と session / recorded_at index を追加した
- `PostgresConversationSessionStore` を追加した
  - session 開始と終了を DB に保存する
  - 終了時に `summary_status='pending'` へ進める
- `TomoroSession` に `active_conversation_session_id` を追加した
  - 最初の参加発話で session を開始する
  - follow-up では同じ active session を再利用する
  - `cooldown -> ambient` と `withdrawn` で session を閉じる
- user / tomoko turn 保存時に active session ID を紐づけるようにした
- 短期文脈読み出しを、同一 session 優先 + 不足分だけ recent completed turn で補完する形にした
- `tests/unit/test_phase85_conversation_sessions.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 既存 unit test の in-memory `ConversationLogWriter` は `conversation_session_id` 引数を持たないものが多い
  → `TomoroSession` 側で writer の signature を見て、対応している writer にだけ keyword を渡す互換レイヤにした
- `conversation_sessions` の DDL は既存 `conversation_logs` 作成後に外部キーを張る必要がある
  → `004_conversation_sessions.sql` として追加し、ローカル PostgreSQL に適用して確認した

### 次のセッションでやること
- Phase 8.6 に進む場合は、online 経路では要約を呼ばず、`summary_status='pending'` の session を background worker が拾うテストから始める

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/004_conversation_sessions.sql`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

## 2026-05-24 セッション36

### やること（開始時に書く）
- 将来の最適化フェーズで使う会話体験評価設計を `_docs/evaluation.md` として追加する
- 人間評価と機械メトリクスを結び、設定調整を定量化できる形にする

### やったこと
-

### 詰まったこと・解決したこと
-

### 次のセッションでやること
-

## 2026-05-24 セッション35

### やること（開始時に書く）
- `Makefile` に `db-stop` / `db-down` / `db-dump` を追加する
- `db-stop` / `db-up` / `db-down` / `db-dump` を実際に動作確認する

### やったこと
- `Makefile` に `db-stop` を追加した
  - `$(COMPOSE) stop postgres`
  - コンテナ停止のみ。volume は残す
- `Makefile` に `db-down` を追加した
  - `$(COMPOSE) down`
  - コンテナと network を削除する。volume は削除しない
- `Makefile` に `db-dump` を追加した
  - `docker exec tomoko-postgres pg_dump -U tomoko -d tomoko`
  - `logs/db-dumps/tomoko-YYYYMMDD-HHMMSS.sql` に保存する

### 詰まったこと・解決したこと
- dump は git 管理対象にしたくない
  → 既存で ignore 済みの `logs/` 配下に保存するようにした
- `db-down` 後に DB が戻るか不安が残る
  → `db-down` 後に `db-up` し、既存テーブルが見えることを確認した

### 検証
- `make db-dump`
- `make db-stop`
- `make db-up`
- `make db-down`
- `make db-up`
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -c "\\dt"`

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション34

### やること（開始時に書く）
- ルートの `docker-compose.yml` を `docker/` 配下へ移動できるか確認する
- 移動する場合は `Makefile` の `db-up` が壊れないよう compose file の指定を更新する

### やったこと
- `docker-compose.yml` を `docker/docker-compose.yml` に移動した
- `Makefile` に `COMPOSE = docker compose --project-directory . -f docker/docker-compose.yml` を追加した
- `db-up` は `$(COMPOSE) up -d postgres` を使うようにした
- `ARCHITECTURE.md` の構成図を `docker/docker-compose.yml` 前提に更新した

### 詰まったこと・解決したこと
- compose file を単純に `docker/` 配下へ移すと相対パスや compose project 名が変わる可能性がある
  → `--project-directory .` を付け、build context / bind mount / volume project 名をルート基準のまま維持した

### 検証
- `docker compose --project-directory . -f docker/docker-compose.yml config`
- `make db-up`
- `git diff --check`

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション33

### やること（開始時に書く）
- Markdown 編集禁止ルールの一時解除を受け、`README.md` を現状のセットアップ手順に合わせて更新する
- `mise` 前提と `make deps` の導線を明記する

### やったこと
- `README.md` の必要なものに `mise` と Docker / Docker Compose を明記した
- Python / uv は `mise.toml` と `make deps` で揃える方針を書いた
- 現行 default の LM Studio / MLX Whisper / Kokoro MLX / embedding 構成を追記した
- 初回モデルダウンロードと warm-up に時間がかかること、LM Studio を使う場合の準備を追記した
- セットアップ手順を `make deps` / `make db-up` / `make server` に整理した

### 詰まったこと・解決したこと
- README の古い手順は Ollama / irodori 前提が残っていた
  → 現在の `config/central_realtime.toml` に合わせて、LM Studio + MLX/Kokoro 構成へ更新した

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション32

### やること（開始時に書く）
- `Makefile` に依存関係を解決する入口を追加する
- 既存の `mise exec -- uv ...` 運用に合わせる

### やったこと
- `Makefile` に `deps` target を追加した
- `deps` は `mise exec -- uv sync` を実行し、既存の `mise` / `uv` 運用に揃えた
- `README.md` のセットアップ手順を `uv sync` から `make deps` に更新した

### 詰まったこと・解決したこと
- 特になし

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション31

### やること（開始時に書く）
- LLM オーケストラツールが投入する `task.md` / `task.acceptance.md` / `summary.md` を git 管理対象から外す
- `.gitignore` に追加し、今後追跡されないようにする

### やったこと
- `.gitignore` に `task.md` / `task.acceptance.md` / `summary.md` を追加した
- `git rm --cached task.md task.acceptance.md summary.md` を実行し、ローカルファイルを残したまま git 管理対象から外した

### 詰まったこと・解決したこと
- 3ファイルはいずれも tracked だった
  → index からだけ削除し、以後は ignored file として扱うようにした

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション30

### やること（開始時に書く）
- 開発用ディレクトリである `tools/` を `_tools/` にリネームする
- 開発時の計測結果ディレクトリである `docs/` を `_docs/` にリネームする
- 過去実装参照ディレクトリである `reference/` を `_reference/` にリネームする
- コード・設定・ドキュメント内の参照を更新して、既存機能を壊さない

### やったこと
- `tools/` を `_tools/` にリネームした
- `docs/` を `_docs/` にリネームした
- `reference/` を `_reference/` にリネームした
- `AGENTS.md` / `README.md` / `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` / `LOG.md` / `_docs/latency.md` / `pyproject.toml` の参照を新ディレクトリ名へ更新した
- `_tools` 配下の Python import と unit test の import を `tools.*` から `_tools.*` に更新した
- `pyproject.toml` の ruff exclude を `_reference` に更新した

### 詰まったこと・解決したこと
- `_tools` import の軽い確認コマンドで戻り値型を勘違いしたワンライナーを実行して失敗した
  → 正しいアクセスに直して import 確認し、unit test でも `_tools` import が通ることを確認した

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション29

### やること（開始時に書く）
- ルートの `asset-factory_for_work` を `_tools/` 配下へ移動する
- 既存の参照パスを更新して、機能を維持する
- ruff / unit test で壊れていないことを確認する

### やったこと
- `asset-factory_for_work/asuka.wav` を `_tools/asset-factory_for_work/asuka.wav` へ移動した
- `asset-factory_for_work/generate_tomoko_assets.py` を `_tools/asset-factory_for_work/generate_tomoko_assets.py` へ移動した
- 移動後も `assets/images/` に出力できるよう、スクリプトの repository root 解決を `parents[2]` に更新した
- `_docs/latency.md` の `asuka.wav` 参照を新しい `_tools/asset-factory_for_work/asuka.wav` に更新した

### 詰まったこと・解決したこと
- 移動前の `ROOT = Path(__file__).resolve().parents[1]` は `_tools/` 配下へ移すと `_tools/` を root と誤認する
  → `parents[2]` に変えて、引き続きリポジトリルート配下の `assets/images/` へ生成するようにした

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション28

### やること（開始時に書く）
- TTS ベンチ出力スクリプトの出力先を git 管理外の `logs/` 配下へ変更する
- `artifacts/` ディレクトリを削除する
- `artifacts/` 配下の WAV を git 管理対象から外す

### やったこと
- `_tools/bench_tts_backends.py` の default 出力先を `artifacts/tts-bench` から `logs/tts-bench` に変更した
- `.gitignore` に `artifacts/` を追加し、再作成されても git 管理対象にならないようにした
- `git rm -r artifacts` で tracked WAV 8 ファイルを削除し、git 管理対象から外した
- `_docs/latency.md` の TTS ベンチ WAV 保存先を `logs/tts-bench/` に更新した
- `PLAN.md` / `MEMORY.md` に、過去の `artifacts/` 保存運用を否定して `logs/` 配下へ補正する判断を追記した

### 詰まったこと・解決したこと
- `artifacts/` は実体としても git index としても不要な生成物だった
  → 出力先を `logs/` に変更し、既存 tracked WAV は削除した

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション27

### やること（開始時に書く）
- ルートディレクトリにある `test_ollama*.py` を `tests/` 配下へ移動する
- pytest / ruff の対象として問題なく扱われることを確認する
- ルートディレクトリの見通しを良くする

### やったこと
- ルート直下の `test_ollama*.py` 7 ファイルを `tests/manual/ollama/` に移動した
- これらは pytest 形式の unit test ではなくトップレベル実行される手動確認スクリプトだったため、`pyproject.toml` の pytest 設定で `tests/manual` を collection 対象から外した

### 詰まったこと・解決したこと
- `test_ollama*.py` を `tests/` 直下へ置くと pytest collection 時に実行される可能性があった
  → `tests/manual/ollama/` に置き、通常の `pytest -m unit` では拾わない形にした

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション26

### やること（開始時に書く）
- 外部LLMから追加指摘された TomoroSession state/control 設計を評価する
- 妥当な内容を `PLAN.md` / `ARCHITECTURE.md` / `MEMORY.md` / `AGENTS.md` に反映する
- `_reference/2026-05-24-1200_設計評価と改善提案.md` は原文として参照のみ行い、変更しない

### やったこと
- 指摘は妥当と判断した
  - M2 では本格的な event-driven architecture ではなく、`post_event()` / reducer / command 境界の最小足場に留める
  - M3 で自発発話や arrival による競合が増えた段階で、event queue / drain loop / 個別 event dataclass を強化する
- `PLAN.md` に Phase 8.8.5 `TomoroSession 状態管理の最小足場` を追加した
- `PLAN.md` に Phase 10.5 `TomoroSession runtime hardening` を追加した
- `ARCHITECTURE.md` に `TomoroSession` の stateful control core / one-way control flow / reducer / command / stale result 方針を追記した
- `MEMORY.md` に state と制御判断の集約、event-shaped runtime、stale result 破棄の判断を追記した
- `AGENTS.md` に今後の作業規約として `TomoroSession` state/control 境界を追記した

### 詰まったこと・解決したこと
- `git diff --check` はユーザー更新済みの `_reference/2026-05-24-1200_設計評価と改善提案.md` に含まれる trailing whitespace で失敗した
  → 原文ファイルは変更せず、こちらが編集した Markdown 5 ファイルだけに絞って `git diff --check -- AGENTS.md ARCHITECTURE.md LOG.md MEMORY.md PLAN.md` を実行し、問題なしを確認した

### 次のセッションでやること
- 実装へ進む場合は Phase 8.8 の `ContextSnapshotBuilder` DTO / unit test か、Phase 8.8.5 の `SessionEvent` / `TransitionResult` DTO から着手する

## 2026-05-24 セッション20

### やること（開始時に書く）
- M2 Phase 8: 長期記憶（エピソード記憶）を実装する
- `conversation_logs` からローカル embedding を生成して pgvector に保存する
- `ThinkDeepMode` で類似検索した過去会話をプロンプトに差し込む
- 短い発話は fast、深い話題は deep に振り分ける最小モード選択を入れる

### 作業メモ
- 既存の Phase 7 方針を維持し、`conversation_logs` は role 行形式のまま長期記憶の原本として扱う
- WebSocket エンドポイントは増やさず、クライアント側判断も追加しない
- embedding 生成と記憶検索は DTO / DB 境界を守り、`server/gateway/thinking/*.py` は FastAPI に依存させない

### やったこと
- `MemoryHit` と `ThinkingInput.long_term_memory` を追加した
- `server/shared/inference/embedding/` に `intfloat/multilingual-e5-small` 用 embedding backend を追加した
- `conversation_embeddings` テーブルと pgvector HNSW index を追加した
- `PostgresConversationMemoryStore` を追加し、backfill / embedding 保存 / 類似検索を実装した
- `ThinkDeepMode` を追加し、top-K の過去会話を system prompt に差し込むようにした
- `should_use_deep_memory()` で短い発話は fast、記憶 cue や長めの相談文は deep に振り分けるようにした
- `TomoroSession` に deep mode / embedding backend / memory store を接続した
- 現在の user transcript 自身が deep memory 検索に混ざった場合は除外するようにした
- 起動時 warm-up に embedding backend を追加した
- `_tools/embed_conversation_logs.py` を追加し、既存 `conversation_logs` の embedding backfill を可能にした
- ローカル PostgreSQL に `conversation_embeddings` を適用し、既存 turn 3件を backfill した
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` / `ARCHITECTURE.md` に Phase 8 の結果を追記した

### 詰まったこと・解決したこと
- `_tools/embed_conversation_logs.py` を直接実行すると `server` package が import できなかった
  → repository root を `sys.path` に入れ、script 単体でも動くようにした
- `sentence-transformers` 追加後、初回 lock / install で `scikit-learn` などが入った
  → `uv.lock` を更新し、unit test で依存指定の PEP 508 妥当性を確認した
- 現在発話の embedding が非同期保存された場合、検索結果に自分自身が入る可能性がある
  → deep memory hits から同一 user transcript を除外する回帰テストを追加した

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/003_conversation_embeddings.sql`
- `mise exec -- uv run python _tools/embed_conversation_logs.py --limit 3`
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -c "SELECT count(*) FROM conversation_embeddings;"`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

### 次のセッションでやること
- Chrome 実セッションで「この前話してた〇〇覚えてる？」が deep memory を引くか確認する
- 実会話ログが増えたら `_tools/embed_conversation_logs.py --limit 100` で backfill する
- 必要なら deep/fast selector の cue と長さ閾値を実ログに合わせて調整する

## 2026-05-24 セッション18

### やること（開始時に書く）
- M2 Phase 7: 短期記憶を進められるところまで実装する
- `conversation_logs` に会話ターンを保存し、`ThinkFastMode` の入力へ直近会話ターンを差し込む
- 疑問点は人間の希望に従い LOG.md に記録しつつ、破壊的でない範囲は仮実装で進める

### 作業メモ・疑問点（仮実装で進める）
- Phase 7 の会話ログ schema は PLAN では `(user_text, tomoko_text, timestamp, emotion)` だけ指定されている。実装では将来の device/speaker/attention 分析に備えて `device_id` / `speaker` / `participation_mode` / `attention_mode` / `created_at` も持たせる。ただし短期文脈ではまず `user_text` / `tomoko_text` / `emotion` / `created_at` だけ使う。
- `TomoroSession` は reply/TTS を background task 化済みなので、会話ログ保存タイミングは `reply_done` 後にする。hard interrupt / cancel された未完了返答は短期記憶に保存しない仮方針で進める。

### 作業メモの訂正
- 上の schema メモにある `attention_mode` / `created_at` は今回の `conversation_logs` には追加しない。既存 schema は `recorded_at` / `device_id` / `speaker` / `role` / `transcript` / `emotion` / `participation_mode` で足りるため、Phase 7 ではテーブル変更なしで進めた。

### やったこと
- `ConversationLogWriter.read_recent_turns()` を追加し、PostgreSQL から直近 `conversation_logs` を `ConversationTurn` として読めるようにした
- `ThinkFastMode` が `ThinkingInput.context` を OpenAI 互換 messages の `user` / `assistant` role として current user message の前に差し込むようにした
- `TomoroSession` が reply 生成時に直近 12 turn を読み込むようにした
- user turn は reply task 起動前に保存済みなので、現在の transcript と同じ末尾 user turn は context から除外して重複を避けた
- `tests/unit/test_phase4_thinking.py` に短期文脈差し込みの unit test を追加した
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` に Phase 7 の結果を追記した

### 詰まったこと・解決したこと
- Phase 7 の PLAN は「会話ターンごとに `(user_text, tomoko_text, timestamp, emotion)`」と書いているが、既存実装は `role=user` / `role=tomoko` の行として保存していた
  → テーブルを作り直さず、role 行を `ConversationTurn` に戻して短期文脈として使う方針にした
- current user turn を先に DB へ保存するため、そのまま読むと current user message が context と current input の両方に入る
  → `TomoroSession._load_recent_context()` で末尾の同一 user turn を落とすようにした

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

### 次のセッションでやること
- Chrome 実セッションで「さっき言った〇〇のことだけど」が文脈付きで返るか確認する
- M2 Phase 8: embedding / pgvector / `ThinkDeepMode` の設計に進む

## 2026-05-24 セッション19

### やること（開始時に書く）
- 人間判断を反映する
  - `conversation_logs` は role 形式のままで進める
  - `conversation_logs.status TEXT NOT NULL DEFAULT 'completed'` を追加する
  - 止められた Tomoko 返答は `status='interrupted'` で保存する

### やったこと
- `conversation_logs` に `status TEXT NOT NULL DEFAULT 'completed'` を追加する DDL を入れた
- 既存ローカル PostgreSQL に `ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';` を適用した
- `ConversationLogStatus = completed | interrupted | cancelled | error` を DTO 側に追加した
- 通常完了した Tomoko 返答は `status='completed'`、hard interrupt で止められた生成済み途中返答は `status='interrupted'` で保存するようにした
- 短期記憶の `read_recent_turns()` は `status='completed'` だけを context に使うようにした
- hard interrupt 中の TTS cancel で interrupted turn が保存される unit test を追加した

### 詰まったこと・解決したこと
- `reply_done` 前に cancel されると、`ReplyPipeline` 内の途中テキストがローカル変数のまま消える
  → `CancelledError` を受けた `_reply_to()` で `reply.reply_text` を `interrupted` として保存するようにした
- `_start_reply_task()` でも既存 reply task を cancel するため、全部を `interrupted` にすると「止められた」以外も混ざる
  → `_cancel_reply_generation(status=...)` で理由を渡し、hard interrupt だけ `interrupted` にした

### 検証
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -c "\\d conversation_logs"` で `status` カラム確認
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

### 次のセッションでやること
- Chrome 実セッションで hard interrupt した時に `conversation_logs.status='interrupted'` が残ることを確認する
- M2 Phase 8: embedding / pgvector / `ThinkDeepMode` の設計に進む

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

## 2026-05-23 セッション1

### やること（開始時に書く）
- M1 Phase 0: Python/uv、PostgreSQL、Ollama/MLX、TTS、STT/VAD、pytest、初期設定ファイルの環境構築

### やったこと
- `mise.toml` に uv を追加し、Python 3.11.15 + uv 0.11.16 の開発環境を作成
- `pyproject.toml` / `uv.lock` / pytest markers / ruff 設定を追加
- `config/central_realtime.toml` を作成し、M1 は Ollama `qwen2.5:7b` + `say` を使う設定にした
- PostgreSQL 用 `docker-compose.yml` と `docker/postgres/` を追加し、pgvector / PGroonga 拡張を有効化
- `NodeConfig` の最小 TOML ローダーと Phase 0 unit test を追加
- Ollama を Homebrew で導入し、`qwen2.5:7b` を pull 済み
- `mlx-lm` を導入し、`mlx-community/Qwen2.5-7B-Instruct-4bit` のロード確認済み
- faster-whisper small と Silero VAD のロード確認済み
- macOS `say -v Kyoko` の音声ファイル生成確認済み

### 詰まったこと・解決したこと
- Silero VAD のロードに `torchaudio` が必要だったため依存に追加して解決
- Docker Desktop が未起動だったため起動し、PostgreSQL コンテナの healthcheck と拡張確認まで実施
- `irodori-tts` は Homebrew/PyPI に無く、ローカルサービスも未起動。公式 GitHub 実装を外部依存としてどう扱うかは人間確認待ち

### 次のセッションでやること
- `irodori-tts` を外部リポジトリとして導入するか、M1 では `say` のみで進めるか判断する
- Phase 1: WebSocket 1本で float32 エコーバックを実装する

## 2026-05-23 セッション2

### やること（開始時に書く）
- M1 Phase 0 の残対応確認。環境構築項目を再検証し、未完了項目が実作業で解消可能なら対応する

### やったこと
- Phase 0 の環境を再検証した
  - Python 3.11.15 / uv 0.11.16 は `mise exec -- uv ...` で利用可能
  - PostgreSQL コンテナは healthy、`vector` / `pgroonga` 拡張を確認
  - Ollama `qwen2.5:7b` が pull 済みであることを確認
  - `mlx_lm` / faster-whisper small / Silero VAD / macOS `say -v Kyoko` を確認
- `pyproject.toml` に混入していた不正な依存指定を削除し、`uv run pytest -m unit` が通る状態に戻した
- PEP 508 として壊れた依存指定を検出する unit test を追加した
- 公式 `Aratako/Irodori-TTS-Server` を隣接ディレクトリ `../Irodori-TTS-Server` に clone し、`uv sync` と `GET /health` 200 応答を確認した

### 詰まったこと・解決したこと
- 通常の shell PATH では `uv` が見えなかった
  → Tomoko リポジトリ内では `mise exec -- uv ...` を使えば動作することを確認
- `uv run pytest -m unit` が `pyproject.toml` の `pytest=*` / `pytest-asyncio=*` で失敗した
  → 不正行を削除し、PEP 508 検証テストを追加して再発を検出できるようにした
- irodori は PyPI/Homebrew パッケージではなく、公式 OpenAI 互換サーバーを外部サービスとして扱うのが現時点の最小導入と判断した

### 次のセッションでやること
- Phase 1: WebSocket 1本で float32 エコーバックを実装する
- irodori の音声合成実推論は M1 完了後、または TTSBackend 実装時にモデルロード時間と品質を測る

## 2026-05-23 セッション3

### やること（開始時に書く）
- M1 Phase 1: WebSocket 1本で float32 エコーバックを実装する

### やったこと
- `server/edge/main.py` を追加し、`/ws` で受け取ったバイナリを変換せずそのまま返す Phase 1 エコーバックを実装した
- `client/audio-worklet.js` で AudioWorklet から 32ms チャンクの float32 を取得する実装を追加した
- `client/main.js` / `client/index.html` / `client/styles.css` を追加し、マイク入力を WebSocket に送り、返ってきた float32 を AudioContext で再生する最小画面を作った
- `/ws` のバイナリエコーを検証する unit test を追加した
- `_docs/latency.md` にローカル `/ws` echo round trip の実測値を追記した

### 詰まったこと・解決したこと
- AudioWorkletNode は音声グラフ上で接続されていないと処理が止まる可能性があるため、無音 GainNode 経由で destination に接続して処理を維持するようにした
- in-app browser の `iab` がこの環境で利用できず画面の自動確認はできなかったため、HTTP 配信は `curl`、WebSocket は TestClient と実測スクリプトで確認した

### 次のセッションでやること
- Chrome で `http://127.0.0.1:8000` を開き、マイク許可後に実音声エコーが返ることを手動確認する
- Phase 2: Silero VAD で発話開始・終了の状態遷移を実装する

## 2026-05-23 セッション4

### やること（開始時に書く）
- M1 Phase 2: Silero VAD ラッパー、TomoroSession 初版、state イベント送信、クライアント state 表示、状態遷移 unit test を実装する

### やったこと
- `server/edge/pipeline/vad.py` に `SileroVAD` ラッパーと `VADProcessor` を追加した
- `server/session.py` に `TomoroSession` 初版を追加し、`idle` / `listening` / `processing` の状態遷移と state イベント送信を実装した
- 既存 `/ws` に VAD 処理を組み込み、バイナリエコーを維持したまま `{type: "state"}` JSON イベントを送るようにした
- クライアントに VAD state 表示を追加した
- `tests/unit/test_vad.py` と WebSocket state イベントテストを追加した
- `_docs/latency.md` に 300 / 400 / 500ms の無音閾値検出タイミングを追記した

### 詰まったこと・解決したこと
- unit test では Silero 実モデルをロードしないよう、VAD scorer を注入できる設計にした
- in-app browser の `iab` がこの環境で利用できず画面の自動確認はできなかったため、HTTP 配信は `curl`、WebSocket は TestClient で確認した

### 次のセッションでやること
- Chrome で `http://127.0.0.1:8000` を開き、マイク許可後に実音声で "listening" / "processing" が表示されることを手動確認する
- Phase 3: 常時 STT と ParticipationJudge の実装に進む

## 2026-05-23 セッション5

### やること（開始時に書く）
- M1 Phase 3: 常時 STT、ambient_logs、ParticipationJudge / WakeWordJudge、参加判断 unit test を実装する

### やったこと
- `ambient_logs` テーブル DDL を追加し、既存ローカル PostgreSQL にも適用した
- `Transcript` / `ParticipationDecision` DTO を追加した
- `FasterWhisperSTT` ラッパーを追加し、発話終了後に `SpeechSegment` を文字起こしする流れを実装した
- `ParticipationJudge` 抽象と `WakeWordJudge` を追加した
- `TomoroSession` に STT → ambient_logs → 参加判断 → idle 復帰の処理を追加した
- Wake word と常時 STT 経路の unit test を追加した
- Phase 3 の参加判断・DB 書き込みレイテンシーを `_docs/latency.md` に追記した

### 詰まったこと・解決したこと
- 初期化済み PostgreSQL には新しい docker init SQL が自動適用されないため、同じ DDL を手元 DB に直接適用して確認した
- faster-whisper の同期 transcribe がイベントループを塞がないよう、`asyncio.to_thread` に逃がした

### 次のセッションでやること
- Chrome 実音声で「トモコ」が `participation` イベントを出し、それ以外が `ambient_logs` にのみ残ることを手動確認する
- Phase 4: LLM ストリーミングで返答テキストを実装する

## 2026-05-23 セッション6

### やること（開始時に書く）
- Phase 2 の Chrome 手動確認結果を受けて、手動テストを妨げるエコーバック再生を止める
- M1 Phase 3 実装済み範囲を再確認し、常時 STT と ParticipationJudge の unit test が通る状態にする

### やったこと
- `/ws` が受け取った float32 バイナリを返送しないようにし、Phase 1 のエコーバックを停止した
- クライアントからエコー再生と往復レイテンシー計測を削除し、`participation` JSON イベントを status に出すようにした
- WebSocket unit test を「バイナリを受けるが返さない」「state JSON は送る」前提へ更新した
- Phase 3 実装済みの常時 STT / ambient_logs / WakeWordJudge 経路を unit test で再確認した

### 詰まったこと・解決したこと
- Chrome / in-app browser の自動接続はこの環境では利用できなかった
  → 既存 uvicorn reload サーバーと `curl http://127.0.0.1:8000/` でページ配信は確認済み。実マイク確認はユーザー側 Chrome で行う
- `mise exec -- ruff` は PATH 上に `ruff` が無かった
  → `mise exec -- uv run ruff check .` で確認した

### 次のセッションでやること
- Chrome 実音声で「トモコ」が `participation:called` 表示になり、それ以外が `ambient_logs` にのみ残ることを確認する
- Phase 4: LLM ストリーミングで返答テキストを実装する

## 2026-05-23 セッション7

### やること（開始時に書く）
- M1 Phase 4: LLM ストリーミングで返答テキストが完了しているか確認し、未完了箇所があればテスト先行で対応する

### やったこと
- `ThinkingInput` / `ThinkingEvent` DTO を追加し、`ThinkFastMode` から token delta を DTO で返すようにした
- `TomoroSession` で参加判定後に `InferenceRouter` 経由で LLM backend を選び、`reply_text` delta を WebSocket に順次送信する経路を確認した
- `InferenceRouter` に実測 latency による fallback と privacy 時の非 private fallback 禁止を追加した
- Phase 4 の thinking/session/router unit test を追加した
- Ollama `qwen2.5:7b` の初回 text delta レイテンシーを測定し、`_docs/latency.md` に記録した

### 詰まったこと・解決したこと
- 既存実装は `ThinkingMode -> session` が `str` 直渡しだった
  → `ThinkingEvent` DTO に包み、`reply_done` も明示イベントにした
- Ollama cold start 込みの初回 delta が 17931.7ms と大きい
  → Phase 4 の機能完了とは別に、M1 800ms 目標には warm-up / MLX / 事前生成の検討が必要

### 次のセッションでやること
- Chrome 実音声で「トモコ」に対して `reply_text` が画面にストリーミング表示されることを手動確認する
- Phase 5: TTS ストリーミングで声を出す

## 2026-05-23 セッション8

### やること（開始時に書く）
- M1 Phase 5: `say` ベースの TTSBackend、句読点単位の TTS 起動、WebSocket 音声バイナリ送信、クライアント再生、TTS レイテンシー計測を実装する

### やったこと
- `TTSInput` / `AudioChunkOut` DTO と `TTSBackend` 抽象を追加した
- `SayBackend` を追加し、`config/central_realtime.toml` の `tts_backend = "say"` から生成できるようにした
- `TomoroSession` で LLM の `text_delta` を蓄積し、句点・感嘆符・疑問符で TTS に流すようにした
- `/ws` で TTS 音声チャンクをバイナリ送信するようにした
- クライアントで WebSocket バイナリを `decodeAudioData` し、`AudioBufferSourceNode` をスケジューリング再生するようにした
- Phase 5 の unit test と perf test を追加し、`_docs/latency.md` に実測値を追記した

### 詰まったこと・解決したこと
- macOS `say` は真の逐次 PCM ストリーミングではなく AIFF ファイル生成型なので、M1 では句読点単位で 1 AIFF チャンクとして送る実装にした
- in-app browser はこの環境では `iab` が利用できず画面の自動確認はできなかった
  → `curl http://127.0.0.1:8000/` で HTTP 200、unit/perf で WebSocket/TTS 経路を確認した

### 次のセッションでやること
- Chrome 実音声で「トモコ」に対して声が返ることを手動確認する
- Phase 6a: 感情情報を DOM に出し、TTS に流す本文から emotion 行を分離する

## 2026-05-23 セッション9

### やること（開始時に書く）
- M1 Phase 5 の手動確認で「返答テキストは来るが音声が再生されない」問題を切り分け、テスト先行で修正する

### やったこと
- Chrome の `decodeAudioData` 互換性を優先し、`SayBackend` の出力を AIFF から 16kHz/16bit RIFF/WAVE に変更した
- クライアントで音声チャンク再生前に `AudioContext.resume()` を呼ぶようにした
- Phase 5 の unit/perf test を WAV 前提に更新した

### 詰まったこと・解決したこと
- `say -o speech.wav` だけでは `Opening output file failed: fmt?` で WAV を生成できなかった
  → `--data-format=LEI16@16000 -o speech.wav` を指定すると RIFF/WAVE を生成できることを確認した

### 次のセッションでやること
- Chrome 実音声で「トモコ」に対して声が返ることを再確認する
- まだ無音なら Chrome コンソールの `audio-error` と WebSocket バイナリ受信有無を確認する

### 人間確認
音声出ました。
codex tomoko: Phase 6a: 感情情報を DOM に出し、TTS に流す本文から emotion 行を分離する を対応して下さい。

## 2026-05-23 セッション10

### やること（開始時に書く）
- M1 Phase 6a: `EMOTION:<value>` 行を返答本文から分離し、emotion イベントを DOM に表示し、TTS には本文だけを流す

### やったこと
- `prompts/base_persona.md` に `EMOTION:<emotion>` 形式の出力指示を追加した
- `ThinkFastMode` でストリーム先頭の emotion 行を分離し、`ThinkingEvent(type="emotion")` を送るようにした
- `TomoroSession` で emotion イベントを WebSocket JSON として送信し、TTS style に反映した
- TTS には emotion 行を含めず、本文の `text_delta` だけを流すようにした
- クライアント DOM に `#emotion` 表示を追加した
- Phase 6a の unit test を追加し、`_docs/latency.md` に perf 再測定を追記した

### 詰まったこと・解決したこと
- LLM ストリームで `EMOTION:` が複数チャンクに分割される可能性がある
  → 先頭行だけバッファし、`EMOTION:` の prefix 途中なら待つ実装にした
- in-app Browser はこの環境で `iab` が利用できず視覚確認はできなかった
  → `uvicorn` + `curl` で HTML/JS 配信と `#emotion` DOM の存在を確認した

### 次のセッションでやること
- Chrome 実音声で emotion 表示が返答内容に応じて変わり、音声に `EMOTION:` が読み上げられないことを手動確認する
- Phase 6b: 静止画切り替えと声のトーンに進む

### 添付画像確認
- `/Users/seijiro/Sync/sync_work/by-llms/tomoko/.fyws/inputs/1507640092045475910/CleanShot_2026-05-23_at_16.03.162x.png`: 936x938。Tomoko UI、`stopped`、State `listening`、STT `-`、Sent `2226176`、Start/Stop ボタン、下部に「こんにちは！何か手伝えることがありますか？」が見える。

### 人間確認
#### Chrome 実音声で emotion 表示が返答内容に応じて変わり、音声に `EMOTION:` が読み上げられないことを手動確認する

## 2026-05-23 セッション11

### やること（開始時に書く）
- M1 Phase 6a 手動確認で `EMOTION:happy` が読み上げ・本文表示に漏れた問題を、テスト先行で修正する

### やったこと
- 添付画像から、LLM が `EMOTION:happy` の後に改行を入れず本文を続けたケースであることを確認した
- `ThinkFastMode` に `EMOTION:<value> 本文` 形式の分離を追加した
- `EMOTION:happy` が `reply_text` と TTS に流れない unit test を追加した

### 詰まったこと・解決したこと
- 既存実装は `EMOTION:<value>\n本文` だけを分離対象にしていた
  → 許可済み emotion の直後に空白と本文が続く場合も、emotion イベントと本文 delta に分離するよう修正した

### 次のセッションでやること
- Chrome 実音声で `EMOTION:` が読み上げられず、本文表示にも出ないことを再確認する
- Phase 6b: 静止画切り替えと声のトーンに進む

## 2026-05-23 セッション12

### やること（開始時に書く）
- Phase 6b: emotion イベントで静止画を音声より先に切り替え、TTS の声色を emotion に応じて変える
- `asset-factory_for_work/` で立ち絵画像を生成し、`assets/images/` に配置する

### やったこと
- `asset-factory_for_work/generate_tomoko_assets.py` を追加し、7 emotion 分の Tomoko 静止画 SVG を `assets/images/` に生成した
- `/assets` を FastAPI の StaticFiles として配信するようにした
- emotion イベントに `image` フィールドを追加し、音声チャンク送信より先に届くことを unit test で固定した
- クライアントは WebSocket で届いた `image` を `#tomoko-image` に表示するようにした
- `SayBackend` の emotion rate マッピングに `surprised` を追加し、全 emotion の rate を unit test で固定した
- `_docs/latency.md` に Phase 6b の perf 再測定を追記した

### 詰まったこと・解決したこと
- in-app Browser はこの環境で `iab` が利用できず視覚確認できなかった
  → `GET /` 200 と `/assets/images/tomoko-happy.svg` 200 (`image/svg+xml`) で配信確認した
- irodori backend はまだ Tomoko リポジトリ内に無いため、Phase 6b の声色は既存 `SayBackend` の rate 表現として実装した

### 次のセッションでやること
- Chrome 実音声で emotion に応じて静止画が切り替わり、声の速さが変わることを手動確認する
- M2 Phase 7: 短期記憶に進む

## 2026-05-23 セッション13

### やること（開始時に書く）
- wake word 後の会話継続、ambient 聞き取り復帰、「聞いてなかった」扱いを今後の前提設計として `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に追記する

### やったこと
- `ARCHITECTURE.md` に AttentionMode と `recorded` / `attended` / `remembered` の分離を追記した
- `PLAN.md` に Phase 7 の前に Phase 6.5 を追加する方針と完了条件を追記した
- `MEMORY.md` に Phase 7 前に AttentionMode を実装する判断を追記した

### 詰まったこと・解決したこと
- 既存 PLAN では Phase 6b の次が Phase 7 だった
  → 既存内容は編集せず、追記でその順序を否定して Phase 6.5 を先に実装する方針にした

### 次のセッションでやること
- Phase 6.5: `TomoroSession` に `attention_mode` を追加し、wake word 後の会話継続と ambient 復帰を unit test 先行で実装する

## 2026-05-23 セッション14

### やること（開始時に書く）
- MacBook スピーカー音声が内蔵マイクへ回り込む問題に対して、まずクライアント側 AEC だけを有効化して改善するか確認できる状態にする

### やったこと
- `client/main.js` の `getUserMedia` 制約で `echoCancellation` と `noiseSuppression` を有効化した

### 詰まったこと・解決したこと
- サーバー側の speaker echo filter はまだ入れない
  → AEC だけで劇的に改善する可能性があるため、最小変更で実測する

### 次のセッションでやること
- Chrome 実音声で Tomoko の TTS が再度 STT/参加判定に入るか確認する
- まだ回り込む場合は、TTS 再生中の時間窓 + 文字列類似度で speaker_echo を observer 扱いにする

## 2026-05-23 セッション14

### やること（開始時に書く）
- Phase 6.5: `TomoroSession` に `attention_mode` を追加し、wake word 後の会話継続、cooldown 経由の ambient 復帰、withdrawn、ambient/conversation log 境界を unit test 先行で実装する

### やったこと
- `AttentionMode` / `ParticipationMode` / `ParticipationContext` を DTO として `server/shared/models.py` に追加した
- `TomoroSession` に `attention_mode` を集約し、`ambient -> engaged -> cooldown -> ambient` と `withdrawn` の遷移を実装した
- `WakeWordJudge` を `attention_mode` 前提に拡張し、`engaged` / `cooldown` 中の継続発話を `invited` として扱うようにした
- `ambient_logs` に `attention_mode` / `attended` / `participation_mode` を保存し、`conversation_logs` には attended な会話ターンだけを書く writer を追加した
- 既存ローカル PostgreSQL に Phase 6.5 の DDL を適用した
- `tests/unit/test_attention_mode.py` を追加し、wake word 後の継続、ambient 復帰、withdrawn、会話ログ境界を固定した
- `_docs/latency.md` に Phase 6.5 の perf 再測定結果を追記した

### 詰まったこと・解決したこと
- 既存 unit/perf の in-memory ambient writer が旧シグネチャだった
  → `attention_mode` / `attended` / `participation_mode` を受け取る形に更新した
- init SQL は既存 PostgreSQL volume には自動再適用されない
  → `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/002_ambient_logs.sql` で手元 DB に適用した

### 次のセッションでやること
- Chrome 実音声で「トモコ」後の wake word なし継続発話が `invited` になり、無発話で `cooldown` / `ambient` に戻ることを確認する
- 「静かにして」で `withdrawn` になり、以後の発話に返答しないことを手動確認する
- Phase 7: attended な `conversation_logs` を短期記憶として `ThinkFastMode` の context に差し込む

## 2026-05-23 セッション15

### やること（開始時に書く）
- Tomoko 発話中の「ちょっと待って」「違う違う」「待って待って」などの割り込み検出を Phase 6.6.0 として `PLAN.md` / `MEMORY.md` に追記する

### やったこと
- `PLAN.md` に Phase 6.6.0 TurnTaking / BargeInDetector を追記した
- `MEMORY.md` に Tomoko 発話中もマイク入力を止めず、割り込みを分類する判断を追記した

### 詰まったこと・解決したこと
- 旧 Unity 実装では `isAITalking` 中に録音処理を止めていた
  → 今回は人間の割り込みを拾うため、その方針を否定し、STT 継続 + BargeInDetector で分類する設計にした

### 次のセッションでやること
- Phase 6.6.0: `BargeInDetector` を unit test 先行で実装し、echo / backchannel / soft_interrupt / hard_interrupt / new_question を分類する

## 2026-05-23 セッション16

### やること（開始時に書く）
- AEC だけでは MacBook スピーカー回り込みを防げなかったため、Phase 6.6.0 の初期実装として `BargeInDetector` と TTS 再生時間窓ベースの speaker echo / 割り込み分類を追加する

### やったこと
- `BargeInContext` / `BargeInDecision` DTO を追加した
- `server/gateway/turn_taking/barge_in.py` に `BargeInDetector` を追加し、`echo` / `backchannel` / `soft_interrupt` / `hard_interrupt` / `new_question` を分類するようにした
- `TomoroSession` が TTS 音声送信後に再生時間窓を推定し、その窓内の transcript を `BargeInDetector` に通すようにした
- `/ws` のデフォルト `TomoroSession` 生成時にも `BargeInDetector` を渡すようにした
- `echo` / `backchannel` は `observer` 相当にして返答ループへ入れず、hard interrupt は通常の参加判定へ進めるようにした
- `tests/unit/test_barge_in.py` を追加した
- `_docs/latency.md` と `MEMORY.md` に AEC だけでは不十分だった判断と Phase 6.6.0 初期実装を追記した

### 詰まったこと・解決したこと
- Tomoko 発話開始直後の startup grace が hard interrupt まで潰していた
  → `echo` 判定の次に hard interrupt を優先し、その後に startup grace を適用する順序にした
- 現在の `/ws` ループは返答生成/TTS送信を `process_audio_chunk()` 内で await している
  → 今回は TTS 送信後の推定再生窓で回り込みを分類する初期実装に留め、真の同時割り込みは後続の並行化対象にする

### 次のセッションでやること
- Chrome 実音声で Tomoko 音声の回り込みが `barge_in: echo` になり、返答ループしないことを確認する
- Tomoko 発話中の「違う違う」「待って待って」が `hard_interrupt` になり、通常の参加判定へ進むことを確認する
- 必要なら `/ws` 受信ループと reply/TTS 生成を並行化し、真の同時 barge-in に対応する

## 2026-05-23 セッション17

### やること（開始時に書く）
- Phase 6.6.1 AudioPlaybackControl を追加し、`turn_id` 付きの `audio_start` / `audio_end` / `audio_control stop` とクライアント側 source 停止を実装する

### やったこと
- `PLAN.md` に Phase 6.6.1 AudioPlaybackControl を追記した
- `MEMORY.md` に `turn_id` と `audio_start` / `audio_end` / `audio_control stop` の判断を追記した
- `TomoroSession` が返答ごとに `turn_id` を発行し、最初の音声バイナリより前に `audio_start`、返答完了時に `audio_end` を送るようにした
- hard interrupt 時に `audio_control stop` を送るようにした
- クライアントが `turn_id` ごとに `AudioBufferSourceNode` を保持し、サーバー命令で再生中/予約済み source を止めるようにした
- `tests/unit/test_phase5_tts.py` と `tests/unit/test_barge_in.py` に audio control の regression test を追加した
- `_docs/latency.md` に Phase 6.6.1 の検証結果を追記した

### 詰まったこと・解決したこと
- `audio_end` 後もクライアントでは再生が残っている可能性がある
  → `turn_id` は `audio_end` 後もサーバー側に保持し、再生時間窓内の hard interrupt で stop できるようにした

### 次のセッションでやること
- Chrome 実音声で hard interrupt 時に再生中/予約済み音声が止まることを確認する
- 必要なら `playback_started` / `playback_ended` テレメトリを追加して、サーバー側の推定再生窓を実測に置き換える

## 2026-05-23 セッション18

### やること（開始時に書く）
- git log と実装済みファイルを確認し、Phase 6.6.1 までの完了済み項目を `PLAN.md` のチェックボックスへ反映する

### やったこと
- `git log` / `LOG.md` / `MEMORY.md` / 実装ファイルを照合し、Phase 0 〜 Phase 6.6.1 の完了済み項目を `PLAN.md` でチェックした
- KokoroMLXBackend、Phase 7 以降、`playback_started` / `playback_ended` テレメトリなど未実装の項目は未チェックのまま残した

### 詰まったこと・解決したこと
- 作業ツリーには Phase 6.6.1 相当の未コミット変更が含まれていたため、既存変更を戻さず `PLAN.md` のチェック更新だけに絞った

### 次のセッションでやること
- Chrome 実音声で hard interrupt 時に再生中/予約済み音声が止まることを確認する
- 必要なら `playback_started` / `playback_ended` テレメトリを追加して、サーバー側の推定再生窓を実測に置き換える

## 2026-05-23 セッション19

### やること（開始時に書く）
- デバッグしやすいように、サーバーから届く `attention` イベントを画面に表示する

### やったこと
- クライアントのメーターに `Attention` 表示を追加した
- `client/main.js` で `{type: "attention", mode: ...}` を受け取り、`ambient` / `engaged` / `cooldown` / `withdrawn` をそのまま表示するようにした

### 詰まったこと・解決したこと
- サーバー側は既に attention イベントを送っていたため、クライアント表示だけの変更で対応した

### 次のセッションでやること
- Chrome 実音声で `Attention` が `engaged -> cooldown -> ambient` に遷移することを確認する

## 2026-05-23 セッション20

### やること（開始時に書く）
- クライアントから `playback_started` / `playback_ended` telemetry を `/ws` に返し、サーバー側で受け取れる土台を作る

### やったこと
- `PlaybackTelemetry` DTO を追加した
- クライアントが `AudioBufferSourceNode` の再生予定時刻に `playback_started`、`ended` イベントで `playback_ended` を `/ws` へ JSON 送信するようにした
- `/ws` 受信ループを binary 音声チャンクと JSON text event の両対応にした
- `TomoroSession` が playback telemetry を保持して log.info に残すようにした
- telemetry はまだ barge-in 判定には使わず、実測後に料理する前提にした

### 詰まったこと・解決したこと
- 既存 `/ws` は `receive_bytes()` 固定だった
  → `receive()` に変更し、bytes は音声、text は playback telemetry として分岐するようにした

### 次のセッションでやること
- Chrome 実音声で `playback_started` / `playback_ended` がサーバーログに出ることを確認する
- telemetry と回り込み transcript の時刻差を見て、speaker echo 窓への反映方法を決める

## 2026-05-23 セッション21

### やること（開始時に書く）
- サーバーログの見方を明確にし、正しいサーバー起動コマンドを `Makefile` に追加する

### やったこと
- `Makefile` を追加し、現在の正しい ASGI app である `server.edge.main:app` を `make server` / `make server-reload` で起動できるようにした
- `server.*` logger の info ログが uvicorn のターミナルに出るようにした

### 詰まったこと・解決したこと
- `README.md` の `server.gateway.main:app` は現状の実装とズレている
  → Makefile では現行の `server.edge.main:app` を正として固定した

### 次のセッションでやること
- `make server` で起動し、Chrome 実音声で playback telemetry の `log.info` が表示されることを確認する

## 2026-05-23 セッション22

### やること（開始時に書く）
- `make server-reload` のターミナルに playback telemetry の `logger.info` が出ない原因を確認して修正する

### やったこと
- `server.*` logger を `uvicorn.error` の handler に接続し、`server.session` の `log.info` が uvicorn 起動ターミナルに出るようにした

### 詰まったこと・解決したこと
- uvicorn のアクセスログは出ていたが、アプリ側 `server.*` logger は handler に接続されていなかった
  → `server.edge.main` の import 時に `server` logger へ uvicorn handler を接続した

### 次のセッションでやること
- `make server-reload` を再起動し、Chrome 実音声で `TomoroSession playback telemetry...` が出ることを確認する

## 2026-05-23 セッション23

### やること（開始時に書く）
- `make server-reload` でまだアプリ側 `server.*` ログが出ない問題を修正する

### やったこと
- uvicorn handler が import 時点で取得できない場合でも `server.*` ログを stderr に出す fallback handler を追加した

### 詰まったこと・解決したこと
- 前回修正は `uvicorn.error` handler が存在する前提だった
  → handler が空の場合に `server_logger.propagate = False` でログが捨てられていたため、専用 `StreamHandler` を追加した

### 次のセッションでやること
- `make server-reload` を再起動し、WebSocket 接続時に `INFO:server.edge.main:phase4 websocket connected` が出ることを確認する

## 2026-05-23 セッション24

### やること（開始時に書く）
- playback telemetry を chunk 単位にし、`playback_ended` 後の回り込み猶予で自己会話ループを抑止する

### やったこと
- クライアントの `playback_started` / `playback_ended` payload に `chunk_id` / `scheduled_audio_time` / `sent_audio_time` を追加した
- サーバーの `PlaybackTelemetry` DTO と `/ws` JSON parser を chunk 単位 telemetry に対応させた
- `TomoroSession` に `playback_ended + 1200ms` の speaker echo grace を追加した
- grace 中の transcript は hard interrupt 以外 `echo` / `continue_speaking` として通常参加判定へ流さないようにした
- regression test を追加した

### 詰まったこと・解決したこと
- 同一 `turn_id` に複数 audio chunk があるため、turn 単位 telemetry だけでは再生区間を特定しにくかった
  → chunk ごとに `chunk_id` を振り、開始/終了イベントを対応付けられるようにした

### 次のセッションでやること
- Chrome 実音声で `playback_ended` 直後の回り込みが `reason=playback_ended_grace` で observer 扱いになることを確認する

## 2026-05-23 セッション25

### やること（開始時に書く）
- サーバーのアプリログを確実に出し、必要ならファイルから LLM が読めるようにする

### やったこと
- `server.*` logger を uvicorn handler 依存から外し、stderr と `TOMOKO_LOG_FILE` の両方へ必ず出すようにした
- 通常起動では `logs/server.log` に出力するようにした
- `make server-debug` を追加し、DEBUG レベルで起動しつつ stdout/stderr も `logs/server-debug.log` へ `tee` するようにした
- `README.md` にログファイルと `make server-debug` を追記した

### 詰まったこと・解決したこと
- uvicorn reload 時の import 順序により、uvicorn handler へ接続する方式は不安定だった
  → アプリ側で専用 stderr handler と file handler を持つ方式にした

### 次のセッションでやること
- `make server-debug` で実音声確認し、`logs/server-debug.log` から playback telemetry と barge-in の時系列を読む

## 2026-05-23 セッション26

### やること（開始時に書く）
- `make server-debug` の実ログから回り込み軽減の効き方を確認し、ログが読みにくい問題を直す

### やったこと
- `logs/server-debug.log` を確認し、`playback_ended_grace` により少なくとも1回は自己会話候補が `echo` 扱いで抑止されていることを確認した
- `server-debug` で同じ `server.session` ログが二重にファイルへ入る原因を修正した
- `server-debug` は `tee` で stdout/stderr を1本の `logs/server-debug.log` に集約し、アプリ側 file handler は使わないようにした
- `server-debug` の uvicorn log level を `info` に戻し、WebSocket binary frame dump で会話ログが埋もれないようにした

### 詰まったこと・解決したこと
- `TOMOKO_LOG_FILE=logs/server-debug.log` と `tee -a logs/server-debug.log` を同時に使っていたため、アプリログだけ二重記録されていた
  → `TOMOKO_LOG_FILE` を空にしたときは file handler を張らないようにし、debug target では `tee` だけで記録する方式にした

### 次のセッションでやること
- 新しい `make server-debug` で実音声確認し、重複なしのログで `playback_ended_grace` と `attention_engaged_followup` の残り方を比較する

## 2026-05-23 セッション27

### やること（開始時に書く）
- `playback_ended` 後の回り込み猶予を2秒へ延長し、再生中 active chunk 区間でも自己会話を抑止する

### やったこと
- `TomoroSession` の `playback_echo_grace_ms` デフォルトを 1200ms から 2000ms に変更した
- `playback_started` / `playback_ended` の `(turn_id, chunk_id)` を active playback chunk として管理するようにした
- active playback chunk が存在する間は hard interrupt 以外を `echo` / `continue_speaking` に倒し、通常の参加判定へ流さないようにした
- active chunk 中の通常 follow-up 抑止、active chunk 中の hard interrupt 通過、2秒猶予の unit test を追加した

### 詰まったこと・解決したこと
- `playback_ended` 後の猶予だけでは、次 chunk が再生中のタイミングで拾った音が `new_question` として参加判定へ進む
  → クライアント telemetry の active chunk 状態もサーバー側 barge-in 判定に使うようにした

### 次のセッションでやること
- `make server-debug` で実音声確認し、`reason=playback_active_chunk` と `reason=playback_ended_grace` が自己会話候補を抑止しているか確認する

## 2026-05-23 セッション28

### やること（開始時に書く）
- 実ログ確認後、回り込み猶予を1.2秒へ戻し、ログ timestamp をミリ秒単位にする

### やったこと
- `logs/server-debug.log` を確認し、改善の主因が `playback_active_chunk` であることを確認した
- `TomoroSession` の `playback_echo_grace_ms` デフォルトを 2000ms から 1200ms に戻した
- アプリログの timestamp を `YYYY-MM-DD HH:MM:SS.mmm` 形式に変更した
- 1.2秒猶予の unit test に更新した

### 詰まったこと・解決したこと
- 秒精度ログでは `playback_ended` から `playback_ended_grace` までの正確な差分が見えなかった
  → Python logging formatter に `%(msecs)03d` を追加し、次回ログからミリ秒単位で読めるようにした

### 次のセッションでやること
- 空にした `logs/server-debug.log` で実音声確認し、`playback_ended` から `playback_ended_grace` までの ms 差分を見る

## 2026-05-23 セッション29

### やること（開始時に書く）
- STT が何を認識して会話継続しているかをログで見えるようにする

### やったこと
- 発話終了後の `transcriber.transcribe()` 直後に transcript 全件を `server.session` logger に出すようにした
- ログには `text` / `speaker` / `audio_level_db` / `attention_mode` / `state` を含める

### 詰まったこと・解決したこと
- 小さな物音や誤 STT が `attention_engaged_followup` / `attention_cooldown_followup` に流れているかをログだけでは判別しづらかった
  → participation 判定前の transcript を必ず出すことで、次回ログから原因を追えるようにした

### 次のセッションでやること
- `make server-debug` のログで transcript と participation / barge-in の対応を見て、短すぎる transcript や空に近い STT を observer 扱いにする条件を決める

## 2026-05-23 セッション30

### やること（開始時に書く）
- Phase 6.6.1.2 を `PLAN.md` に追加し、小さな物音や Whisper hallucination による follow-up 誤起動を抑制する

### やったこと
- `PLAN.md` に Phase 6.6.1.2: Follow-up 誤起動の抑制を追加した
- `ParticipationContext` に `audio_level_db` を渡すようにした
- `WakeWordJudge` で engaged/cooldown follow-up 前に低信頼 transcript を `observer` に倒すようにした
- 空文字、1〜2文字、低音量短文、Whisper 定型 hallucination を低信頼 follow-up として扱うようにした
- 低信頼 observer 発話では attention idle を延長しないようにした
- attention decay は `idle` 状態の無音 chunk だけで積算するようにした
- participation / attention の unit test を追加した

### 詰まったこと・解決したこと
- 発話終了待ちの silence chunk まで attention idle に積算すると、発話中に cooldown/ambient へ進みすぎる
  → `process_audio_chunk` の順序を調整し、state 遷移後に `state == idle` のときだけ無音積算するようにした

### 次のセッションでやること
- `make server-debug` で実音声確認し、`reason=low_confidence_followup` が小さな物音や定型 hallucination を observer に落としているか確認する

## 2026-05-23 セッション31

### やること（開始時に書く）
- STT を MLX Whisper 推論へ切り替え可能にし、ストリーミング partial transcript と速度ベンチを実装する

### やったこと
- `BackendSpec` に `streaming` / `stream_interval_ms` / `stream_min_audio_ms` を追加した
- `MlxWhisperSTT` を追加し、`mlx_whisper` で一時 WAV を transcribe する経路を実装した
- `TomoroSession` が VAD listening 中に streaming STT partial を出し、発話終了時に stream buffer を reset するようにした
- `config/central_realtime.toml` の STT backend を `local_whisper_mlx_small` に切り替えた
- `make bench-stt` と `tests/perf/test_stt_latency.py` を追加し、faster-whisper small と MLX Whisper small を同じ音声で比較できるようにした
- `mlx-whisper` を optional dependency に追加した

### 詰まったこと・解決したこと
- `mlx-community/whisper-small` は存在しない/取得できないモデル ID だった
  → `mlx-community/whisper-small-mlx` に修正してベンチが通るようにした
- 初回 MLX はモデル取得と cache 作成が乗るため `warm_ms=13404.8` と重いが、warm 後の measured は `102.1ms` だった

### 次のセッションでやること
- Chrome 実音声で `transcript_partial` の出方と WebSocket 処理への詰まりを確認する
- partial STT を同期 await ではなく background task 化する必要があるか、実ログで判断する

## 2026-05-23 セッション32

### やること（開始時に書く）
- サーバー起動時の一般的な warm-up 仕組みを作り、MLX Whisper STT の初回コストを接続前に払う

### やったこと
- FastAPI lifespan startup で `_warm_up_app()` を実行する初期化フックを追加した
- STT transcriber に `warm_up()` を持たせ、`FasterWhisperSTT` / `NullTranscriber` は no-op、`MlxWhisperSTT` は短い無音 `SpeechSegment` を1回 transcribe するようにした
- startup warm-up の開始/完了と `elapsed_ms` を `server.edge.main` logger に出すようにした
- startup warm-up と STT backend warm-up の unit test を追加した
- キャッシュ済み MLX Whisper small で `_warm_up_app()` を直接実行し、`elapsed_ms=2015.5` を確認した

### 詰まったこと・解決したこと
- FastAPI の `on_event("startup")` は非推奨警告が出るため使わず、lifespan handler に切り替えた

### 次のセッションでやること
- `make server-debug` 起動時に startup warm-up log が出てから WebSocket 接続されることを確認する
- Chrome 実音声で最初の STT に MLX 初回コストが乗らないことを確認する

## 2026-05-23 セッション33

### やること（開始時に書く）
- Phase 6.6.2: STT Hallucination Filter を実装し、MLX Whisper の反復 hallucination を partial 表示・参加判定・ambient_logs の手前で抑制する

### やったこと
- `TranscriptFilterDecision` DTO と `server/edge/pipeline/stt_filter.py` を追加した
- final transcript の `drop` は participation 判定・ambient_logs へ進めず、partial の `suppress_partial` は UI へ送らないようにした
- `TomoroSession` に filter を接続し、判定結果を `server.session` log に出すようにした
- 実ログで見えた `また` 反復、`Have` 反復、`今日は日曜日の日曜日です`、低音量の「お疲れ様でした」、`ご視聴ありがとうございました` 系を unit test で固定した
- `PLAN.md` に append-only で Phase 6.6.2 実装結果を追記した

### 詰まったこと・解決したこと
- 最初の反復検出では単発の「今日は日曜日の日曜日です」が accept になった
  → 実ログ例に合わせて `日曜日の日曜日` を明示的な repetition hint として扱った
- drop 済み transcript を ambient_logs に保存するか迷う余地があった
  → 今回は記憶土台を汚さないことを優先し、filter `drop` は完全に保存しない方針にした

### 次のセッションでやること
- `make server-debug` の実音声ログで `TomoroSession transcript filter ... action=drop` が出ることを確認する
- 過剰 drop があれば、対象語を減らすより音量・長さ条件を足して調整する

## 2026-05-23 セッション34

### やること（開始時に書く）
- Phase 6.6.3 のうち、kokoro/irodori TTS 差し替え前に効果が大きい最小限の TomoroSession 状態保護だけを実装する

### やったこと
- `TomoroSession` に `asyncio.Lock` を追加し、audio turn と playback telemetry の状態更新だけを短く保護した
- `audio_start` / `audio_end` / `audio_control stop` は lock 内で送信イベントを予約し、実際の `send_event` は lock 外で行うようにした
- `_audio_sequence` 採番と `_tomoko_speaking_until` 更新を lock 内に入れた
- `handle_playback_telemetry` を async 化し、`server/edge/main.py` から await するようにした
- `tests/unit/test_session_concurrency.py` を追加し、並行 start/stop の二重送信防止と telemetry async 契約を固定した
- `PLAN.md` に Phase 6.6.3 の最小実装結果を append-only で追記した

### 詰まったこと・解決したこと
- 現状の `/ws` 受信ループ自体は直列なので、大規模な actor/queue 化は今やる必要がない
  → kokoro / irodori TTS 前に効く audio turn / playback state の保護だけに絞った
- `send_event` を lock 内で await すると逆に詰まりやすい
  → lock 内ではイベント内容の予約と状態確定だけを行い、I/O は lock 外へ出した

### 次のセッションでやること
- kokoro / irodori TTS 差し替え時に、必要なら `/ws` 受信ループと reply/TTS 生成の本格並行化を検討する

## 2026-05-23 セッション35

### やること（開始時に書く）
- 別 LLM からの `TomoroSession` 肥大化リスクに関するフィードバックを確認し、妥当なら `PLAN.md` に Phase 6.6.4 として対応方針を追記する

### やったこと
- `server/session.py` の責務集中状況を確認し、フィードバックは妥当と判断した
- `PLAN.md` に Phase 6.6.4: TomoroSession responsibility split を append-only で追記した

### 詰まったこと・解決したこと
- 実装変更ではなく計画追記のため、テスト実行は不要と判断した

### 次のセッションでやること
- Phase 6.6.4 に入る場合は、先に characterization test を追加してから `AudioTurnController` / `ReplyAudioPipeline` の境界を切る

## 2026-05-23 セッション36

### やること（開始時に書く）
- Phase 6.6.4: `TomoroSession` の責務分割を実装する
- 先に既存挙動を固定する characterization test を追加し、その後 `AudioTurnController` / reply-TTS helper の境界を切る

### やったこと
- `AudioTurnController` を追加し、audio turn / playback telemetry / audio sequence / speaker echo grace を `TomoroSession` から切り出した
- `ReplyAudioPipeline` を追加し、`ThinkingEvent` から emotion / reply text / TTS flush command への変換を切り出した
- `TomoroSession` は既存 public entrypoint と WebSocket protocol を変えず、会話状態機械と送信順序のオーケストレーションに寄せた
- `ARCHITECTURE.md` に状態所有ルールを追記した
- `tests/unit/test_audio_turn_controller.py` と `tests/unit/test_reply_audio_pipeline.py` を追加した
- `PLAN.md` と `MEMORY.md` に Phase 6.6.4 の実装結果と確定判断を追記した

### 詰まったこと・解決したこと
- 既存テストが `TomoroSession` の private helper を直接触っていた
  → 挙動互換の delegate helper / property を残し、実処理だけ controller に移した
- `ruff` で import 順の指摘が出た
  → `ruff check . --fix` で整列した

### 次のセッションでやること
- kokoro / irodori TTS 差し替え時に、必要なら `ReplyAudioPipeline` の command 境界を拡張する
- `/ws` 受信ループと reply/TTS 生成の本格並行化が必要になったら、今回切った `AudioTurnController` 境界を前提に進める

## 2026-05-23 セッション37

### やること（開始時に書く）
- `ReplyAudioPipeline` が emotion-to-image の asset mapping まで持っている境界の妥当性を確認し、必要なら修正する

### やったこと
- 指摘どおり、`ReplyAudioPipeline` が `/assets/images/...` を知るのは UI asset mapping への関心漏れと判断した
- `ReplyAudioPipeline` から画像 path 対応表と `image` field を削除した
- emotion event の `image` 付与は WebSocket event を組み立てる `TomoroSession` 側へ戻した
- `tests/unit/test_reply_audio_pipeline.py` を、pipeline が emotion 値と style だけを返す期待に更新した

### 詰まったこと・解決したこと
- 画像は TTS flush 変換ではなく表示 event の enrichment なので、`ReplyAudioPipeline` では扱わない方が境界として自然だった

### 次のセッションでやること
- emotion-to-image mapping がさらに肥大化する場合は、`TomoroSession` 直置きではなく `EmotionAssetMapper` のような表示 event 用 helper へ切り出す

## 2026-05-23 セッション38

### やること（開始時に書く）
- reply 周辺の境界を `session -> reply -> audio/emotion/image` の依存方向へ修正する
- `TomoroSession` から emotion-to-image mapping を外し、reply 配下の image helper へ移す

### やったこと
- `server/gateway/reply/` を追加し、reply 配下を `pipeline.py` / `audio.py` / `emotion.py` / `image.py` に分けた
- `TomoroSession` は `ReplyPipeline` だけを import する形にした
- `ReplyPipeline` が `ReplyAudioPlanner` / `ReplyEmotionState` / `EmotionImageMapper` を内部利用する構造にした
- 既存互換のため `server/gateway/reply_audio.py` は re-export の shim にした
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に境界修正を追記した

### 詰まったこと・解決したこと
- 直前修正では image mapping が `TomoroSession` 側に戻っていた
  → 「session は reply だけを見る」という依存方向を優先し、image mapping は reply 配下の helper に移した

### 次のセッションでやること
- reply command が増える場合も、`TomoroSession` が audio/emotion/image の個別 helper を直接 import しないように保つ

## 2026-05-23 セッション39

### やること（開始時に書く）
- reply 周辺の境界を `session -> reply -> audio/display` に修正する
- display 側に emotion と画像 asset 解決をまとめ、将来の pose / animation / 追加表示媒体に備える

### やったこと
- `server/gateway/reply/display.py` を追加し、emotion 状態と image asset mapping を `ReplyDisplayPlanner` に集約した
- `ReplyPipeline` は `ReplyAudioPlanner` と `ReplyDisplayPlanner` の二系統だけを内部利用する形にした
- 旧境界の `reply/emotion.py` と `reply/image.py` を削除した
- display planner の単体テストを追加した

### 詰まったこと・解決したこと
- `audio/emotion/image` の三分割だと、将来 image 以外の表示要素が増えた時に display concern が散らばる
  → emotion は表示状態を駆動する入力でもあるため、reply 配下では `display` にまとめる判断に変更した

### 次のセッションでやること
- 表示要素が image 以外に増える場合も、`ReplyDisplayPlanner` で display command を拡張し、`TomoroSession` に個別表示ルールを戻さない


## 人間メモ

1. kokoroをmlxかつstreaming　する TTS backend を入れる
2. say の同期実行前提を外す
3. ReplyPipeline から sentence flush された単位を即 TTS streaming に流す
4. audio chunk を生成され次第 /ws で送る
5. barge-in 時に生成中 TTS / playback を止められるようにする

## 2026-05-23 セッション40

### やること（開始時に書く）
- kokoro-mlx streaming TTS backend を追加する
- say の同期実行前提を外し、sentence flush 単位で TTS streaming task に流す
- 生成された audio chunk を即 `/ws` に送り、barge-in 時に生成中 TTS と playback を停止できるようにする

### やったこと
- `KokoroMLXBackend` を追加し、`kokoro_mlx.KokoroTTS.generate_stream()` の numpy chunk を RIFF/WAVE `AudioChunkOut` として返すようにした
- `config/central_realtime.toml` の TTS backend を `kokoro_mlx` に切り替えた
- `pyproject.toml` / `uv.lock` に `kokoro-mlx` と `misaki[ja]` を追加し、import 可能なことを確認した
- `TomoroSession` の reply 生成を background task 化し、TTS worker が sentence flush を queue から順次 streaming する構造にした
- hard interrupt で reply task / TTS worker を cancel し、`audio_control stop` を送るようにした
- Kokoro backend と streaming/cancel の unit test を追加した
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に今回の判断と検証結果を追記した

### 詰まったこと・解決したこと
- `process_audio_chunk()` が reply/TTS を await すると `/ws` のマイク入力が止まり、barge-in が真に並行処理できない
  → 参加判断後は reply generation task を起動して戻る構造へ変更した
- Kokoro の streaming chunk は raw numpy audio なので、そのまま送ると既存クライアントの `decodeAudioData` で読めない
  → chunk ごとに RIFF/WAVE に包み、クライアント側ロジックを増やさない形にした
- 既存テストは reply 完了を `process_audio_chunk()` の戻りと同時だと仮定していた
  → 必要なテストだけ `_wait_for_reply_task()` で明示的に待つよう更新した

### 次のセッションでやること
- Kokoro 実モデルの初回 download / warm-up と real latency を測り、`_docs/latency.md` に記録する
- Chrome 実音声で Kokoro の日本語品質、chunk 境界の途切れ、hard interrupt 停止を確認する

## 2026-05-23 セッション41

### やること（開始時に書く）
- kokoro TTS に切り替え後、音声が再生されない原因をサーバーログから確認する

### やったこと
- `logs/server-debug.log` を確認し、wake word 後の返答生成で kokoro の日本語G2Pが `fugashi` / `unidic` の辞書未導入により落ちていることを特定した
- `KokoroMLXBackend` が日本語音声では `pyopenjtalk` 版の `misaki.ja.JAG2P` を使うようにした
- `jf_` / `jm_` voice では kokoro に `language="ja"` を明示するようにした
- kokoro backend の回帰テストを追加し、実 backend でも短い日本語から RIFF/WAVE chunk が出ることを確認した

### 詰まったこと・解決したこと
- `misaki[ja]` は `unidic` パッケージを入れるが、辞書本体の `mecabrc` は存在しなかった
  → Tomoko 側では `unidic` 辞書に依存せず、既に通る `pyopenjtalk` 経路へ固定して解決した

### 次のセッションでやること
- `make server-debug` で Chrome 実音声確認し、kokoro 音声の再生、chunk 境界、初回 warm-up レイテンシーを確認する

## 2026-05-24 セッション1

### やること（開始時に書く）
- kokoro TTS で音声が出たり出なかったりする原因をサーバーログから確認する

### やったこと
- `logs/server-debug.log` を確認し、音声が出たターンでは `playback_started` / `playback_ended` telemetry が返っていることを確認した
- 音声が出なくなったターンでは `Voice file not found: .../voices/jf_beta.safetensors` で reply/TTS 生成が落ちていることを特定した
- 手元の Kokoro モデルに存在する日本語 voice が `jf_alpha`, `jf_gongitsune`, `jf_nezumi`, `jf_tebukuro`, `jm_kumo` で、`jf_beta` が存在しないことを確認した
- `KokoroMLXBackend` で未存在 voice を選んだ場合、設定上の既定 voice へフォールバックするようにした
- 実 kokoro backend で `sad` style の日本語から RIFF/WAVE chunk が出ることを確認した
- 英語・中国語混入についてログを確認し、`因为` / `washed` / `TTTT...` などはSTT側の hallucination と判断した
- 低音量のASCII-only transcriptを `TranscriptFilter` で drop するようにした
- `prompts/base_persona.md` に本文は日本語だけで返す指示を追加した

### 詰まったこと・解決したこと
- emotion style の `sad` / `thinking` / `gentle` が `jf_beta` に固定されていた
  → model に存在しない voice だったため、その感情の返答だけ無音になっていた
  → voice の存在確認と fallback を入れて解決した
- `washed` のような英語単語だけの低信頼STTが accept されていた
  → 低音量ASCII-only発話は `low_audio_ascii_text` として落とすようにした

### 次のセッションでやること
- `make server-debug` を再起動して Chrome 実音声で `sad` / `thinking` 系 emotion でも音声が出ることを確認する
- 英語・中国語混入が続く場合は、`reply_text` delta もサーバーログへ出して STT由来かLLM出力由来かを分離する

### 追加対応
- `TomoroSession` が `reply_text` delta を WebSocket 送信する直前に `server.session` log へ出すようにした
- `ruff check server/session.py` と `pytest -m unit` が通過した

## 2026-05-24 セッション2

### やること（開始時に書く）
- 応答に英語が残る問題について、まずサーバーログから STT 由来か LLM 返答由来かを切り分ける

### やったこと
- `logs/server-debug.log` を確認し、英語混入が STT hallucination と LLM 返答本文由来の2種類あることを切り分けた
- `gallery gallery...` / `llllllll...` は `TranscriptFilter` で drop/suppress されていた
- `hear you` / `TAXONOMY` / `Goes from remembering to evaluating.` は `reply_text delta` に出ていたため、LLM 返答本文由来と判断した
- `ReplyPipeline` に `ReplyTextSanitizer` を追加し、表示用 `reply_text` と TTS 用 `tts_text` の両方へ流す前に ASCII 英字などの日本語外文字を除去するようにした
- 分割 token の `TAX` `ON` `OM` `Y` も表示/TTSへ出ない unit test を追加した

### 詰まったこと・解決したこと
- プロンプトでは「本文は日本語だけ」と指示済みだったが、LLM が `reply_text delta` に英語を出すケースが残っていた
  → プロンプトだけに依存せず、サーバー側 reply 境界で出力契約を守るようにした

### 次のセッションでやること
- Chrome 実音声で `reply_text` に英語が残らないことを確認する
- まだ不自然な空白や記号だけが残る場合は、`ReplyTextSanitizer` の許可文字を追加で絞る

### 追加対応
- 最初に `Irodori-TTS-Server` の OpenAI 互換 HTTP backend を `irodori_mlx` として実装したが、これは mlx-audio 版ではないため撤回した
- GitHub 最新の `mlx-audio` を `uv add "mlx-audio @ git+https://github.com/Blaizzy/mlx-audio.git"` で追加した
- `IrodoriMLXBackend` を `mlx_audio.tts.utils.load_model()` で `mlx-community/Irodori-TTS-500M-v3-8bit` を直接ロードする実装に置き換えた
- `config/central_realtime.toml` の `tts_backend` を `irodori_mlx` に切り替えた
- Irodori v3 は mlx-audio 側で真の `stream=True` が未実装なので、Tomoko の sentence flush / TTS queue により文単位で逐次生成する
- 実モデル smoke で `こんにちは。` から RIFF/WAVE chunk が返ることを確認した
- `_docs/latency.md` にキャッシュ済み短文合成 2959.1ms を追記した
- `ruff check .` と `pytest -m unit` が通過した

## 2026-05-24 セッション3

### やること（開始時に書く）
- Irodori backend が本当に streaming / MLX / Irodori で動いているか確認する
- 起動時 TTS warm-up の有無を確認し、未実装なら追加する
- mlx-audio の Irodori v2 で streaming 可能か確認し、可能なら v2 へ切り替える

### やったこと
- `irodori_mlx` は `mlx_audio.tts.utils.load_model()` で `mlx-community/Irodori-TTS-500M-v3-8bit` を直接ロードしており、MLX + Irodori v3 で動いていることを確認した
- 一方で `mlx-audio` の Irodori 実装は `Model.generate(..., stream=True)` が v2/v3 共通で `NotImplementedError` になるため、真の streaming ではないことを確認した
- v2 にしても streaming は有効にならないため、v3 recommended / sway sampling / automatic duration の利点を優先して v3 のままにした
- `TTSBackend.warm_up()` を追加し、`IrodoriMLXBackend` は短文 `あ。` を一度生成してモデルロードと初回生成コストを起動時に払うようにした
- `_create_default_tts_backend()` が `app.state._default_tts_backend` に backend をキャッシュし、warm-up 済み backend を `/ws` session で再利用するようにした
- `_warm_up_app()` が STT に続いて TTS backend も warm-up するようにした
- cached `_warm_up_app()` 実測で STT 1262.1ms、Irodori MLX TTS 2831.9ms を確認し、`_docs/latency.md` に追記した

### 詰まったこと・解決したこと
- 「streaming」と呼べるのは現時点では Tomoko の文単位 queue streaming であり、Irodori モデル内部の chunk streaming ではなかった
  → v2 切り替えでは解決しないため、v3 のまま起動時 warm-up で初回遅延を前払いする方針にした

### 次のセッションでやること
- Chrome 実音声で Irodori MLX の音質と、起動後初回返答の体感レイテンシーを確認する
- Irodori の 3秒級レイテンシーが厳しい場合は、Kokoro を通常会話用、Irodori を高品質モード用にする切り替え方針を検討する

## 2026-05-24 セッション4

### やること（開始時に書く）
- MLX + Irodori v2/v3 + streaming 必須の制約で、レイテンシーを下げられる別 Irodori TTS backend を作れるか確認する
- 真の model-internal streaming が未実装なら、Irodori MLX のまま先頭音声を早く返す実用的な streaming backend を実装する

### やったこと
- mlx-audio の Irodori 実装を確認し、v2/v3 共通で `stream=True` が未実装であることを再確認した
- `seconds` 明示と `num_steps=6` により、warm-up 済み短文生成が 100ms 前後まで下がることを実測した
- `IrodoriMLXStreamBackend` を追加し、text を短い日本語発話単位に分割して各単位を Irodori v3 + MLX で逐次生成するようにした
- `config/central_realtime.toml` の default TTS backend を `irodori_mlx_stream` に切り替えた
- 実モデル smoke で warm-up 後 `うん、わかった。少し待ってね。` が 2 chunk、first 107.0ms、total 206.9ms で返ることを確認した
- `_docs/latency.md` に実測値を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- Irodori の真の model-internal streaming は現時点の mlx-audio では使えない
  → backend 境界で短い Irodori 生成を複数回走らせ、生成できた単位から `/ws` に流す方式にした
- 初回プロセスではモデルロード込みで first chunk が約 2.9 秒かかる
  → 既存の起動時 TTS warm-up と backend cache に乗せることで、会話時の first chunk を約 107ms にした

### 次のセッションでやること
- Chrome 実音声で `irodori_mlx_stream` の文節ごとの自然さと音切れを確認する
- 必要なら `max_chars` と `seconds` 推定式を実音声の聞こえ方に合わせて微調整する

## 2026-05-24 セッション5

### やること（開始時に書く）
- Qwen3-TTS の小さいモデルと大きいモデルを `mlx-audio` 経由の Tomoko TTS backend として追加する
- `irodori_mlx` / `irodori_mlx_stream` / Qwen3 小モデル / Qwen3 大モデルを同じ日本語文でベンチし、first chunk と総時間を `_docs/latency.md` に記録する

### やったこと
- `Qwen3MLXTTSBackend` を追加した
  - `mlx-audio` の `Model.generate(..., stream=True)` を使う
  - 同期 generator を worker thread で消費し、chunk が出るたび `AudioChunkOut` に変換する
  - `lang_code="Japanese"` 固定、emotion style は `instruct` / `speed` へ変換する
- `config/central_realtime.toml` に `qwen3_tts_mlx_small` と `qwen3_tts_mlx_large` を追加した
- `_tools/bench_tts_backends.py` を追加し、4 backend を同じ文で測れるようにした
- 初回実行で Qwen3 small / large を Hugging Face から取得した
- キャッシュ済み再実行で以下を確認した
  - `irodori_mlx`: first 659.2ms / total 659.2ms / 1 chunk
  - `irodori_mlx_stream`: first 96.6ms / total 192.7ms / 2 chunks
  - `qwen3_tts_mlx_small`: first 142.6ms / total 545.3ms / 8 chunks
  - `qwen3_tts_mlx_large`: first 216.7ms / total 820.5ms / 8 chunks
- 聞き比べ用 WAV を `artifacts/tts-bench-cached/` に保存した
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` に実測と判断を追記した

### 詰まったこと・解決したこと
- Qwen3 の初回ベンチはモデルダウンロード時間が warm-up に混ざった
  → 同じスクリプトを再実行し、キャッシュ済みの比較値を採用した
- 自然さは自動判定できない
  → WAV を保存し、人間の試聴判断に回す

### 次のセッションでやること
- `artifacts/tts-bench-cached/*.wav` を聞いて、Tomoko の声として許容できる候補を選ぶ
- Qwen3 が自然なら default backend の切り替え、Irodori stream が自然なら現状維持を判断する

## 2026-05-24 セッション6

### やること（開始時に書く）
- LLM の返答に英語が混じる前提で、TTS 用に軽量 LLM で日本語化する実験プログラムを追加する
- 日本語化の出力品質とレイテンシーを確認できるベンチプログラムを作り、実測結果を残す

### やったこと
- `_tools/normalize_tts_text_mlx.py` を追加した
  - 単一テキストを TTS 用日本語へ正規化する
  - 既定は `mlx-vlm` + `mlx-community/gemma-4-e2b-it-4bit`
  - 出力 JSON に load / first token / first text / total latency を残す
- `_tools/bench_tts_text_normalizer_mlx.py` を追加した
  - 日本語文、英語混じり文、時刻、API用語など複数サンプルを同一モデルで測る
  - `logs/tts-text-normalizer/gemma4-e2b/summary.md` と `results.jsonl` を出力する
- `mlx-vlm` を依存追加した
- `mlx_lm` の Gemma 4 E2B text-only 変換を試したが、手元の `mlx_lm` では重み不一致でロード不可だった
- `mlx-community/gemma-4-e2b-it-4bit` を `mlx-vlm` 経由でロードし、正規化出力とレイテンシーを確認した
- warm 後の複数サンプルでは first token 87.4〜184.1ms、first text 162.7〜243.6ms、total 166.5〜247.3ms だった

### 詰まったこと・解決したこと
- `jorch/gemma-4-e2b-it-lm-4bit` と `mlx-community/Gemma4-E2B-IT-Text-int4` は `mlx_lm.load()` で重み不一致になった
  → `mlx-vlm` を導入し、`mlx-community/gemma-4-e2b-it-4bit` を使う形に切り替えた
- `mlx-vlm` の stream は token ごとに空文字を返し、最後にまとまった日本語 text が出ることがある
  → latency は `first_token_ms` と `first_text_ms` を分けて記録するようにした

### 次のセッションでやること
- TTS pipeline に入れる場合は、`ReplyPipeline` の TTS flush 直前に `ReplySpeechNormalizer` として統合する
- その際は毎文 LLM 正規化するのではなく、ASCII 英字や時刻が混じる文だけ正規化対象にする

## 2026-05-24 セッション7

### やること（開始時に書く）
- Irodori stream を x1 速度に戻す
- TTS 直前で日本語以外・時刻・英字混入を検出し、混入時だけ Gemma 4 E2B で TTS 用日本語化する
- 起動時に Gemma 正規化器を warm-up し、混入時の初回レイテンシーを前払いする

### やったこと
- `ReplySpeechNormalizer` を追加した
  - 純日本語はそのまま返す
  - ASCII 英字、時刻、非日本語文字体系を検出した時だけ `mlx-vlm` + `mlx-community/gemma-4-e2b-it-4bit` で TTS 用日本語へ変換する
- `TomoroSession._flush_tts_text()` に正規化を入れ、TTS backend へ渡す直前の音声用テキストだけ変えるようにした
- `ReplyPipeline` は表示用には sanitizing 済み delta を使い、TTS buffer には raw delta を保持するようにした
  - これで英字を削ってから TTS へ渡すのではなく、Gemma が英語混じり文を日本語化できる
- `_warm_up_app()` で Gemma 正規化器も warm-up し、モデルロードと初回生成を起動時に前払いするようにした
- `irodori_mlx_stream` の秒数推定を x1 に戻した
- 実Gemma smoke で warm 後 `トモコ、today の meeting は 3pm からだよ。` が 163.2ms で `トモコ、今日の会議は午後三時からですよ。` になった
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 既存の `ReplyTextSanitizer` が TTS 前に英字を削っていた
  → 表示用は sanitizing を維持し、TTS 用 buffer だけ raw delta を保持して正規化器に渡す形にした

### 次のセッションでやること
- Chrome 実音声で、英語混じり LLM 返答が表示では日本語契約を守りつつ、TTS では自然な日本語音声になるか確認する
- 必要なら `ReplySpeechNormalizer` の検出条件を、実ログに出る混入パターンへ合わせて追加する

## 2026-05-24 セッション8

### やること（開始時に書く）
- irodori_mlx_stream の発話秒数を x1.2 にして品質寄りに調整する
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream のサンプルWAVを再出力する
- レイテンシーと出力先を記録する

### やったこと
- `irodori_mlx_stream` の `seconds` 推定に品質用スケール x1.2 を掛けるようにした
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream x1.2 のサンプルWAVを出力した
  - `logs/tts-normalized-irodori-asuka-x1_2/gemma-normalized-irodori-stream-asuka-x1_2.wav`
  - 正規化: 191.0ms
  - TTS first chunk: 432.4ms
  - TTS total: 1198.2ms
  - audio duration: 4.68秒
  - chunks: 3
- `_docs/latency.md` と `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- x1 はレイテンシーは良いが、asuka 参照音声では長め文の品質が厳しい
  → x1.2 で発話秒数に余裕を持たせる判断にした

### 次のセッションでやること
- x1.2 のWAVを試聴し、まだ厳しければ `num_steps` を6から8へ上げるか、stream unitをさらに短くする

## 2026-05-24 セッション9

### やること（開始時に書く）
- irodori_mlx_stream の発話秒数を x1.5 に上げる
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream のサンプルWAVを再出力する
- レイテンシーと出力先を記録する

### やったこと
- `irodori_mlx_stream` の品質用スケールを x1.2 から x1.5 へ上げた
- clamp 上限も x1.5 に合わせて 3.6秒へ戻した
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream x1.5 のサンプルWAVを出力した
  - `logs/tts-normalized-irodori-asuka-x1_5/gemma-normalized-irodori-stream-asuka-x1_5.wav`
  - 正規化: 191.7ms
  - TTS first chunk: 463.0ms
  - TTS total: 1264.6ms
  - audio duration: 5.88秒
  - chunks: 3
- `_docs/latency.md` と `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- x1.2 でも品質が厳しかった
  → 発話秒数の余裕をさらに増やし、まず x1.5 を default として試聴判断する

### 次のセッションでやること
- x1.5 のWAVを試聴し、まだ厳しければ duration ではなく `num_steps` や unit 分割幅を調整する

## 2026-05-24 セッション10

### やること（開始時に書く）
- Kokoro MLX stream に Gemma 日本語正規化を前段適用したサンプルWAVを出力する
- 英語混じり原文のKokoro出力と、正規化後Kokoro出力を比較できるように保存する
- レイテンシーと出力先を記録する

### やったこと
- 同じ英語混じり文で Kokoro MLX の raw 出力と Gemma 正規化後出力を作成した
  - raw: `logs/tts-normalized-kokoro-mlx/kokoro-mlx-raw-mixed.wav`
  - normalized: `logs/tts-normalized-kokoro-mlx/kokoro-mlx-gemma-normalized.wav`
  - summary: `logs/tts-normalized-kokoro-mlx/summary.json`
- Gemma 正規化結果:
  - `トモコ今日の会議は午後三時からだからスケジュールを確認して終わったらすぐに教えて`
- 実測:
  - Gemma 正規化: 188.6ms
  - raw Kokoro: first 293.8ms / total 293.9ms / audio 13.25秒 / 1 chunk
  - normalized Kokoro: first 142.8ms / total 142.9ms / audio 7.175秒 / 1 chunk
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- Kokoro の `generate_stream()` はこの長さの文でも 1 chunk だけ返した
  → 少なくとも今回の条件では、Kokoro は実質一括生成に近い挙動だった

### 次のセッションでやること
- WAVを試聴し、Kokoro + Gemma正規化が Irodori x1.5 より自然か判断する
- Kokoroを再候補にする場合は、長文を `ReplyAudioPlanner` 側でさらに短くflushするか確認する

## 2026-05-24 セッション11

### やること（開始時に書く）
- Kokoro + Gemma 正規化の文章崩れとレイテンシーを確認する
- 生成済みKokoro WAVをSTTで戻し、raw / normalized / per-flush の違いを見る
- 方針判断のための分析と提案をまとめる

### やったこと
- 生成済み `kokoro-mlx-raw-mixed.wav` と `kokoro-mlx-gemma-normalized.wav` を MLX Whisper で戻した
  - raw は英字をそのまま読もうとして崩れた
  - normalized は句読点なしの長い文になっており、内容も一部崩れて見えた
- 句読点付きの手動正規化文で Kokoro サンプルを追加生成した
  - `logs/tts-normalized-kokoro-mlx-analysis/kokoro-punctuated-full.wav`
  - `logs/tts-normalized-kokoro-mlx-analysis/kokoro-punctuated-split.wav`
- 実アプリに近い文ごと flush の Gemma 正規化 + Kokoro サンプルを生成した
  - `logs/tts-normalized-kokoro-mlx-analysis/kokoro-gemma-normalized-per-flush.wav`
  - first chunk 100.0ms / TTS total 178.7ms / 2 chunks
  - 正規化は1文目168.7ms、2文目103.4ms
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- Kokoro音質そのものより、Gemma正規化が句読点を落とすことでTTS用文章が壊れていた可能性が高い
  → Kokoroに戻すなら、句読点保持・補完を正規化プロンプトに入れる必要がある

### 次のセッションでやること
- 方針決定後、Kokoroをdefaultに戻すか、Kokoro専用のTTS正規化プロンプトとflushルールを実装する

## 2026-05-24 セッション12

### やること（開始時に書く）
- default TTS を Kokoro MLX に戻す
- Gemma TTS 正規化を句読点保持・文単位前提に修正する
- Kokoro + Gemma正規化の回帰テストとサンプル生成で確認する

### やったこと
- `config/central_realtime.toml` の default TTS を `kokoro_mlx` に戻した
- `ReplySpeechNormalizer` のプロンプトを Kokoro 向けに修正した
  - 入力の文数と順序を保つ
  - 複数文を一文へ結合しない
  - 句読点を保持・補完する
  - 文末に句点・疑問符・感嘆符を付ける
  - 一般英語はカタカナより自然な日本語訳を優先する
- モデルが文末句読点を落とした場合に、source の文末に合わせて補完する後処理を追加した
- `クイックに` のような不自然な読み上げ語を `すぐに` へ寄せる軽い後処理を追加した
- 採用版の Kokoro サンプルを出力した
  - `logs/tts-kokoro-gemma-adopted/kokoro-gemma-punctuation-preserved.wav`
  - 正規化後:
    - `トモコ、今日の会議は午後三時からだから、スケジュールを確認して。`
    - `終わったらすぐに教えて。`
  - 正規化: 211.1ms / 158.4ms
  - Kokoro first chunk: 112.8ms
  - Kokoro total: 166.6ms
  - chunks: 2
- `_docs/latency.md` と `MEMORY.md` に採用結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- プロンプトを強めても `quick` が `クイックに` になるケースが残った
  → TTS用途では不自然なので、限定的な後処理で `すぐに` へ寄せた

### 次のセッションでやること
- Chrome 実音声で Kokoro + Gemma句読点保持正規化の体感品質と初回音声タイミングを確認する

## 2026-05-24 セッション13

### やること（開始時に書く）
- Kokoro向けに読点/文節flushを実装しつつ、短すぎる `トモコ、` 単独flushを避ける
- 文節flush版のKokoro + Gemma正規化サンプルを再出力する
- レイテンシーと品質確認用WAVを記録する

### やったこと
- `ReplyAudioPlanner` に日本語読点の soft flush を追加した
- 10文字未満の短すぎる断片は soft flush しないようにした
  - `トモコ、` 単独TTSを避ける
  - ASCII comma は soft flush 対象から外した
- 回帰テストを追加した
  - `トモコ、` ではTTSしない
  - `トモコ、today の meeting は 3pm からだから、` まで来たらTTSする
  - 既存の英語混じりASCII commaケースは勝手にflushしない
- 文節flush版の Kokoro + Gemma サンプルを再出力した
  - `logs/tts-kokoro-gemma-bunsetsu-merged/kokoro-gemma-bunsetsu-merged.wav`
  - raw units:
    - `トモコ、today の meeting は 3pm からだから、`
    - `schedule を確認して。`
    - `終わったら quick に教えて。`
  - normalized:
    - `トモコ、今日の会議は午後三時からだから。`
    - `スケジュールを確認してください。`
    - `終わったらすぐに教えて。`
  - first chunk 75.6ms / TTS total 191.6ms / 3 chunks
- `_docs/latency.md` と `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 最初の実装は先頭の短い読点しか見ず、後続の読点まで待てなかった
  → 全文字を走査して、flush可能な読点だけ候補にするよう修正した
- ASCII comma でもflushして既存の英語混じりテストを壊した
  → soft flush は日本語読点だけに限定した

### 次のセッションでやること
- 文節flush版を試聴し、文体が丁寧語に寄りすぎる場合はGemma正規化プロンプトに「文体を変えない」をさらに強く入れる

## 2026-05-24 セッション14

### やること（開始時に書く）
- 会話LLMに「TTSでそのまま読める日本語」を指示した場合の追従率を測るベンチを追加する
- 現行プロンプトとTTS向け追加プロンプトで、英字混入率・Gemma正規化必要率・初回本文レイテンシーを比較する

### やったこと
- `_tools/bench_tts_ready_prompt.py` を追加した
  - 現行プロンプト、TTS読み上げ用追加プロンプト、few-shot付き追加プロンプトを同じ入力で比較する
  - `EMOTION:` ヘッダを除いた本文を評価する
  - Gemma正規化が必要な英字・時刻・非日本語混入と文末句読点を判定する
- 判定ロジックのユニットテストを追加した
- `qwen2.5:7b` で8入力のベンチを実行した
  - baseline: TTS ready 2/8、Gemma必要 6/8
  - TTS-ready prompt: TTS ready 0/8、Gemma必要 8/8
  - few-shot prompt: TTS ready 2/8、Gemma必要 6/8
  - 結果: `logs/tts-ready-prompt-bench-examples/summary.md`
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- 最初の判定では中国語の簡体字がCJK漢字として通ってしまった
  → ベンチ判定側に簡体字の軽い検出を追加した
- TTS-ready promptを強めても、`Zoom`、`GitHub Actions`、`LLM`、`TTS` などの入力コピーが残った
  → プロンプトだけでGemma層を外すのは危険と判断した

### 次のセッションでやること
- 上流LLMプロンプトへTTS向け日本語ルールを入れる場合も、Gemma正規化fallbackは維持する

## 2026-05-24 セッション15

### やること（開始時に書く）
- Gemma 4 E2B MLX をメイン会話推論候補として、TTS-ready追従率とレイテンシーを測る
- qwen2.5:7b の結果と同じベンチ条件で比較する

### やったこと
- `_tools/bench_tts_ready_prompt.py` に `--backend gemma_mlx` を追加した
  - `mlx-vlm` 経由で `mlx-community/gemma-4-e2b-it-4bit` を会話プロンプトに使う
  - ベンチ開始前に warm-up を走らせ、起動時ホット化後に近い値を測る
- Gemma 4 E2B MLX で8入力のTTS-readyベンチを実行した
  - baseline: TTS ready 8/8、Gemma必要 0/8、平均 first body 204.7ms
  - TTS-ready prompt: TTS ready 7/8、Gemma必要 1/8、平均 first body 312.1ms
  - few-shot prompt: TTS ready 8/8、Gemma必要 0/8、平均 first body 385.2ms
  - 結果: `logs/tts-ready-prompt-bench-gemma4-e2b-warm/summary.md`
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- 初回計測では最初の1件にモデルロードが乗り、baseline平均が歪んだ
  → Gemma backendにwarm-upを追加して測り直した
- TTS-ready追加プロンプトは追従率を上げる一方で出力が長くなり、first bodyが悪化した
  → Gemmaを会話LLMにするなら、まず短い現行persona寄りのプロンプトで試す判断

### 次のセッションでやること
- Gemmaを実アプリの会話backendにする場合は、`InferenceRouter` に MLX会話backendを追加して、実セッションでTTS正規化fallbackの発火率を見る

## 2026-05-24 セッション16

### やること（開始時に書く）
- メイン会話推論を Gemma 4 E2B MLX に切り替える
- Kokoro は sentence flush のみで流し、読点による文節flushとGemma TTS正規化を無効にする
- 起動時warm-upと実セッションで使うbackendを共有し、レイテンシーログを追加する

### やったこと
- `GemmaMLXBackend` を追加し、`InferenceRouter` の `gemma_mlx` backend として使えるようにした
- `config/central_realtime.toml` の `conversation_backend` を `local_gemma4_e2b_mlx` に切り替えた
  - fallback は既存の `local_qwen7b`
  - `tts_backend` は `kokoro_mlx`
  - `speech_normalizer_enabled = false`
- FastAPI startup warm-up で conversation backend も温めるようにした
- `_create_default_router()` を `app.state._default_router` にキャッシュし、warm-up済み Gemma backend をWebSocketセッションでも再利用するようにした
- `ReplyAudioPlanner` は読点soft flushをやめ、`。！？` の sentence flush だけに戻した
- `TomoroSession` にレイテンシーログを追加した
  - `speech_end`
  - `transcript` / STT elapsed
  - `reply_start`
  - `first_reply_text`
  - `tts_start`
  - `first_audio_chunk`
- `logs/gemma-kokoro-warmup-smoke.log` で実 warm-up を確認した
  - STT warm-up: 1356.5ms
  - Kokoro warm-up: 277.8ms
  - Gemma conversation warm-up: 3751.6ms
  - TTS text normalizer は disabled で skip
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 最初は Gemma生成を `asyncio.to_thread` に逃がしたが、`mlx-vlm` が worker thread 上で `There is no Stream(gpu, 2)` を出して落ちた
  → Gemma会話backendでは `mlx-vlm.stream_generate()` を同じスレッドで同期消費する形にした
- 起動時にGemmaを温めても、routerを毎回作るとWebSocketセッションで別インスタンスが作られてcold startに戻る
  → default routerをキャッシュして同じbackendインスタンスを再利用するようにした

### 次のセッションでやること
- Chrome 実セッションで `TomoroSession latency first_audio_chunk` の `speech_end_to_first_audio_ms` を確認する
- Gemma同期生成がマイク入力処理に体感影響を出す場合は、`mlx-vlm` の専用生成スレッド/stream初期化方式を追加調査する

## 2026-05-24 セッション17

### やること（開始時に書く）
- LM Studio の OpenAI互換 streaming API を会話backendとして追加する
- メイン会話推論を内蔵 `mlx-vlm` Gemma から LM Studio の `gemma-4-e2b-it-mlx` に切り替える
- 起動時 warm-up と unit test でストリーミングSSE処理を固定する

### やったこと
- `LMStudioBackend` を追加した
  - OpenAI互換 `/v1/chat/completions` の `stream:true` SSE を読み、`delta.content` をそのまま `chat_stream()` で流す
  - base URL が `/v1` あり/なしのどちらでも動くようにした
  - 起動時 `warm_up()` で短い日本語応答を一度流してホット化する
- `InferenceRouter` に `lm_studio` backend type を追加した
- `config/central_realtime.toml` を LM Studio 採用構成に切り替えた
  - `conversation_backend = "lmstudio_gemma4_e2b"`
  - `conversation_fallback = "local_gemma4_e2b_mlx"`
  - URL は `http://192.168.11.66:1234`
  - model は `gemma-4-e2b-it-mlx`
- unit test を追加/更新した
  - SSE parser
  - LM Studio endpoint URL 組み立て
  - backend streaming payload
  - config/router の default backend
- `logs/lmstudio-kokoro-warmup-smoke.log` で実 warm-up を確認した
  - STT warm-up: 1854.2ms
  - Kokoro warm-up: 279.8ms
  - LM Studio conversation warm-up: 243.0ms
  - 依存同期後の再実行でも LM Studio conversation warm-up は 257.1ms で通過
  - TTS text normalizer は disabled で skip
- `httpx` と `mlx-whisper` を default dependency に明示した
  - `httpx` は LM Studio backend が直接使うため
  - `mlx-whisper` は default config の `local_whisper_mlx_small` が使うため
- `_docs/latency.md` と `MEMORY.md` に採用判断と実測値を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 通常サンドボックスでは `uv` cache と LAN の LM Studio API に触れず warm-up 確認が失敗した
  → 権限昇格で実機 warm-up を実行して確認した
- LM Studio の URL は `/v1` なしで渡される想定だが、将来 `/v1` 付きに変えても二重にならないよう helper で吸収した

### 次のセッションでやること
- Chrome 実セッションで `TomoroSession latency first_audio_chunk` の `speech_end_to_first_audio_ms` を確認する
- LM Studio 側でロード済みモデルが変わった場合に、設定の `model` と実際の応答 `model` がズレないかログで見る

## 2026-05-24 セッション21

### やること（開始時に書く）
- M2 に会話セッション境界の新 Phase を追記する
- `attention_mode` と `conversation_sessions` の関係、短期文脈の読み出し順を PLAN.md に明文化する

### やったこと
- `PLAN.md` の M2 Phase 8 と M2 完了条件の間に Phase 8.5「会話セッション境界」を追記した
- `ambient -> engaged` または最初の `should_participate=True` で session を開始し、`cooldown -> ambient` / `withdrawn` で閉じる方針を明文化した
- 短期文脈は同一 session の completed turn を優先し、足りない場合だけ最近の completed turn で補う方針を追記した

### 詰まったこと・解決したこと
- 既存 Phase 7/8 の記憶実装は否定せず、会話境界がない点だけを補う Phase として追加した

### 次のセッションでやること
- Phase 8.5 を実装する場合は、DDL と unit test を先に書いてから `TomoroSession` / `ConversationLogWriter` を更新する

## 2026-05-24 セッション22

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、ARCHITECTURE.md / AGENTS.md / PLAN.md を整理する
- `conversation_sessions` に会話境界・要約・要約 embedding を集約する設計へ更新する
- 要約と embedding 生成をオンライン経路から外し、別プロセスで pending session を処理する Phase を追加する

### やったこと
- `AGENTS.md` に会話セッション境界とセッション要約索引の実装規約を追加した
- `ARCHITECTURE.md` に `conversation_sessions`、`session_summarizer`、session summary search の設計を追記した
- `PLAN.md` の Phase 8.5 を `conversation_sessions` 一本で summary / embedding を持つ設計へ修正した
- `PLAN.md` に Phase 8.6「セッション要約索引」を追加し、pending session を別プロセスで要約・embedding 化する流れを定義した
- `MEMORY.md` に、会話セッション要約は `conversation_sessions` に集約し、オンライン経路から外す判断を追記した

### 詰まったこと・解決したこと
- 要約 embedding を別テーブルに分ける案は、現時点では管理対象を増やす割に利点が薄い
  → 複数モデルや複数要約種別が必要になるまで `conversation_sessions.summary_embedding` に一本化する

### 次のセッションでやること
- Phase 8.5 実装時は DDL と unit test を先に書き、session 開始/終了と `conversation_logs.conversation_session_id` の保存を固定する
- Phase 8.6 実装時は online `TomoroSession` 経路で summarizer が呼ばれないことをテストで保証する

## 2026-05-24 セッション23

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、用語集ログと人格スナップショットの versioned JSONB 設計を追記する
- PostgreSQL `jsonb` とプログラム側モデルクラスで、分析しやすく型も扱いやすい構成にする方針を文書化する

### やったこと
- `ARCHITECTURE.md` に `persona_lexicon_versions` / `persona_state_versions` の versioned JSONB snapshot 設計を追記した
- `lexicon_json` / `state_json` / `diff_json` の役割と JSON 形状例を追加した
- PostgreSQL `jsonb` / jsonpath / GIN index を外部分析に使い、アプリケーションではモデルクラスに変換して扱う方針を明文化した
- `AGENTS.md` に JSONB snapshot の実装規約を追加した
- `PLAN.md` に Phase 8.7「用語集ログと人格スナップショット」を追加し、M5 Phase 17 を DB の versioned snapshot 前提に補正した
- `MEMORY.md` に確定した判断として追記した

### 詰まったこと・解決したこと
- 用語や人格状態を細かい正規化テーブルで持つ案もあるが、変動点トレースと外部分析を優先し、まずは JSONB snapshot + diff に寄せる
- 生 JSON をプログラム中で持ち回ると境界が曖昧になるため、DB 入出力時に schema version 付きモデルクラスへ変換する規約にした

### 次のセッションでやること
- Phase 8.7 実装時は JSONB DDL、モデルクラス、round-trip test、jsonb query test を先に書く

## 2026-05-24 セッション24

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、`ContextSnapshotBuilder` を設計に盛り込む
- LLM に渡す文脈取得を一箇所に集約し、depth と perf benchmark でレイテンシー管理できる Phase を追加する

### やったこと
- `AGENTS.md` に `ContextSnapshotBuilder` の実装規約を追加した
- `ARCHITECTURE.md` に `ContextSnapshotBuilder` の責務、DTO、depth、初段 fallback、perf 目標を追記した
- `PLAN.md` に Phase 8.8「ContextSnapshotBuilder 初段」を追加した
- `MEMORY.md` に LLM 文脈取得は builder に集約し、depth ごとの latency を固定して監視する判断を追記した

### 詰まったこと・解決したこと
- すべての記憶系機能が実装されるまで待つと抽象化が遅すぎる
  → 初段は未実装 source を空 list / None で返し、既存 recent turns と Phase 8 memory search から段階的に移行する

### 次のセッションでやること
- Phase 8.8 実装時は `TomokoContextSnapshot` DTO、builder unit test、perf test を先に追加する

## 2026-05-24 セッション25

### やること（開始時に書く）
- 別LLMからの ContextSnapshotBuilder hardening 指摘を確認し、妥当な内容を設計へ反映する
- `ContextBuildPolicy` / `ContextBuildTrace` / parallel DB I/O / degraded context / TTL cache の方針を Markdown に追記する

### やったこと
- 指摘内容はプロジェクトのレイテンシー管理方針と整合しているため、設計へ取り込んだ
- `ARCHITECTURE.md` の ContextSnapshotBuilder 節に best-effort runtime、parallel DB I/O、`ContextBuildPolicy`、`ContextBuildTrace`、process-local TTL cache、TomoroSession 管制塔方針を追記した
- `PLAN.md` の Phase 8.8 を `ContextBuildPolicy` / `ContextBuildTrace` / timeout degraded context / parallel source / cache trace 前提に拡張した
- `PLAN.md` に Phase 8.8.1「ContextSnapshotBuilder 運用 hardening」を追加した
- `AGENTS.md` に context build の timeout / degraded / trace / TTL cache 規約を追加した
- `MEMORY.md` に確定判断と気づきを追記した

### 詰まったこと・解決したこと
- context source を増やすほど待ち時間も増える設計は危険
  → source を parallel DB I/O として読み、deadline で打ち切る best-effort runtime とする
- cache を入れると状態の真実が分散しやすい
  → process-local TTL cache は DB read の speed-up に限定し、authoritative state は cache しない方針にした

### 次のセッションでやること
- Phase 8.8 実装時は `ContextBuildPolicy` / `ContextBuildTrace` を DTO として先に作り、timeout/degraded path の unit test を最初に書く

### 外部LLMとの会話原文
[会話原文](_reference/2026-05-24-1200_設計評価と改善提案.md)
