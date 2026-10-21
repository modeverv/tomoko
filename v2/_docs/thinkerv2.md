# thinker2 / thinker v2 implementation plan

## 目的

Tomoko に「外界と人間の状態を薄く見続け、必要な時だけ自然に話す」ための background cognition worker を追加する。

既存の turn-taking / stop / barge-in 系 shadow worker は、会話 hot path のすぐ横で低遅延 advisory を返す役割に限定する。thinker2 はそれとは分け、カレンダー、タイマー、world information、カメラ、スクリーンショットを観測して、DB state と `utterance_candidates` / `arrival_candidates` を作る。

## 境界

### thinker2 が持つ

- deterministic source から candidate を作る
  - Google Calendar
  - ユーザー登録 timer / alarm / schedule
  - deterministic reminder
  - world information の探索テーマ seed
- perception source を観測して DB に保存する
  - camera frame
  - screenshot frame
  - human presence
  - human activity
  - screen activity
- perception / calendar / world information を合成して短い context snapshot を作る
- snapshot から必要な candidate を作る
- stale frame / stale inference は捨てる
- worker backlog / latency / error をログに残す

### thinker2 が持たない

- `/ws` hot path
- TomoroSession の authoritative state
- candidate final gate
- playback / interrupt / stop / barge-in 判定
- client-side retry / state decision
- raw image を prompt に直接混ぜる online conversation path

最終的に話すかどうかは、既存どおり TomoroSession / candidate speak policy が決める。

## 基本データフロー

```text
calendar / timer / world / camera / screenshot
  -> thinker2 source workers
  -> raw frame store / observation tables
  -> latest state / context snapshot
  -> candidate generation
  -> utterance_candidates / arrival_candidates
  -> TomoroSession final gate
```

`candidate` は「Tomoko が言いたいこと」。
`observation` / `state` / `snapshot` は「今言ってよいか、何を踏まえるか」の材料。

## モデル方針

初期候補は local Gemma E12B class の multimodal / VLM backend を thinker2 内蔵モデルとして使う。

ただし、存在判定は頻度が高く単純なので、将来小規模 classifier へ差し替えられる境界にする。

- 推論1: camera image -> `human_present: true | false`
- 推論2: camera image -> `human_activity_label`
- screen 推論: screenshot -> `screen_activity_label`
- 推論3: presence + activity + screen -> `user_activity_summary`
- 推論4: camera + calendar + screenshot + world -> `context_summary`

各推論は JSON-only の短い schema に固定し、自由文で runtime を操作させない。

## 保存ポリシー

カメラ画像とスクリーンショットは 30 秒に 1 回保存し、それぞれ最新 100 件だけ保持する。

raw frame は短命 artifact として扱い、会話 prompt へ直接入れない。DB には frame path / hash / captured_at と、推論済み observation を保存する。

## DB schema 

### perception_frames

camera / screenshot の raw artifact 管理。

- `id UUID PRIMARY KEY`
- `source TEXT NOT NULL`
  - `camera`
  - `screenshot`
- `device_id TEXT`
- `captured_at TIMESTAMPTZ NOT NULL`
- `file_path TEXT NOT NULL`
- `sha256 TEXT NOT NULL`
- `width INTEGER`
- `height INTEGER`
- `retained BOOLEAN NOT NULL DEFAULT true`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

retention worker は source ごとに最新 100 件を残し、それ以前を削除または `retained=false` にする。

### human_presence_observations

camera frame から人間がいるかだけを 2 値で保存する。

- `id UUID PRIMARY KEY`
- `frame_id UUID REFERENCES perception_frames(id)`
- `observed_at TIMESTAMPTZ NOT NULL`
- `present BOOLEAN NOT NULL`
- `confidence DOUBLE PRECISION NOT NULL`
- `model TEXT NOT NULL`
- `raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

### human_activity_observations

camera frame から人間が何をしているかを一言で保存する。

- `id UUID PRIMARY KEY`
- `frame_id UUID REFERENCES perception_frames(id)`
- `presence_observation_id UUID REFERENCES human_presence_observations(id)`
- `observed_at TIMESTAMPTZ NOT NULL`
- `activity_label TEXT NOT NULL`
- `confidence DOUBLE PRECISION NOT NULL`
- `model TEXT NOT NULL`
- `raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

`present=false` の時に `activity_label="ギターを弾いている"` のような矛盾が出た場合、row は保存してよい。ただし latest 合成では `coherent_activity_label=NULL` または `away` に丸める。

### screen_activity_observations

screenshot から画面上の活動を一言で保存する。

- `id UUID PRIMARY KEY`
- `frame_id UUID REFERENCES perception_frames(id)`
- `observed_at TIMESTAMPTZ NOT NULL`
- `screen_activity_label TEXT NOT NULL`
- `app_hint TEXT`
- `document_hint TEXT`
- `url_hint TEXT`
- `confidence DOUBLE PRECISION NOT NULL`
- `model TEXT NOT NULL`
- `raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

### user_context_snapshots

camera / screenshot / calendar / world information を合成した短い状態 snapshot。

- `id UUID PRIMARY KEY`
- `computed_at TIMESTAMPTZ NOT NULL`
- `device_id TEXT`
- `present BOOLEAN`
- `presence_observed_at TIMESTAMPTZ`
- `activity_label TEXT`
- `activity_observed_at TIMESTAMPTZ`
- `screen_activity_label TEXT`
- `screen_observed_at TIMESTAMPTZ`
- `calendar_summary TEXT`
- `world_summary TEXT`
- `user_activity_summary TEXT NOT NULL`
- `context_summary TEXT NOT NULL`
- `interaction_readiness TEXT NOT NULL`
  - `away`
  - `do_not_disturb`
  - `low_intrusion_ok`
  - `chat_ok`
  - `needs_help_maybe`
- `confidence DOUBLE PRECISION NOT NULL`
- `source_frame_ids UUID[] NOT NULL DEFAULT '{}'`
- `source_observation_ids UUID[] NOT NULL DEFAULT '{}'`
- `model TEXT`
- `raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

## Candidate source 案

### calendar_reminder

予定データがなければ `make gcal` 相当の import を起動し、`calendar_events` を更新する。

予定開始前の deterministic window で candidate を積む。

- 15 分前: 通知 candidate
- 5 分前: urgent candidate
- 開始時刻: due candidate

LLM は使わない。candidate text は template で作る。

### timer_alarm_reminder

既存 timer / alarm row から、指定時刻に candidate または due notification を作る。

ユーザー指定の timer / alarm は通常の自発発話より優先度を高くする。ただし、既存の timer / alarm due 制御を壊さない。

### world_information_digest

既存 world information / world observation 機構を使い、最初は deterministic topic seed で掘る。

例:

- 今日の大きなニュース
- AI / local model / speech interface
- Apple / macOS / MLX
- ユーザーが最近話していた topic

収集結果は raw artifact -> normalizer -> interpretation -> candidate の既存流れへ寄せる。

### screen_context_candidate

screen activity から、作業補助や軽い声かけ candidate を作る。

例:

- `pytest` failure を長く見ている -> 「テストの落ち方、一緒に切り分ける？」
- 同じドキュメントを長く読んでいる -> 「そこ、要約してみようか？」
- 動画視聴中 -> 原則 candidate は抑制、必要なら低 intrusion

### activity_context_candidate

camera activity と presence から candidate を作る。

例:

- `present=false` -> candidate は溜めるが発話しない
- `present=true` かつ `returned` -> arrival candidate
- `present=true` かつ `ギターを弾いている` -> 邪魔しない方向に gate
- `present=true` かつ `idle` / `困っている可能性` -> low intrusion candidate

## thinker2 worker 構成

### Capture workers

- `camera_capture_loop`
  - 30 秒に 1 回 camera frame を保存
  - 最新 100 件 retention
- `screenshot_capture_loop`
  - 30 秒に 1 回 screenshot を保存
  - 最新 100 件 retention

### Perception workers

- `presence_inference_loop`
  - 未処理 camera frame を古い順に処理
  - `human_presence_observations` に保存
- `human_activity_inference_loop`
  - presence と同じ camera frame を処理
  - `human_activity_observations` に保存
- `screen_activity_inference_loop`
  - 未処理 screenshot frame を処理
  - `screen_activity_observations` に保存

stale frame は処理しない。backlog がある場合は最新 frame を優先し、古い未処理 frame は skipped としてログに残す。

### Synthesis workers

- `user_context_snapshot_loop`
  - latest presence / activity / screen / calendar / world を合成
  - `user_context_snapshots` に保存
- `candidate_generation_loop`
  - deterministic reminder candidate
  - world information candidate
  - screen / activity context candidate
  - existing `utterance_candidates` / `arrival_candidates` store に保存

## 実装フェーズ

### Phase T2.0: document and boundary

- [x] `thinkerv2.md` に設計と実装計画を書く
- [x] PLAN.md へ実装 phase と完了条件を追記する
- [x] MEMORY.md へ確定した判断を追記する

### Phase T2.1: deterministic candidate sources

- [x] calendar reminder source を追加する
- [x] timer / alarm reminder source との境界を確認する
- [x] deterministic candidate text template を固定する
- [x] `pytest -m unit` で source / dedupe / available_at / expires_at を検証する

### Phase T2.2: perception frame store

- [x] `perception_frames` DDL を追加する
- [x] frame store DTO / InMemory / PostgreSQL store を追加する
- [x] camera / screenshot の retention 100 件を実装する
- [x] unit / integration test を追加する

### Phase T2.3: camera presence

- [x] camera capture を 30 秒ごとに保存する
- [x] presence JSON schema を固定する
- [x] Gemma E12B class backend で `present` / `confidence` を返す
- [x] `human_presence_observations` に保存する
- [x] stale frame discard と backlog skip をテストする

### Phase T2.4: camera activity

- [x] activity JSON schema を固定する
- [x] `activity_label` を一言で返す
- [x] `present=false` と activity label の矛盾を latest 合成で丸める
- [x] `human_activity_observations` に保存する

### Phase T2.5: screenshot activity

- [x] screenshot capture を 30 秒ごとに保存する
- [x] screen activity JSON schema を固定する
- [x] `app_hint` / `document_hint` / `url_hint` を optional にする
- [x] `screen_activity_observations` に保存する

### Phase T2.6: user context snapshot

- [x] latest presence / activity / screen / calendar / world を読む
- [x] `user_context_snapshots` を保存する
- [x] `interaction_readiness` を deterministic rule + optional LLM synthesis で決める
- [x] `present=false` の時は activity を `away` 相当に丸める
- [x] snapshot generation の elapsed / skipped source をログに残す

### Phase T2.7: context-derived candidates

- [x] screen context candidate source を追加する
- [x] activity context candidate source を追加する
- [x] `interaction_readiness` に応じて priority / urgency / intrusion を調整する
- [x] candidate は既存 `utterance_candidates` / `arrival_candidates` に保存する
- [x] candidate final gate は TomoroSession から動かさない

### Phase T2.8: world information autonomous collection

- [x] deterministic topic seed を固定する
- [x] 既存 world observation operator 経由で収集する
- [x] raw artifact / normalizer / interpretation / candidate の既存境界を使う
- [x] low confidence / outdated / sensitive は candidate にしない

### Phase T2.9: runtime integration and inspection

- [x] `background-process/run_thinker2.py` を追加する
- [x] `make thinker2` / `make thinker2-once` を追加する
- [x] queue depth / inference latency / skipped stale frame / candidate count をログに出す
- [x] offline replay / inspection HTML を作り、live runtime 接続前に挙動を確認する

## テスト方針

- unit
  - DTO round-trip
  - retention 100 件
  - stale frame discard
  - presence/activity/screen schema validation
  - deterministic reminder candidate timing
  - snapshot synthesis rule
  - candidate dedupe
- integration
  - PostgreSQL DDL / store round-trip
  - `thinker2-once` が local DB に observations / snapshots / candidates を保存する
- perf
  - capture なしの source processing
  - image inference latency
  - snapshot synthesis latency

online `/ws` の latency を壊さないことを完了条件に含める。

## 初期完了条件

- calendar / timer / world / camera / screenshot の各 source が thinker2 で独立に動く
- raw image は短命 artifact として保持され、DB には structured observation が残る
- `user_context_snapshots` から「人間がいるか」「何をしているか」「今話してよい温度」を読める
- context-derived candidate が既存 candidate pool に積まれる
- TomoroSession の final gate / `/ws` hot path / shadow worker の責務を変更しない
- `pytest -m unit` が通る
