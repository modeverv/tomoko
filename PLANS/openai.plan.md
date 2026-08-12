# openai.plan.md

## 文書の目的

この文書は、`openai.md` に記載された提案を Tomoko v2 へ段階的に実装・検証するための、**実行手順を固定した補助 PLAN** である。

主な利用者は、Luna の軽量モデルを含むコーディングエージェントである。軽量モデルでも制御境界を推測せずに作業できるよう、次を明示する。

- 1 回の作業で扱う責務
- 変更を許可する範囲
- 変更してはいけない範囲
- 最初に書くテスト
- 実装順序
- 検証コマンド
- artifact の形式
- 人間が決める項目
- 迷った場合の停止条件
- rollback 条件

この文書は既存の `PLAN.md` を置き換えない。各 Phase を開始するときは、現在の `LOG.md` / `MEMORY.md` / `PLAN.md` / `ARCHITECTURE.md` を読み、対象 Phase だけを既存 `PLAN.md` へ追記してから着手する。

---

## 0. 現在の実行状態

```text
対象 runtime: Tomoko v2 root
前提: openai.md が分析対象とした Phase S23 完了時点
現在 unlock されている作業: O0A-00 のみ
次 Phase へ自動で進むこと: 禁止
実装 commit: 人間の確認があるまで禁止
push: 明示指示がない限り禁止
```

### 固定ルール

- エージェントは **1 セッションにつき 1 Task ID だけ**扱う。
- Task ID の完了後に、次 Task ID へ勝手に進まない。
- 前 Task の検証結果と artifact が存在しない場合、次 Task を開始しない。
- 人間 decision gate が `UNDECIDED` のままなら、後続 Phase を開始しない。
- 実装中に別の不具合を見つけても、今回の Task に含まれなければ修正しない。`LOG.md` へ記録して止める。
- 「ついでの rename」「ついでの共通化」「ついでの format」「ついでの依存更新」をしない。
- 既存テストを削除・skip・xfail 化して通したことにしない。
- 期待する機能が未実装で probe が FAIL した場合でも、通常 suite に既知の赤テストを残さない。診断 artifact として保存し、修正 Phase の開始時に通常 red test へ昇格する。

---

## 1. Source of Truth と衝突時の扱い

作業開始時の優先順は次の通り。

1. 現在の `LOG.md` にある実行履歴と直近の完了状態
2. 現在の `MEMORY.md` にある確定判断・地雷・未解決疑問
3. 現在の `PLAN.md` にある active Phase・禁止事項・完了条件
4. 現在の `ARCHITECTURE.md` にある責務境界
5. 現在の `AGENTS.md` にある作業規則
6. この `openai.plan.md`
7. `openai.md` の提案・背景説明

### v1 / v2 の混同禁止

この文書は **root の Tomoko v2** を対象にする。

次の条件を一つでも満たさない場合は、コード変更せず人間へ委譲する。

- `server/hot_path/` が存在する
- `server/tomoko/` が存在する
- internal WebSocket control の実装が存在する
- `SpeechOrder` 相当の契約が存在する
- `TurnMaterials` / `LlmFireGate` / `SpeechEmissionGate` 相当の実装が存在する

次は本作業の対象外である。

- `v1/` 配下
- monolithic `server/session.py` の closed-loop 再構成
- v1 の `TomoroSession` package split
- v1 の cooldown / engaged / ambient state machine の移植
- v1 の DB schema への後方移植

`ARCHITECTURE.md` と実コードの名称が異なる場合、名称を推測して置換しない。O0A-00 で symbol を一意に特定し、repository map に記録する。

---

## 2. 絶対に壊してはいけないアーキテクチャ境界

### 2.1 外部通信

- browser 向け public endpoint は既存 `/ws` 一本を維持する。
- REST/RPC endpoint を追加しない。
- playback telemetry は既存 `/ws` の JSON event として返す。
- browser は観測事実だけを返す。発話判断、retry、stale 判定、queue policy、session owner を持たない。

### 2.2 hot-path-process の所有物

hot-path-process は Tomoko の物理インターフェースである。所有してよいものは次だけ。

- mic bytes / VAD / STT の短命状態
- STT observation の送出
- 現在実行中の speech order
- append queue
- TTS task / TTS chunk queue
- playback generation
- replace / append / stop / fade / short silence の物理制御
- audio chunk の順序付き送信
- fixed backchannel の低レイテンシ実行

hot-path-process に追加してはいけないもの。

- 発話してよいかの人格判断
- 自発発話判断
- candidate 採用判断
- memory / long context
- conversation policy
- motivation / relationship state
- LLM prompt 組み立て

### 2.3 tomoko-process の所有物

- partial / final STT observation の解釈
- Materials -> Pressures -> Gates
- `LlmFireGate`
- `SpeechEmissionGate`
- motivation / personality / relationship / memory / context
- 発話内容の決定
- main conversation LLM
- speech queue / priority / replace / append / stop の判断
- decision generation の所有
- stale candidate / supersede / deadline の判断

### 2.4 PostgreSQL の役割

- canonical conversation record
- memory
- audit / replay
- scheduler decision
- prompt request / model output summary
- final research result
- versioned context snapshot

PostgreSQL を short-cycle audio control RPC に戻さない。internal WebSocket が realtime control の origin であり、DB は durable record である。

### 2.5 audio hot loop

- 8 ms 前後の audio hot loop は primitive のまま維持する。
- hot loop 内で cross-layer DTO、JSON、DB write、構造化 log、大量 timestamp object を生成しない。
- raw VAP frame を DB や internal WebSocket へ流さない。
- 境界を渡すのは平滑化済み `TurnMaterials` 相当だけにする。

### 2.6 今回採用しないもの

- GPT-Live API への置換
- native speech-to-speech への全面置換
- VAD / VAP / MaAI / Materials / Pressures / Gates の撤去
- localhost のための WebRTC / WARP 実装
- 計測前の Python -> Go 移行
- client 側 state machine
- public endpoint 追加
- 単一マシンでの 26B model 二重常駐 handoff
- speculative/live view を canonical DB record にすること
- acknowledgement を semantic answer latency として報告すること
- 汎用 EventBus / 外部 message broker の導入
- GPU 不足の実測なしに inference queue platform を導入すること

---

## 3. 軽量エージェント向け作業プロトコル

各 Task は必ず次の順で行う。

### Step A: 読む

```bash
cat MEMORY.md
cat LOG.md
cat PLAN.md
cat ARCHITECTURE.md
cat AGENTS.md
cat openai.md
cat openai.plan.md
```

長い文書を全文保持できない場合でも、最低限次を `rg` で再確認する。

```bash
rg -n "Phase S23|Phase O0|openai|preemption|response_kind|SpeechOrder|TurnMaterials|LlmFireGate|SpeechEmissionGate" PLAN.md LOG.md MEMORY.md ARCHITECTURE.md openai.md openai.plan.md
```

### Step B: clean state を確認する

```bash
git status --short --branch
git rev-parse HEAD
```

作業開始前から未コミット差分がある場合、その差分を消さない。今回 Task と競合する場合は停止する。

### Step C: LOG に開始記録を追記する

```markdown
## YYYY-MM-DD OpenAI Plan Task <TASK-ID>

### やること
- Task ID:
- 対象責務:
- 変更許可ファイル:
- 変更禁止領域:
- 完了条件:
```

### Step D: characterization / red test を先に置く

- 先に現状を固定できる場合は characterization test を書く。
- 新契約の場合は red test を書く。
- red test の failure reason が想定どおりであることを確認する。
- 同じ Task 内で実装し、終了時には通常 suite を green に戻す。

### Step E: 最小実装

- allowlist 外のファイルを編集しない。
- 既存責務 owner を移動しない。
- 同じ意味の二つ目の DTO / enum / state holder を作らない。
- module-level mutable state を作らない。
- 大きい `realtime.py` / `conversation.py` に新責務を直接積まず、小さい dedicated module を使う。ただし既存同等 module があるなら重複新設しない。

### Step F: 検証

最低限、Task 固有 test、full unit、ruff、diff check を行う。

```bash
<targeted test command>
<full unit command>
<ruff command>
git diff --check
```

DB / WS / schema 変更なら integration、latency 変更なら perf/e2e、browser event 変更なら実 browser check を追加する。

### Step G: 報告して止まる

Task 完了後は次の Task に進まず、固定フォーマットで報告する。

```text
Task ID:

変更内容:
- ...

変更していないもの:
- runtime behavior（該当する場合）
- audio hot loop
- process ownership
- public /ws contract（変更した event 以外）
- DB ordering（該当する場合）
- ...

検証:
- targeted test:
- full unit:
- integration:
- perf/e2e:
- ruff:
- git diff --check:

artifact:
- ...

人間確認が必要なこと:
- ...
```

---

## 4. 固定用語とデータ契約

以下は実装時の意味を固定する。実コードの model framework は既存方式に合わせる。Pydantic を dataclass に変える、dataclass を Pydantic に変える、といった変更はしない。

### 4.1 ResponseKind

```text
backchannel:
  MaAI 等による固定相づち。本文回答ではない。

acknowledgement:
  依頼を受け取ったこと、少し待つこと、処理中であることを示す短い feedback。
  本文回答ではない。

content:
  通常回答、自発発話、calendar notice など、意味内容を持つ substantive speech。

correction:
  partial-origin の発話を final/new observation により訂正・言い直す speech。

followup:
  sense / research / tool / background delegation の結果を後から返す speech。
```

固定 mapping:

| 発話源 | response_kind |
|---|---|
| MaAI fixed backchannel | `backchannel` |
| 「うん」「少し待ってね」等の受領・待機 | `acknowledgement` |
| main user reply | `content` |
| initiative / calendar notice | `content` |
| final reconcile による言い直し | `correction` |
| sense / research result | `followup` |
| `mode=stop` | `None` を許可。音声本文を持たない |

固定 invariant:

- `mode != stop` かつ `text` が非空の `SpeechOrder` は `response_kind` 必須。
- `response_kind` を後段 LLM で分類しない。
- metric 集計時に text の内容で推測しない。

### 4.2 ID と owner

| ID | owner | 用途 | 禁止 |
|---|---|---|---|
| `origin_trace_id` | 最初の observation 作成側 | STT observation から followup までの因果系列 | process ごとに別 ID を再発行しない |
| `decision_generation_id` | tomoko-process | どの判断世代から candidate/order が生まれたか | hot-path が採番しない |
| `order_id` | tomoko-process | speech order identity | audio chunk ごとに変えない |
| `playback_generation_id` | hot-path | TTS/audio 実行世代 | tomoko-process が採番しない |
| `utterance_id` | observation/canonical owner | 同一 user utterance の partial/final identity | partial ごとに変えない |
| `revision` | observation owner | live view 更新順 | final 後に過去 revision へ戻さない |
| `client_event_seq` | browser connection | playback observation の接続内順序 | server decision generation と混ぜない |

`trace_id` が既に `origin_trace_id` と同じ意味を持つ場合は再利用する。意味が違う場合だけ新 field を追加する。

### 4.3 SpeechOrder の論理 contract

既存 field を削除せず、必要な field だけ段階追加する。

```text
SpeechOrder:
  id / order_id
  text
  mode: replace_current | append_after_current | stop
  response_kind: ResponseKind | None
  origin_trace_id
  decision_generation_id
  created_at
  deadline_at: optional
  supersedes_order_id: optional
  reason: optional
  priority: optional
```

`playback_generation_id` は order を hot-path が受理した時に hot-path 側 execution state へ付与する。Tomoko から送る order に hot-path の future generation を埋めない。

### 4.4 PlaybackObservation の論理 contract

browser は判断せず、観測事実だけを送る。

```text
PlaybackObservation:
  type: playback_started | playback_ended | playback_buffered
  order_id
  playback_generation_id
  client_event_seq
  audio_context_time_sec: optional
  buffered_until_audio_context_sec: optional
  performance_now_ms: optional
```

server は process 間の monotonic clock を直接引き算しない。latency 集計では server が event を受信した時刻を server monotonic clock で記録する。

### 4.5 Milestone の固定名

```text
observation_received
stt_partial_ready
stt_final_ready
llm_job_started
llm_first_token
llm_first_sentence
speech_order_created
speech_order_sent
speech_order_received
playback_generation_started
first_tts_chunk_ready
first_audio_chunk_queued
first_audio_chunk_sent
client_playback_started_received
client_playback_ended_received
candidate_invalidated
tts_task_cancel_requested
tts_task_cancelled
playback_generation_invalidated
canonical_persistence_enqueued
canonical_persistence_completed
```

既存 log 名がある場合は既存名を維持し、上記 canonical name との mapping を artifact に記録する。

### 4.6 latency metric の固定定義

#### first feedback

```text
first_feedback_server_ms:
  origin observation を server が受信
  -> response_kind in {backchannel, acknowledgement, content, correction, followup}
     の最初の audio chunk を server writer が送信

first_feedback_client_ms:
  origin observation を server が受信
  -> 該当 order の playback_started を server が受信
```

canonical report の `first_feedback_ms` は client telemetry がある場合だけ `first_feedback_client_ms` を使う。telemetry がない場合は `null` とし、server 値で代用しない。

#### first content

```text
first_content_server_ms:
  origin observation を server が受信
  -> response_kind in {content, correction, followup}
     の最初の audio chunk を server writer が送信

first_content_client_ms:
  origin observation を server が受信
  -> response_kind in {content, correction, followup}
     の order の playback_started を server が受信
```

canonical report の `first_content_ms` も client telemetry がある場合だけ client 値を使う。

#### correction

```text
correction_ms:
  旧 candidate を無効化した observation を server が受信
  -> response_kind=correction の playback_started を server が受信
```

#### cancel

```text
cancel_to_server_stop_ms:
  STOP/REPLACE の原因 event を hot-path が受信
  -> playback_generation_id を invalid にした時刻

cancel_to_client_playback_stop_ms:
  STOP/REPLACE の原因 event を hot-path が受信
  -> 対象 generation の playback_ended を server が受信
```

browser event 欠落時は `null` とし、timeout flag を立てる。推測値を入れない。

#### stale audio

```text
old_generation_chunks_after_invalidation:
  playback generation invalidation 後に
  ordered WS writer が送信した旧 generation audio chunk 数
```

目標値は常に `0`。

### 4.7 Queue policy の固定分類

| 種別 | policy |
|---|---|
| realtime `TurnMaterials` | capacity 1 相当、latest-wins |
| 同一 utterance の partial revision | key 単位 coalesce、古い revision を置換可 |
| final STT | silent drop 禁止 |
| STOP / cancel / replace | 最優先、silent drop 禁止 |
| session boundary | silent drop 禁止 |
| canonical persistence event | silent drop 禁止、idempotent retry |
| stale candidate / expired sense result | deadline 後 drop 可、理由を記録 |
| normal noncritical result | bounded queue、overload policy を明示 |

「bounded にしたので安全」とは扱わない。drop 対象と drop 禁止対象をテストで分ける。

### 4.8 live view と canonical record

```text
live view:
  partial/final revision を更新できる
  UI、早期 feedback、barge-in、gate 用
  memory/summary の原本ではない

canonical user record:
  final user utterance のみ
  utterance_id ごとに最大 1 件

canonical assistant record:
  実際に playback_started が観測された substantive segment のみ
  backchannel/ack は別 audit class
  cancel された未再生 text 全文を保存しない
```

---

## 5. Artifact の固定配置

新しい測定 artifact は次へ保存する。

```text
_docs/openai-plan/
  repository-map.md
  o0a-contract.md
  o0b-baseline/
    manifest.json
    raw-samples.jsonl
    summary.json
    summary.md
  o0b-preemption/
    stop.json
    replace.json
    report.md
  o0b-slow-path/
    llm-5s.json
    db-5s.json
    tool-30s.json
    report.md
  decision-gate-o0.md
  o1a-preemption/
  o1b-live-control/
  o1c-backpressure/
  o2-emission/
  o3-canonical/
  o4-delegation/
  o5-warmup/
  o6-context/
  o7-soak/
```

実測の要約は `_docs/latency.md` に append する。raw JSON/JSONL を `_docs/latency.md` に貼り込まない。

### 5.1 manifest.json の必須 field

```json
{
  "schema_version": 1,
  "phase": "O0b",
  "run_id": "...",
  "git_commit": "...",
  "git_dirty": false,
  "started_at_utc": "...",
  "host": {
    "os": "...",
    "machine": "..."
  },
  "config_fingerprint": "sha256:...",
  "backends": {
    "stt": "...",
    "llm": "...",
    "tts": "..."
  },
  "cold_warm": "cold|warm",
  "scenario": "...",
  "sample_count": 0
}
```

### 5.2 raw sample の必須 field

```json
{
  "run_id": "...",
  "sample_index": 1,
  "origin_trace_id": "...",
  "order_id": "...",
  "decision_generation_id": 1,
  "playback_generation_id": 1,
  "response_kind": "content",
  "first_feedback_server_ms": 0.0,
  "first_feedback_client_ms": 0.0,
  "first_content_server_ms": 0.0,
  "first_content_client_ms": 0.0,
  "correction_ms": null,
  "cancel_to_server_stop_ms": null,
  "cancel_to_client_playback_stop_ms": null,
  "old_generation_chunks_after_invalidation": 0,
  "queue_max_depth": {},
  "queue_max_age_ms": {},
  "frame_gap_ms": {},
  "event_loop_lag_ms": {},
  "errors": []
}
```

### 5.3 統計ルール

- 30 response sample は raw values、p50、p95、max を出す。
- 30 sample から p99 を主張しない。
- p99 は frame/event-loop sample のように十分な母数がある場合、または 60 分以上の soak でのみ出す。
- `no_audio` は音声を期待する scenario だけを母集団にする。
- cold 5 回は raw values / median / max。p95 を出さない。
- warm 20 回以上は raw values / p50 / p95 / max。
- 異なる backend、seed、prompt、cold/warm を同じ母集団に混ぜない。

---

## 6. Repository Map を最初に固定する

後続 Task は `_docs/openai-plan/repository-map.md` に記録された実ファイルだけを使う。

### 必須 component

| logical component | expected symbol / path hint |
|---|---|
| shared speech order DTO | `SpeechOrder`, `server/shared/models.py` |
| hot-path public WS | `server/hot_path/app.py` |
| partial lane | `AudioPartialLane` |
| final lane | `AudioFinalLane` |
| TurnMaterials latest value | `server/hot_path/turn_materials.py` |
| internal WS client/server | `server/hot_path/ws_control.py`, `server/tomoko/realtime.py` |
| remote Tomoko core | `RemoteTomokoWsCore` |
| conversation core | `TomokoConversationCore.handle_observation` |
| LLM fire gate | `LlmFireGate` |
| speech emission gate | `SpeechEmissionGate` |
| speech executor | `SpeechOrderExecutor` |
| result queue | `result_queue` |
| deferred TTS path | `_send_audio_conversation_result`, `execute_stream` |
| browser audio scheduler | current client audio playback module |
| sense request record | `SenseRequestRecord` |
| canonical utterance store | current durable utterance model/store |
| context snapshot | current closed-session/context snapshot implementation |
| scenario runner | real replay/autopilot runner |
| latency artifact writer | current `_docs/latency.md` writer or script |

### 一意性ルール

各 symbol は次で検索する。

```bash
rg -n "class SpeechOrder|SpeechOrder\(" server tests
rg -n "class RemoteTomokoWsCore|RemoteTomokoWsCore" server tests
rg -n "class TomokoConversationCore|handle_observation" server tests
rg -n "class LlmFireGate|class SpeechEmissionGate" server tests
rg -n "class SpeechOrderExecutor|execute_stream" server tests
rg -n "result_queue|AudioPartialLane|AudioFinalLane" server tests
rg -n "playback_started|playback_ended|buffered_until" client server tests
rg -n "SenseRequestRecord" server tests
```

- 1 件に一意なら repository map に記録する。
- 0 件なら停止する。
- 複数 owner 候補があるなら停止する。
- 推測で「たぶんこれ」を選ばない。

---

## 7. Phase 全体の hard gate

| Gate | unlock 条件 | unlock される範囲 |
|---|---|---|
| G0A | O0A-00〜O0A-07 完了 | O0b |
| G0B | O0b artifact 完成 | 人間 decision gate |
| G0H | 800ms の対象、server/client stop budget、regression budget を人間が記入 | O1a |
| G1A | O1a preemption PASS | O1b |
| G1B | O1b live control PASS | O1c |
| G1C | O1c bounded/idempotent PASS | O2 |
| G2 | O2 stale emission 0 | O3 |
| G3 | O3 canonical correctness PASS | O4 |
| G4 | O4 stale delegation 0 | O5 |
| G5 | warm-up/cache の採否が artifact 化 | O6 |
| G6 | prompt token soft limit と continuity requirement を人間が確定 | O6 実装本体 |
| G7 | O6 cutover/rollback PASS | O7 |

後続を unlock するのは人間である。エージェントは gate を自動で書き換えない。

---

# Phase O0a: response taxonomy と観測契約

## O0A-00: repository inventory と current behavior map

### 目的

コードを変更する前に、`openai.md` が指した symbol と現在の repository を一意に対応づける。

### 変更許可

- `LOG.md` への append
- `_docs/openai-plan/repository-map.md` の新規作成
- `_docs/openai-plan/o0a-contract.md` の新規作成

### 変更禁止

- runtime code 全て
- test code 全て
- config
- DB schema
- client

### 実行手順

1. Source of Truth を読む。
2. `git status` と commit hash を記録する。
3. Section 6 の `rg` を実行する。
4. 各 logical component について次を記録する。
   - exact path
   - exact symbol
   - current owner
   - current tests
   - current queue maxsize
   - current ID fields
   - current log fields
5. 次の current call chain を文字で固定する。

```text
browser mic
  -> public /ws receive
  -> VAD/STT partial/final
  -> internal WS observation
  -> Tomoko conversation handling
  -> LlmFireGate
  -> LLM
  -> SpeechEmissionGate
  -> SpeechOrder
  -> hot-path result queue
  -> SpeechOrderExecutor / TTS
  -> ordered WS audio send
  -> browser playback
```

6. STOP/REPLACE の current call chain を別に固定する。
7. DB persistence が order delivery より前か後か、どこで await されるかを固定する。
8. `openai.md` の行番号が現コードとずれている場合は repository map に差分を記録する。

### 完了条件

- 必須 component がすべて一意に解決されている。
- current queue / lock / await graph が記録されている。
- runtime code は変更されていない。
- 次 Task の allowlist が exact path で決められる。

### 停止条件

- `SpeechOrder` owner が複数ある。
- current v2 path が存在しない。
- `openai.md` の分析対象と repository が大きく異なる。
- current worktree の未コミット差分と O0 が競合する。

---

## O0A-01: ResponseKind contract の追加

### 目的

相づちと意味回答を決定論的に分ける。まだ latency 集計は変更しない。

### 変更許可

- repository map で特定した shared DTO file
- repository map で特定した SpeechOrder creation site
- dedicated unit test file
- `LOG.md` append

### 変更禁止

- gate score
- speech timing
- TTS execution
- queue behavior
- DB schema
- browser

### 先に書くテスト

1. 許可された ResponseKind が 5 種だけである。
2. `mode != stop` の speech order は `response_kind` が必須。
3. `mode=stop` は `response_kind=None` を許可。
4. fixed backchannel creation site は `backchannel`。
5. acknowledgement creation site は `acknowledgement`。
6. main reply / initiative / calendar は `content`。
7. final reconcile は `correction`。
8. background sense result は `followup`。
9. JSON serialize / internal WS round-trip で値が保たれる。

### 実装手順

1. 既存 model style で `ResponseKind` を定義する。
2. `SpeechOrder` 相当へ field を追加する。
3. 全 creation site を `rg "SpeechOrder\("` で列挙する。
4. Section 4.1 の mapping に従って全 site を明示更新する。
5. default を使って未分類 site を隠さない。
6. `mode=stop` だけは明示的に `None` を入れるか、既存 constructor 規約に沿って omission を許可する。
7. internal WS encode/decode を更新する。
8. unknown string は parse error にし、`content` へ黙って fallback しない。

### 完了条件

- 全 SpeechOrder creation site が分類済み。
- text 内容から後分類していない。
- unit / full unit / ruff / diff check が PASS。
- runtime timing は変わっていない。

---

## O0A-02: origin trace と generation owner の固定

### 目的

Tomoko の判断世代と hot-path の再生世代を混同しない。

### 変更許可

- shared DTO
- Tomoko decision owner
- hot-path execution state
- internal WS serializer
- dedicated unit tests

### 変更禁止

- generation invalidation behavior の変更
- STOP/REPLACE の dispatch 経路変更
- TTS task 化
- DB schema

### 先に書くテスト

1. Tomoko が `decision_generation_id` を発行する。
2. hot-path は受信した `decision_generation_id` を変更しない。
3. hot-path が order accept 時に `playback_generation_id` を発行する。
4. Tomoko は `playback_generation_id` を発行しない。
5. `origin_trace_id` が order / execution / milestone に伝播する。
6. 既存 `trace_id` を再利用する場合、同一 origin であることを test 名と docstring に明記する。
7. replace 後に decision generation と playback generation が独立して進む。

### 実装手順

1. current `trace_id` / request id / generation field を棚卸しする。
2. 同じ意味の field は再利用する。
3. 足りない field だけ shared model に追加する。
4. decision owner で monotonic generation を管理する。
5. playback owner で monotonic generation を管理する。
6. module-level counter を作らない。connection/session owner の field に置く。
7. log へ両 generation を同時に出せるようにするが、制御挙動はまだ変えない。

### 完了条件

- owner 境界が test で固定される。
- 既存 generation guard の挙動は変わらない。
- full unit / ruff / diff check が PASS。

---

## O0A-03: milestone event と純粋集計器

### 目的

STT / LLM / order / TTS / playback の観測点を固定し、feedback と content を別集計できるようにする。

### 推奨 dedicated module

repository に同等 module がなければ、小さい observability module を新設する。大きい `conversation.py` / `app.py` に集計ロジックを直接書かない。

### 先に書くテスト

1. Section 4.5 の milestone 名以外を受け付けない。
2. milestone は `origin_trace_id` / order / generation / response_kind を保持する。
3. out-of-order event が来ても、負 latency を出さず invalid sample として理由を残す。
4. first milestone は一度だけ採用する。
5. backchannel だけでは `first_content_*` を埋めない。
6. `content` order の playback start で `first_content_client_ms` が埋まる。
7. cross-process monotonic 値を直接減算しない。
8. missing telemetry は `null` になる。

### 実装手順

1. milestone record の内部 model を追加する。
2. pure aggregator を追加する。
3. server receipt time と process-local duration だけを入力にする。
4. log 出力と artifact 出力を分ける。
5. aggregator は state owner ではなく観測用 helper とする。
6. metric 算出失敗で live path を落とさない。ただし parse error は structured error count に残す。

### 完了条件

- fixture だけで metric を再現できる。
- runtime path への hook はまだ最小で、speech timing を変えていない。
- unit / full unit / ruff / diff check が PASS。

---

## O0A-04: browser playback observation

### 目的

server synthesis 完了ではなく、browser が実際に再生を開始・終了した事実を返す。

### 変更許可

- current browser audio scheduler
- public `/ws` JSON event parser
- shared playback observation model
- tests

### 変更禁止

- client 側発話判断
- client retry
- public endpoint 追加
- audio encoding 変更
- playback schedule algorithm の最適化

### 先に書くテスト

server side:

1. `playback_started` / `playback_ended` / `playback_buffered` だけを受け付ける。
2. `order_id` / `playback_generation_id` が必須。
3. connection 内 `client_event_seq` の重複は idempotent に扱う。
4. stale generation の observation は audit へ残すが current playback truth を巻き戻さない。
5. malformed event で server を落とさない。

client side:

6. source node の実 start 時に started を一度送る。
7. `onended` で ended を一度送る。
8. scheduled buffer 更新時に buffered_until を送る。
9. client は stop/replace を判断しない。

### 実装手順

1. current browser audio scheduling code を特定する。
2. 既存 `order_id` / generation metadata が browser へ届く方法を確認する。
3. metadata がない場合、既存 `/ws` JSON control event を拡張する。新 endpoint は作らない。
4. browser は event を送るだけにする。
5. server は受信時刻を monotonic clock で記録する。
6. O0 では playback truth を gate にまだ使わない。観測だけにする。

### 実 browser 確認

- 1 order につき started が 1 回。
- 正常完了で ended が 1 回。
- stop で ended または stop-observed event が返る。
- console error がない。
- audio gap が増えていない。

### 完了条件

- client observation が既存 `/ws` で往復する。
- client に判断ロジックが増えていない。
- server metrics へ causal ID 付きで入る。

---

## O0A-05: frame / queue / event-loop / internal WS instrumentation

### 目的

slow path が voice delivery を止めている場所を分解できるようにする。まだ queue policy は変えない。

### 固定観測項目

- mic frame inter-arrival gap
- receive loop processing duration
- event-loop lag
- partial lane depth / age / coalesce count / drop count
- final lane depth / age / drop count
- materials lane update age
- result queue depth / oldest age
- control event depth
- TTS queue depth
- persistence wait duration
- internal WS request/ack RTT
- ordered writer pending count

### 実装上の制約

- 8 ms frame ごとに JSON log を出さない。
- counter / histogram は memory 内で更新し、structured summary は最大 1 Hz を標準とする。
- 既存 metrics framework がある場合はそれを使う。
- raw mic audio や raw VAP value を artifact に保存しない。
- instrumentation failure で audio path を止めない。

### 先に書くテスト

1. queue depth/age を fake clock で測れる。
2. coalesce/drop counter が policy event に応じて増える。
3. event-loop lag probe が cancellation で終了する。
4. metrics task が connection close 後に残らない。
5. 1 Hz throttle が hot loop log spam を防ぐ。
6. internal WS RTT は同じ process clock 内の send/ack で計算する。

### 完了条件

- current behavior を変えずに bottleneck 分解が可能。
- long run で metrics task leak がない。
- unit / integration / full unit / ruff / diff check が PASS。

---

## O0A-06: artifact writer と response taxonomy 集計

### 目的

同一 schema で baseline / preemption / slow-path probe を保存できるようにする。

### 先に書くテスト

1. manifest 必須 field が欠けると writer が明示失敗する。
2. raw sample は JSONL で 1 sample 1 行。
3. p50/p95/max が deterministic fixture と一致する。
4. n=30 では p99 を出さない。
5. no-audio scenario の母集団 filter が固定される。
6. dirty worktree flag を保存する。
7. config fingerprint が同じ config で安定する。
8. secret/token/path の値を fingerprint input artifact に平文保存しない。

### 実装手順

1. current artifact helper があれば拡張する。
2. なければ pure writer module を小さく追加する。
3. config は key/value の正規化後に sha256 を取る。
4. backend/model/version は human-readable field として別保存する。
5. raw sample と summary を分ける。
6. `_docs/latency.md` には summary と artifact path だけ append する。

### 完了条件

- fixture から artifact 一式を生成できる。
- production speech behavior は変わらない。

---

## O0A-07: O0a checkpoint

### 実行する検証

- O0A 対象 unit tests
- full unit
- WS integration tests
- client tests または static validation
- ruff
- `git diff --check`
- startup smoke
- 1 回の実 browser conversation

### checkpoint artifact

`_docs/openai-plan/o0a-contract.md` に次を固定する。

- ResponseKind mapping
- ID owner mapping
- playback event schema
- milestone schema
- metric definitions
- actual file mapping
- 実行した test command と結果
- 未解決事項

### 完了条件

- acknowledgement だけで first content が埋まらない。
- browser observation は観測専用。
- generation owner が分離されている。
- current conversation behavior に明確な regression がない。
- 人間が O0b を unlock する。

---

# Phase O0b: baseline と非破壊 probe

## O0B-00: baseline runner の固定

### 目的

比較条件を毎回変えない real `/ws` latency suite を固定する。

### 固定条件

- current default WhisperKit / Argmax CLI `large-v3-v20240930_turbo`
- encoder / decoder compute units は current config
- 同じ audio fixture
- 同じ prompt/persona/config
- 同じ host
- 同じ browser version
- 同じ warm-up 手順
- 10 response x 3 rounds = 30 response sample

### cold / warm の定義

```text
cold:
  対象 backend process/model を fresh start し、最初の real request を測る。

warm:
  readiness 完了後、規定 warm-up を 1 回実行し、同一 process で測る。
```

OS reboot や page cache flush を cold の必須条件にしない。何を restart したか manifest に書く。

### 先に書くテスト

- runner が run_id と sample_index を安定発行する。
- 30 sample 未満なら summary を completed にしない。
- scenario failure と harness failure を分ける。
- current config fingerprint を保存する。

### 完了条件

- dry-run で scenario と artifact path が確認できる。
- runtime behavior は変わっていない。

---

## O0B-01: real `/ws` baseline 実測

### 実行順

1. git clean state と commit を記録する。
2. backend readiness を確認する。
3. cold 5 回を別 artifact に取る。
4. warm-up する。
5. warm 10 回 x 3 round を取る。
6. raw sample を保存する。
7. p50/p95/max を計算する。
8. first feedback と first content を別表にする。
9. STT / LLM / DB/tool / TTS / queue / client playback の区間別にする。
10. `_docs/latency.md` へ summary を append する。

### 禁止

- baseline 中に threshold を変更しない。
- 一部失敗 sample を理由なく除外しない。
- negative partial-origin を semantic content の速さとして説明しない。
- old default backend の過去値と current default を同じ表の同一系列にしない。

### 完了条件

- raw 30 sample がある。
- feedback/content が混ざっていない。
- current default の真の baseline が説明できる。

---

## O0B-02: in-flight TTS preemption 診断 probe

### 目的

現行 STOP/REPLACE が TTS generator 動作中に効くかを、通常 suite を赤くせず診断する。

### fake TTS の固定挙動

```text
chunk count: 8
chunk interval: 100 ms
first chunk barrier: あり
completion event: 8 chunk 後
cancel observation: first chunk 送出後、2 chunk 目の前
```

### STOP scenario

1. order A を開始する。
2. first chunk barrier を待つ。
3. order A の `audio_complete` 前に STOP を投入する。
4. generation invalidation 時刻を記録する。
5. invalidation 後の A chunk 数を数える。
6. server stop と client playback stop を記録する。

### REPLACE scenario

1. order A を開始する。
2. first chunk barrier を待つ。
3. A 完了前に order B `replace_current` を投入する。
4. A generation を invalid にするまでの時間を測る。
5. invalidation 後 A chunk 数を数える。
6. B の first playback start を測る。
7. A が再開しないことを確認する。

### probe の終了コード

- harness が壊れた場合だけ non-zero。
- feature が未達の場合は artifact に `feature_pass=false` を保存し、command 自体は正常終了してよい。
- O1A-00 で同じ scenario を通常 red test に昇格する。

### 完了条件

- current pass/fail が artifact に残る。
- 旧 audio を拾うだけの偽 overlap 判定ではない。
- `tts_result` 完了を待ってから割り込んでいない。

---

## O0B-03: slow-path 非 gating probe

### LLM 5 秒

- fake LLM は開始 barrier 後 5 秒待つ。
- その間も mic frame、Materials、新 STT、cancel 受付数を記録する。
- pending observation age を記録する。

### DB 5 秒

- canonical persistence fake/store を 5 秒待たせる。
- order delivery と mic receive が続くか記録する。
- 現行が止まる場合は failure artifact とする。

### tool 30 秒

- sense worker result を 30 秒待たせる。
- 別 topic の会話を実行する。
- late result がそのまま speech order になるか記録する。

### 実時間短縮禁止

unit test では fake clock/barrier を使ってよいが、O0b artifact 用 probe は指定した wall-clock delay を一度は実行する。

### 完了条件

- どの slow path が voice/control を止めるか説明できる。
- feature failure は artifact 化されている。
- 通常 suite は green。

---

## O0B-04: O0b 統合 report

`_docs/openai-plan/o0b-baseline/summary.md` に次を書く。

- current git commit
- backend/config fingerprint
- first feedback p50/p95/max
- first content p50/p95/max
- correction sample
- server/client cancel latency
- old generation chunk count
- frame gap / event loop lag
- queue max depth / max age
- slow LLM/DB/tool の影響
- pass/fail と根拠 artifact
- O1 で直すべき最小箇所

### 完了条件

- 計測値と推測が分離されている。
- OpenAI 記事の数値を Tomoko へ外挿していない。
- Go/WebRTC などの実装提案へ飛躍していない。

---

# Human Decision Gate G0H

O1 へ進む前に、人間が `_docs/openai-plan/decision-gate-o0.md` を作成し、次を記入する。

```text
G0H_STATUS=APPROVED | REJECTED | NEEDS_MORE_DATA

E2E_800MS_APPLIES_TO=
  first_feedback_client_ms |
  first_content_client_ms |
  another explicitly named milestone

CANCEL_TO_SERVER_STOP_BUDGET_MS=<number>
CANCEL_TO_CLIENT_PLAYBACK_STOP_BUDGET_MS=<number>
PAIRED_REGRESSION_BUDGET_PERCENT=<number>
PAIRED_REGRESSION_BUDGET_ABSOLUTE_MS=<number or NONE>

NOTES=
```

エージェントは値を推測して埋めない。

---

# Phase O1a: physical TTS preemption

## O1A-00: diagnostic probe を通常 red test へ昇格

### 目的

O0B-02 で観測した未達を、修正対象の deterministic regression test にする。

### test 名の例

- `test_stop_preempts_inflight_tts_before_audio_complete`
- `test_replace_preempts_inflight_tts_and_old_generation_never_resumes`
- `test_no_old_generation_chunk_after_invalidation`
- `test_control_mailbox_bypasses_normal_result_backlog`

### red の確認

- failure reason が「現在 TTS 完了まで STOP/REPLACE が届かない」であること。
- timeout や harness error ではないこと。

同じ Task 内で実装へ進まず、O1A-00 は red test と原因確認だけで止めてもよい。ただし通常 branch に赤状態を commit しない。

---

## O1A-01: output ownership map

### 目的

修正前に current result sender / executor / writer / TTS task の owner を固定する。

### docs-only artifact

`_docs/openai-plan/o1a-preemption/ownership.md`

必須記載:

- normal result queue owner
- STOP/REPLACE receive point
- `SpeechOrderExecutor` owner
- TTS generator task owner
- binary WS writer owner
- generation invalidation point
- client stop control event order
- current shutdown path

### 完了条件

- 物理出力 state の owner が hot-path 一箇所に定まる。
- Tomoko 判断 owner を hot-path へ移さない設計になっている。

---

## O1A-02: 高優先 control mailbox

### 目的

STOP/REPLACE を normal result queue の後ろに並ばせない。

### 固定 contract

```text
control mailbox:
  STOP
  REPLACE invalidation
  connection close
  emergency playback reset

normal result lane:
  append order
  ordinary generated result
  noncritical status
```

### 実装ルール

- control mailbox は hot-path session/connection owner の field。
- module-level queue にしない。
- STOP/REPLACE を silent drop しない。
- control mailbox への enqueue が不能なら明示 failure と connection safety stop を選ぶ。黙って normal lane へ fallback しない。
- read loop は control を先に drain する。
- personality判断を mailbox に追加しない。

### 先に書くテスト

1. normal result backlog があっても STOP が先に適用される。
2. STOP は最大一回適用される。
3. duplicate STOP は idempotent。
4. stale STOP が新 generation を止めない。
5. connection close で mailbox task が残らない。

---

## O1A-03: playback generation ごとの cancellable TTS task

### 固定 state

```text
HotPathOutputController:
  current_order_id
  current_decision_generation_id
  current_playback_generation_id
  current_tts_task
  append_queue
  invalid_generations
  writer
```

既存 `SpeechOrderExecutor` が同じ責務を持つ場合、新しい controller を重複作成せずそこへ集約する。

### cancel 順序

1. 対象 playback generation を invalid にする。
2. ordered writer が旧 generation を送れない状態にする。
3. client stop control を enqueue する。
4. TTS task に cancel を要求する。
5. backend が cancellation を無視して返す chunk も generation guard で捨てる。
6. task terminal state を audit する。

### 先に書くテスト

- cancellation-aware backend は task が cancel される。
- cancellation-unaware backend でも旧 chunk が送信されない。
- task cancel error が new generation を止めない。
- replace で append queue policy が既存 contract どおりになる。
- stop で append queue が空になる。

---

## O1A-04: single ordered WS writer

### 目的

複数 task が同じ WebSocket へ直接送信して順序を壊さないようにする。

### 固定 contract

- audio binary と audio control event は single writer owner から送る。
- enqueue 時と send 直前の両方で generation validity を確認する。
- invalid chunk は writer 内で count し、送らない。
- writer queue 自体も bounded policy の対象だが、容量変更は O1c で行う。
- STOP control と旧 chunk の順序は「generation invalidation が先」。旧 chunk は STOP より前でも後でも invalidation 後には送れない。

### 先に書くテスト

1. invalidation と chunk enqueue の race で旧 chunk 0。
2. two producers でも WebSocket send は一 task。
3. writer error で task leak がない。
4. replace 後に旧 generation が再開しない。
5. new generation の chunk order は sequence 順。

---

## O1A-05: STOP / REPLACE integration

### STOP

- current generation invalidation
- current TTS task cancel
- append queue clear
- client playback stop
- current execution state clear

### REPLACE

- current generation invalidation
- current TTS task cancel
- current old append policy を既存仕様どおり処理
- new playback generation 発行
- new order TTS start

### 禁止

- STOP/REPLACE の判断を hot-path で新規に行わない。
- user speech の意味解釈を hot-path に置かない。
- fade/short silence をこの Phase で新規最適化しない。既存挙動を維持する。

---

## O1A-06: O1a verification

必須 test:

- slow multi-chunk STOP
- slow multi-chunk REPLACE
- cancel-unaware backend
- normal completion
- append queue
- duplicate control
- connection close during TTS
- writer failure
- invalid generation chunk 0

必須 metric:

- cancel_to_server_stop
- cancel_to_client_playback_stop
- old_generation_chunks_after_invalidation
- replace first content

G0H budget を満たさない場合、threshold を緩めず原因を artifact に残す。

---

## O1A-07: 人間の実 browser / 耳確認

確認項目:

- 長い発話中に stop してすぐ止まる。
- replace 後に旧音声が一瞬戻らない。
- 新音声の先頭が欠けない。
- stop 後に append 音声が再生されない。
- audio pop/click/gap が悪化していない。
- server log の order/generation が追える。

人間確認前に commit しない。

---

# Phase O1b: single-owner live control

## O1B-00: current lock / await graph characterization

### 目的

`RemoteTomokoWsCore` の単一 lock、`hot_path_realtime()`、`handle_observation()`、persistence await の current ordering を test と docs で固定する。

### artifact

`_docs/openai-plan/o1b-live-control/current-await-graph.md`

### 固定する事実

- internal WS read がどこで heavy LLM を await するか
- new observation がどこで待つか
- DB persistence が ack/order delivery の前か後か
- state mutation owner
- completion callback の thread/task

runtime code はまだ変更しない。

---

## O1B-01: arbiter event contract

### logical events

```text
ObservationReceived
MaterialsUpdated
CancelRequested
InferenceStarted
InferenceFirstSentenceReady
InferenceCompleted
InferenceFailed
InferenceCancelled
CandidatePrepared
CandidateInvalidated
SpeechOrderEmitted
PersistenceCompleted
ConnectionClosed
```

既存 event model があれば再利用する。汎用 EventBus は作らない。

### 先に書くテスト

- event は causal IDs を保持する。
- state mutation は arbiter task からだけ行う。
- child task completion は event として arbiter mailbox に戻る。
- external task が `TomokoConversationCore` state を直接変更しない。

---

## O1B-02: internal WS reader を enqueue-only にする

### 固定責務

internal WS reader:

- decode / validate
- server receive timestamp
- arbiter mailbox enqueue
- protocol error response

internal WS reader が行ってはいけないこと:

- LLM await
- tool await
- DB persistence await
- SpeechEmissionGate の最終判断
- state mutation

### 先に書くテスト

- fake LLM 5 秒中も reader が次 observation を受理する。
- cancel event の受付 latency が G0H budget 内。
- malformed event が arbiter を落とさない。

---

## O1B-03: cancellable child task registry

### owner

arbiter/session owner の instance field。

### key

`decision_generation_id`。

### fixed behavior

- generation ごとに最大 1 main inference task。
- new generation が supersede したら旧 task cancel request。
- cancel 後に completion が戻っても stale event として捨てる。
- task exception は event 化し、unhandled task exception を残さない。
- connection close で全 task cancel + await terminal。

### 先に書くテスト

- old completion から order が出ない。
- cancel/completion race で order 最大一回。
- connection close 後 task 0。
- module-level registry なし。

---

## O1B-04: pending inference の supersede

### fixed rule

- 新 observation 自体が即 cancel を意味するわけではない。
- current scheduler/gate が cancel/supersede decision を出す。
- transport reader は observation を届けるだけ。
- arbiter は existing policy decision に基づき generation を進める。
- generation invalidation 後の first sentence / completion は speech candidate にしない。

### 禁止

- `user_speaking=true` だけを新しい人格 policy として hardcode しない。
- current Materials/Pressures/Gates を飛ばさない。
- LLM に cancellation owner を渡さない。

---

## O1B-05: exactly-once emission race control

### fixed terminal state

```text
pending
prepared
emitted
suppressed
cancelled
failed
```

同一 `decision_generation_id + candidate_id` は terminal state を一つだけ持つ。

### 先に書くテスト

- completion then cancel
- cancel then completion
- duplicate completion
- reconnect retry
- internal WS duplicate frame

いずれも SpeechOrder 最大一件。

---

## O1B-06: structured decision trace

各 transition に次を出す。

```text
event_type
origin_trace_id
decision_generation_id
previous_state
next_state
child_task_state
reason
queue_age_ms
```

text 全文や raw audio を高頻度 log に入れない。必要なら hash/length/order ID を使う。

---

## O1B-07: O1b verification

必須 scenario:

- LLM 5 秒中の mic receive
- LLM 5 秒中の new partial
- LLM 5 秒中の final
- LLM 5 秒中の stop
- cancel/completion race 100 回
- duplicate internal WS event
- normal completion
- connection close

完了条件:

- slow inference 中も control が生きる。
- stale decision から SpeechOrder 0。
- owner/ordering が log で一意。
- full unit / integration / perf / ruff / diff check PASS。

---

# Phase O1c: bounded backpressure と persistence 分離

## O1C-00: 全 queue inventory

### artifact

`_docs/openai-plan/o1c-backpressure/queue-inventory.md`

各 queue について次を表にする。

```text
name
owner
producer
consumer
current maxsize
current peak depth from O0
current max age from O0
criticality
coalesce key
drop allowed
overload action
shutdown drain policy
```

### capacity 決定規則

既存 bounded capacity がある場合は O0 実測で問題がない限り維持する。

現在 unbounded の queue は、次の式で initial dataclass default を決める。

```text
observed_peak = O0 probe の最大 depth
initial_capacity = clamp(next_power_of_two(max(1, observed_peak) * 4), 16, 256)
```

これは初期値であり環境変数を大量追加しない。capacity と算出根拠を artifact に記録する。

critical lane は normal lane と容量を共有しない。critical capacity は current scenario で同時発生しうる最大 critical event 数の 4 倍、最小 8 とする。

---

## O1C-01: partial coalescing

### fixed behavior

- key は `utterance_id`。
- revision が新しい partial は古い partial を置換できる。
- final は partial slot に入れない。
- different utterance を同じ key にしない。
- coalesce count を記録する。
- final 到着時に同 utterance partial を stale 化してよい。

### 先に書くテスト

- 100 partial -> consumer は最新 revision を取得。
- final は失われない。
- two utterance が混ざらない。
- revision rollback を拒否。

---

## O1C-02: critical lane

critical event:

- STOP
- cancel
- replace invalidation
- final STT
- session boundary
- canonical persistence acknowledgement/failure
- connection close

### fixed behavior

- silent drop 禁止。
- enqueue 不可能なら structured overload failure。
- safety 上必要なら connection/session を stop する。
- normal event を捨てて critical を入れる実装でも、どの normal event を捨てたか記録する。

---

## O1C-03: result queue bounded 化

### fixed behavior

- normal result queue を bounded にする。
- STOP/REPLACE はこの queue を通らない。
- stale/expired result は enqueue 前に捨てる。
- enqueue wait duration を計測する。
- producer が永遠に block しない。
- overload 時の動作を explicit error / coalesce / drop のいずれかに分類する。

### 先に書くテスト

- queue saturation
- stale result drop
- normal result explicit failure
- critical event 通過
- shutdown drain
- no unbounded growth

---

## O1C-04: canonical persistence の idempotency 設計 gate

この Task は高リスクである。最初に existing persistence contract を確認する。

### 進めてよい条件

- 既存 canonical event に stable event ID がある。
- DB に unique/idempotency key を置ける。
- retry owner が一意。
- shutdown drain と process restart recovery の current path が説明できる。

### 既存 durable pending record がある場合

- status を `pending/completed/error` 相当で管理する。
- unique event ID で duplicate insert を防ぐ。
- order delivery は DB completion を await しない。
- persistence worker は retry/backoff する。
- shutdown で bounded drain する。

### durable pending record がない場合

エージェントは generic outbox table を勝手に設計しない。次の二案を artifact に比較し、人間へ委譲して止まる。

```text
A. PostgreSQL outbox / pending canonical event
B. existing source event replay + idempotent canonical write
```

人間判断なしに fire-and-forget へ変更しない。

---

## O1C-05: DB 5 秒 delay regression

### 必須検証

- mic receive が継続。
- Materials update が継続。
- SpeechOrder delivery が継続。
- persistence event は失われない。
- retry しても canonical record 1 件。
- shutdown/restart 後 recovery 可能。

---

## O1C-06: paired performance comparison

- same audio fixture
- same seed
- same backend
- same cold/warm
- baseline commit と O1c commit
- G0H regression budget を使用

budget 超過時に capacity を無根拠に増やさない。queue age / event loop lag / DB wait で原因を説明する。

---

## O1C-07: O1c checkpoint

完了条件:

- 全 queue に maxsize / age metric / overload policy。
- critical silent drop 0。
- duplicate canonical record 0。
- unbounded queue 0。
- slow DB が voice/control を止めない。
- human approval 後に O2 unlock。

---

# Phase O2: emission-time revalidation と playback truth

## O2-00: fire snapshot と emission snapshot の分離

### logical model

```text
FireSnapshot:
  decision_generation_id
  materials_version
  materials_captured_at
  pressure values used by LlmFireGate
  topic/context identity

EmissionSnapshot:
  decision_generation_id
  latest materials_version
  captured_at immediately before emission
  playback truth
  current topic/context identity
  candidate age
  deadline
```

同じ object を使い回さない。

### 先に書くテスト

- fire と emission の captured_at が別。
- LLM 中に Materials update すると emission は新 version。
- old version を使うと test fail。

---

## O2-01: latest Materials accessor

### fixed behavior

- latest-wins store から immutable snapshot を取得する。
- snapshot に version と monotonic captured_at を持たせる。
- raw VAP frame を返さない。
- accessor は read-only。
- gate を直接実行しない。

### 先に書くテスト

- concurrent update/read で partial object を返さない。
- version 単調増加。
- snapshot age 計算。
- connection close 後 stale timeout。

---

## O2-02: pure emission revalidation

### input

- PreparedSpeechCandidate
- latest Materials snapshot
- latest pressures
- current topic/context identity
- playback truth
- current decision generation
- deadline

### output

```text
emit_now
append_after_current
replace_current
hold
suppress
stop
```

既存 `SpeechEmissionGate` の output を使う。二つ目の人格 gate を作らない。

### fixed checks

1. generation current
2. deadline not expired
3. topic/context current
4. latest user_speaking / interruption risk
5. playback truth
6. current SpeechEmissionGate recomputation

### 先に書くテスト

- slow LLM 中に user speaks -> old candidate suppress。
- topic change -> suppress。
- newer candidate -> supersede。
- still valid -> emit。
- high-priority correction -> replace。

---

## O2-03: emission 直前への integration

revalidation point は「LLM first sentence/candidate prepared 後、internal WS で SpeechOrder を送る直前」。

禁止:

- LlmFireGate 前だけで済ませない。
- hot-path で人格判断し直さない。
- revalidation 後に長い DB await を挟まない。
- revalidation snapshot を persistence completion 後まで保持しない。

必要なら order delivery を先、persistence enqueue を後にするが、O1c の durability contract を守る。

---

## O2-04: server-side playback truth

### state

```text
PlaybackTruth:
  active_order_id
  active_playback_generation_id
  playback_started_received_at
  buffered_until_client_value
  last_observation_received_at
  state: unknown | scheduled | playing | ended | timed_out
```

### fixed behavior

- browser observation は事実。
- authoritative floor判断は server。
- missing event 時は timeout で `unknown` または安全側 `playing` へ倒す。
- timeout 値は既存 playback duration/grace から導出し、任意の新環境変数を増やさない。
- reconnect で old connection の sequence を current state に適用しない。

### 先に書くテスト

- server synthesis complete でも client playing は active。
- ended で inactive。
- stale observation ignored。
- reconnect sequence reset。
- missing ended timeout。

---

## O2-05: candidate age / Materials age logging

各 emission decision に次を残す。

```text
candidate_age_ms
fire_materials_age_ms
emission_materials_age_ms
fire_materials_version
emission_materials_version
playback_truth_state
decision
reason
```

完了基準に使うのは emission snapshot age。

---

## O2-06: O2 regression scenarios

- slow LLM + new user speech
- slow LLM + topic change
- slow LLM + explicit stop
- candidate deadline expiry
- server TTS complete + browser still playing
- playback ended event missing
- reconnect during buffered playback
- valid candidate normal emission

完了条件:

- stale candidate speech 0。
- emission material age が artifact で追える。
- generation中 overlap と client-buffer playback overlap を別 test にする。

---

## O2-07: 人間確認

- ユーザーが言い直した時、古い回答を話し始めない。
- すでに話し始めた場合は correction/replace が自然。
- browser buffer 中の音を Tomoko が「もう話し終わった」と誤認しない。
- 過剰 suppress で無反応が増えていない。

---

# Phase O3: live view と canonical record

## O3-00: current DB/read model inventory

### artifact

`_docs/openai-plan/o3-canonical/current-record-map.md`

記録対象:

- partial observation store
- final utterance store
- speech-order audit
- assistant utterance store
- summary input query
- memory input query
- playback observation store
- reconnect idempotency key

DB schema はまだ変更しない。

---

## O3-01: utterance identity contract

### fixed contract

```text
utterance_id:
  speech start から final まで不変

revision:
  partial 更新ごとに増加

final:
  同じ utterance_id の terminal revision
```

### 先に書くテスト

- 30 partial -> 1 final、同じ utterance_id。
- revision 単調増加。
- duplicate final idempotent。
- final 後の古い partial は ignore。
- reconnect replay で duplicate canonical user record なし。

---

## O3-02: live view updater

### fixed behavior

- UI read model は latest revision を更新。
- raw audit は partial/final を保存可。
- canonical user table/store へ partial を insert しない。
- live view failure が canonical write を壊さない。
- live view を memory/summary query に使わない。

---

## O3-03: canonical user record

### fixed behavior

- final utterance だけ。
- `utterance_id` unique。
- retry idempotent。
- speaker/timing は final authoritative value。
- final correction がある場合は existing canonical update policy を明示し、履歴/audit を失わない。

### 先に書くテスト

- 30 partial -> canonical 1。
- duplicate final -> 1。
- process retry -> 1。
- summary input に partial 0。

---

## O3-04: assistant delivery segment

予定全文と実際に再生された可能性のある範囲を分ける。

### logical record

```text
AssistantPlannedSpeech:
  order_id
  full_text
  response_kind
  decision_generation_id

AssistantDeliverySegment:
  order_id
  playback_generation_id
  segment_seq
  text_start
  text_end
  audio_chunk_first_seq
  audio_chunk_last_seq
  sent_at
  playback_started_at: optional
  playback_ended_at: optional
  state: planned | sent | playback_started | completed | cancelled
```

既存 TTS segmentation が text range を持たない場合、O3 開始前に exact mapping を設計し、人間へ確認する。全文を playback_started 扱いにしてはいけない。

### canonical assistant rule

- `response_kind in {content, correction, followup}`。
- playback_started が観測された segment だけ。
- cancelled 未再生 segment は除外。
- backchannel / acknowledgement は emission audit へ残すが substantive canonical utterance と分ける。

---

## O3-05: summary / memory input filter

### 先に書くテスト

- backchannel が memory に入らない。
- acknowledgement が substantive turn を増やさない。
- cancelled full text が memory に入らない。
- delivered content segment は入る。
- user final と assistant canonical の順序が安定する。

### 実装ルール

- summary worker の prompt で除外するのではなく、入力 query/model で canonical record だけを選ぶ。
- raw audit を削除しない。

---

## O3-06: reconnect / revision / idempotency

必須 scenario:

- partial 中 reconnect
- final 送信後 ack 前 reconnect
- assistant playback_started 後 reconnect
- cancelled order replay
- duplicate internal WS delivery

完了条件:

- canonical user/assistant duplicate 0。
- revision rollback 0。
- raw audit から経緯を追える。

---

## O3-07: O3 checkpoint

- UI freshness test と DB correctness test を分ける。
- canonical history は source of truth。
- live view は派生 read model。
- full unit / DB integration / replay / ruff / diff check PASS。
- human approval 後 O4 unlock。

---

# Phase O4: deadline/freshness 付き asynchronous delegation

## O4-00: sense kind と failure policy の人間決定

エージェントは最初に existing sense kind を列挙する。

`_docs/openai-plan/o4-delegation/sense-policy.md` template:

```text
sense_kind:
origin:
user explicitly requested: yes/no
default deadline:
failure speech policy: silent | one_apology | retry_then_one_apology
retry count:
retry backoff:
late result policy: suppress
priority:
```

全 kind の policy が人間承認されるまで実装しない。

---

## O4-01: deadline/expiry contract

### fixed rule

- 最初は `deadline_at` 一つだけ。
- `expires_at` と同義 field を二重追加しない。
- dataclass/default は一箇所。
- deadline を環境変数で kind ごとに大量外出ししない。
- existing `trace_id` が origin identity と同じなら再利用。

### 先に書くテスト

- deadline 前 usable。
- deadline 後 suppress。
- clock timezone に依存しない。
- retry しても original deadline を勝手に延長しない。

---

## O4-02: cancellation / supersede key

existing field で表せるか先に確認する。

必要な場合だけ追加する。

```text
origin_trace_id
sense_request_id
topic_generation_id
cancellation_key
```

同じ意味の key を複数作らない。

### 先に書くテスト

- topic generation change で old result stale。
- explicit stop で cancel。
- unrelated new utterance だけでは policy 次第で継続可能。
- retry result exactly once。

---

## O4-03: delegation milestone

```text
sense_dispatched
sense_usable_result
sense_final_result
sense_failed
sense_expired
followup_candidate_prepared
followup_emitted
```

各 event に origin / request / topic generation / deadline を持たせる。

---

## O4-04: result revalidation

late result は次を通す。

1. request current
2. deadline current
3. topic generation current
4. human-approved failure policy
5. latest Materials / playback truth
6. SpeechEmissionGate

結果:

- emit followup
- append
- hold
- suppress
- replace current only if existing policy allows

worker 自体に speech emission 判断を持たせない。

---

## O4-05: exactly-once followup

### terminal state

```text
pending
usable_received
final_received
delivered
suppressed
expired
failed
```

`delivered` 後の retry/final duplicate で二度話さない。

### 先に書くテスト

- usable then final
- final only
- retry duplicate
- timeout then late final
- stop then late final
- topic change then late final

---

## O4-06: 30 秒 research scenario

1. research dispatch。
2. acknowledgement を出す場合は `acknowledgement`。
3. 30 秒 worker delay。
4. 別 topic で通常 content conversation。
5. old result return。
6. policy に従い suppress または followup。
7. audio receive / first content regression を baseline と paired 比較。

### 完了条件

- delegation 中に別会話成立。
- expired/superseded speech 0。
- failure speech 最大一回。
- worker delay が mic receive を止めない。

---

## O4-07: transport decision

最初は existing DB worker transport を維持する。

usable partial streaming transport を追加してよい条件:

- O4 artifact で final-only が体験/latency bottleneck と確認された。
- human が requirement を承認した。
- existing internal WS/world WS boundary と責務が一致する。

条件を満たさなければ新 transport を作らない。

---

# Phase O5: session warm-up と cache affinity

## O5-00: backend capability inventory

各 backend について次を記録する。

```text
explicit warm-up API
explicit prefill API
session affinity support
cache hit metric
prefill token metric
route/session identifier
fallback behavior
RSS visibility
```

「ありそう」で実装しない。公式 backend response/log で確認できる機能だけ使う。

---

## O5-01: benchmark contract

### cold

- 5 回
- raw values / median / max
- readiness
- first real request
- LLM first token
- TTS first chunk
- RSS

### warm

- 20 回以上
- raw values / p50 / p95 / max
- same prompt
- same backend
- same route condition

### 禁止

- cold 5 から p95。
- prompt が異なる sample の混在。
- cache shape を cache hit と呼ぶこと。

---

## O5-02: nonblocking warm-up

warm-up 対象:

- STT
- main LLM
- TTS
- tool schema
- stable persona/glossary prefix

### fixed behavior

- app startup の main LLM/VOICEVOX readiness contract を壊さない。
- summary/background 31B route failure で `/ws` を止めない。
- session warm-up が mic receive を block しない。
- warm-up failure は lazy path へ fallback。
- warm-up request を canonical history へ保存しない。

### 先に書くテスト

- warm-up failure fallback。
- warm-up 中 mic receive。
- history pollution 0。
- duplicate warm-up idempotent。

---

## O5-03: stable prefix / volatile tail

- current prompt structure を維持する。
- stable prefix の fingerprint を保存する。
- volatile user/context tail を混ぜない。
- prefix 変更時に cache invalidation を記録する。
- prompt cache のために人格/context semantics を変えない。

---

## O5-04: session affinity

利用条件:

- backend が route/session affinity を明示提供。
- route ID を trace できる。
- failure 時に別 route へ fallback できる。
- stale model session が canonical state owner にならない。

backend が提供しない場合は不採用で Phase 完了してよい。

---

## O5-05: prefill の禁止事項

- explicit prefill API がない backend で dummy user turn を送らない。
- fake conversation を cache warm-up に使わない。
- canonical DB へ書かない。
- model output を user-visible にしない。

---

## O5-06: adoption decision

artifact に次を出す。

- cold/warm delta
- cache hit evidence
- route affinity evidence
- RSS cost
- first content impact
- failure/fallback behavior
- adopt / reject

改善がない場合は `REJECTED_NO_MEASURABLE_BENEFIT` で完了。

---

# Phase O6: active context snapshot と atomic cutover

## O6-00: human requirement gate

人間が次を確定するまで implementation を開始しない。

```text
ACTIVE_CONTEXT_SNAPSHOT_REQUIRED=yes/no
PROMPT_TOKEN_SOFT_LIMIT=<number>
RECENT_VERBATIM_WINDOW=<turn or token rule>
COMPACTION_TRIGGER=<deterministic rule>
```

`no` なら O6 は不採用完了としてよい。

---

## O6-01: canonical/source model

### fixed principle

- canonical utterance は変更・削除しない。
- active context は派生 snapshot。
- snapshot から canonical event へ参照できる。
- compaction failure で原本を失わない。

### logical schema

```text
ActiveContextSnapshot:
  snapshot_id
  version
  parent_version
  created_at
  last_canonical_event_id
  compact_history
  recent_verbatim_event_ids
  source_event_ids
  prompt_token_count
  status: building | ready | active | inactive | error
  activated_at
  error
```

DB schema style は existing snapshot convention に合わせる。

---

## O6-02: 100-turn deterministic fixture

fixture に明示 fact ID を入れる。

```text
FACT-001 ...
FACT-002 ...
...
```

テストするもの:

- source event ID が snapshot に残る。
- recent verbatim window が deterministic。
- compact representation が token limit 内。
- next prompt input に required fact ID/reference が入る。

テストしないもの:

- LLM が自然言語で正しく思い出すか。

---

## O6-03: live path 外 compactor

- separate child/background task。
- audio receive / gate / order delivery を await させない。
- canonical read-only input。
- build status を記録。
- failure は old snapshot 継続。
- duplicate build idempotent。

---

## O6-04: snapshot validation

ready にする前に確認する。

- schema valid
- token count <= soft limit
- source references exist
- last event boundary consistent
- recent verbatim order stable
- no live/speculative record mixed

validation fail は `error`。active にしない。

---

## O6-05: request boundary atomic cutover

### fixed behavior

- in-flight LLM request の context を途中変更しない。
- next LLM request 開始時に active snapshot version を一度読む。
- request 終了まで同 version。
- cutover transaction/compare-and-swap は existing DB style に合わせる。
- failure 時 old active version を維持。

### 先に書くテスト

- build 中も normal request。
- ready 後 next request だけ new version。
- current request は old version。
- concurrent cutover one active。
- cutover failure rollback。

---

## O6-06: restart / reconnect

- Tomoko process restart で active version を復元。
- reconnect で duplicate cutover しない。
- building snapshot の orphan recovery policy。
- error snapshot を active にしない。
- old snapshot から rollback 可能。

---

## O6-07: resource constraint

単一マシンで新旧 26B model を同時常駐させることを完了条件にしない。

測定:

- compactor CPU/RSS
- prompt token count
- context build duration
- audio frame gap
- first content regression

完了条件:

- token soft limit 内。
- canonical history intact。
- source reference deterministic。
- compaction 中 voice path 継続。
- rollback PASS。

---

# Phase O7: shadow と long-session soak

## O7-00: shadow safety contract

shadow path は次を禁止する。

- TTS call
- SpeechOrder send
- browser event send
- canonical DB write
- scheduler state mutation
- candidate consume/mark
- external tool side effect

許可:

- read-only input
- pure gate evaluation
- metrics/artifact write
- replay 上の heavy LLM comparison

### 先に書くテスト

- shadow evaluator が output method を呼ぶと fail。
- DB writer mock call 0。
- speech executor call 0。
- same input で live/shadow decision artifact を比較可能。

---

## O7-01: feature flags と kill switch

O4-O6 の各新経路は最初から disabled-by-default または rollback 可能にする。

固定要件:

- config fingerprint に flag を含める。
- runtime 途中で kill した時の owner/cleanup を test。
- kill 後 old stable path へ戻る。
- config drift を startup log に出す。
- environment variable を無制限追加しない。既存 config dataclass に少数 field。

---

## O7-02: replay shadow

- heavy LLM 二重実行は live session で行わない。
- recorded observation replay で比較。
- same seed/backend/prompt。
- decision/output は artifact のみ。
- canonical DB は test DB または no-write adapter。

---

## O7-03: 60 分 wall-clock soak scenario

最低限含める。

1. normal conversation
2. partial/final churn
3. backchannel
4. in-flight stop
5. in-flight replace
6. slow LLM
7. slow DB
8. 30 秒 research
9. topic change
10. reconnect 複数回
11. normal disconnect
12. worker restart
13. queue saturation
14. context snapshot build/cutover（採用時）
15. warm-up fallback

---

## O7-04: soak metrics

- first feedback/content p50/p95/p99/max
- cancel server/client p50/p95/p99/max
- old generation chunk count
- queue depth/age/drop/coalesce
- frame gap
- event loop lag
- internal WS RTT
- RSS/CPU
- task count
- open connection count
- canonical duplicate count
- stale candidate count
- expired followup emitted count
- shutdown residual task count

warm-up 除外時間、測定窓、許容 RSS 増加は Phase 開始時に人間が確定する。

---

## O7-05: lifecycle assertions

soak 後に必ず確認する。

- stale audio 0
- duplicate canonical utterance 0
- expired/superseded followup 0
- residual task 0
- unclosed WS 0
- queue within bounds
- critical silent drop 0
- config fingerprint stable
- rollback works

---

## O7-06: autopilot と人間の耳

有効化条件:

- autopilot 3 回連続 PASS
- artifact schema validation PASS
- human ear check 1 回 PASS
- rollback rehearsal PASS

人間確認:

- 相づちだけ速くなって本文が遅くなっていない。
- stop/replace が自然。
- old audio が戻らない。
- stale research result を突然話さない。
- over-suppress で会話が死んでいない。
- long session 後も人格/context が破綻していない。

---

## O7-07: enable decision

人間が次のいずれかを記録する。

```text
ENABLED
ENABLED_WITH_FLAG_OFF_BY_DEFAULT
REJECTED_REGRESSION
REJECTED_COMPLEXITY
NEEDS_MORE_DATA
```

エージェントが自動 enable しない。

---

# 8. 条件付き研究項目の発動条件

## R1: Stateful streaming STT

発動条件:

- O0/O7 artifact で current `--stream-simulated` が主要 latency/CPU bottleneck。
- partial lag/revision churn が体験を支配。
- human approval。

比較項目:

- partial lag
- revision churn
- final diff
- CPU/ANE
- long utterance growth
- DTO contract compatibility

条件未達なら調査・実装しない。

## R2: より細かい TTS streaming

発動条件:

- complete WAV segment 間の可聴 gap が人間評価で問題。
- cancel granularity が budget 未達の主要因。

比較項目:

- first chunk
- chunk interval
- gap
- cancel granularity
- audio quality
- browser compatibility

## R3: 韻律 Materials

発動条件:

- pitch/energy/hesitation/laughter の欠落が人間評価で主要因。
- current control/latency が先に安定。

禁止:

- raw audio を memory 保存。
- raw high-rate feature を internal WS/DB へ流す。

## R4: Native speech-to-speech backend

発動条件:

- local realistic backend が存在。
- current ownership contract を adapter で守れる。
- floor control / session / durable memory を Tomoko に残せる。

全面置換ではなく candidate generation adapter として A/B する。

## R5: WebRTC / 別言語 relay

WebRTC:

- remote/mobile が正式 requirement の場合だけ ADR から開始。

別言語 relay:

- Python event-loop lag が frame SLO violation の主要因と profile で証明された場合だけ。

OpenAI 記事の Go 結果だけを根拠にしない。

---

# 9. Test Matrix

| 領域 | unit | integration | perf/e2e | browser | soak |
|---|---:|---:|---:|---:|---:|
| ResponseKind | 必須 | WS round-trip | baseline | 表示影響確認 | O7 |
| generation owner | 必須 | internal WS | cancel metric | log確認 | O7 |
| playback observation | parser/client unit | public WS | client latency | 必須 | O7 |
| TTS preemption | 必須 | fake backend | server/client stop | 必須 | O7 |
| live arbiter | race unit | internal WS | control latency | 会話確認 | O7 |
| queue policy | saturation unit | process integration | paired regression | 不要 | 必須 |
| persistence | idempotency unit | real PostgreSQL | DB 5s | 不要 | 必須 |
| emission revalidation | pure gate unit | slow LLM | stale rate | 必須 | O7 |
| canonical record | model/filter unit | real DB | replay | UI別確認 | O7 |
| delegation | deadline/exactly-once | worker | 30s scenario | 必須 | O7 |
| warm-up/cache | capability/fallback | real backend | cold/warm | startup確認 | O7 |
| context cutover | fixture/CAS | real DB | compaction | continuity確認 | O7 |
| shadow | side-effect guard | replay | comparison | 不要 | 必須 |

---

# 10. 推奨 scenario 名

既存 scenario framework がある場合、同じ形式で次を追加する。

```text
openai-o0-baseline-content
openai-o0-baseline-backchannel
openai-o0-inflight-stop
openai-o0-inflight-replace
openai-o0-slow-llm
openai-o0-slow-db
openai-o0-slow-tool
openai-o2-stale-after-user-respeaks
openai-o2-client-buffer-still-playing
openai-o3-thirty-partials-one-final
openai-o3-cancelled-assistant-not-canonical
openai-o4-late-research-topic-changed
openai-o4-retry-followup-once
openai-o5-warmup-failure-fallback
openai-o6-context-cutover
openai-o6-context-cutover-failure
openai-o7-normal-disconnect
openai-o7-reconnect-loop
openai-o7-queue-saturation
```

scenario 名だけ作って空実装を残さない。

---

# 11. Rollback 原則

各 Phase は rollback point を持つ。

### O0

instrumentation flag off または hook removal で元挙動へ戻る。ResponseKind field を DB migration に使う前なら code revert 可能。

### O1a

old result sender path を feature flag で残してよいのは比較期間だけ。二つの writer が同時 active にならないことを test する。

### O1b

arbiter flag off で current serial path に戻せる。state を二 owner へ同時更新しない。

### O1c

queue capacity/persistence worker を flag/config で戻せる。canonical event loss を伴う rollback は禁止。

### O2

emission revalidation を disable して旧 gate path に戻せる。ただし playback telemetry instrumentation は残してよい。

### O3

canonical schema migration は原本を削除しない。new read model を off にして old query へ戻せる。

### O4

deadline/revalidation flag off で existing sense path へ戻せる。duplicate speech を防ぐ idempotency key は残してよい。

### O5

warm-up/cache/affinity は個別 disable。lazy path は常に残す。

### O6

old active snapshot を維持し、version pointer の切替だけ戻す。canonical history は不変。

### O7

kill switch で stable path へ戻す。shadow は side effect を持たないため停止だけでよい。

---

# 12. 禁止される実装パターン

```text
NG: response_kind を text regex / LLM で後分類
NG: STOP を normal result queue の末尾に入れる
NG: TTS task を cancel せず、完走させて全 chunk を生成
NG: generation invalidation 後に writer が旧 chunk を送る
NG: multiple task が WebSocket.send_* を直接呼ぶ
NG: internal WS reader が LLM/DB/tool を await
NG: child task が Tomoko state を直接 mutate
NG: final STT を満杯時に silent drop
NG: canonical persistence を create_task だけで放置
NG: browser が playback state から発話判断
NG: server/client monotonic clock を直接引き算
NG: cancelled SpeechOrder 全文を canonical memory に保存
NG: backchannel を substantive assistant turn として summary へ混入
NG: dummy user turn で prefill を模倣
NG: active context compaction で canonical history を上書き
NG: shadow path が TTS/DB write/SpeechOrder を実行
NG: O0 artifact なしに O1 へ進む
NG: 人間 budget 未決定のまま threshold を決める
```

---

# 13. Lightweight Luna 用 Task 依頼テンプレート

一度に一つだけ渡す。

```text
Tomoko v2 の openai.plan.md Task <TASK-ID> だけを実行してください。

必ず最初に読むもの:
- LOG.md
- MEMORY.md
- PLAN.md
- ARCHITECTURE.md
- AGENTS.md
- openai.md
- openai.plan.md
- _docs/openai-plan/repository-map.md

制約:
- 次 Task へ進まない
- allowlist 外を変更しない
- tests first
- audio hot loop を変更しない
- public endpoint を増やさない
- client に判断を置かない
- hot-path と tomoko-process の ownership を変えない
- unrelated refactor をしない
- commit/push しない

完了時は openai.plan.md の報告フォーマットで結果を出してください。
不明点がある場合は推測せず、LOG/MEMORY に未解決事項を追記して停止してください。
```

---

# 14. 全体完了条件

この initiative は次をすべて満たした時だけ完了とする。

1. feedback と semantic content の latency が別 metric で説明できる。
2. TTS 中 STOP/REPLACE で旧 generation chunk が 0。
3. LLM/DB/tool 中も mic/Materials/control を受理できる。
4. state transition owner が一意。
5. speech emission 直前に latest Materials で再評価する。
6. browser playback truth と server synthesis state を分ける。
7. partial/live view と canonical record を分ける。
8. backchannel/cancelled full text が memory/summary に混入しない。
9. background delegation に deadline/supersede/exactly-once がある。
10. warm-up/cache の効果または不採用理由を artifact で説明できる。
11. active context snapshot を採用する場合、atomic cutover/rollback が通る。
12. 60 分以上の soak で stale audio、duplicate canonical、residual task が 0。
13. autopilot 3 回連続と人間の耳確認 1 回が通る。
14. rollback/kill switch が実演済み。
15. `PLAN.md` / `LOG.md` / `MEMORY.md` / `ARCHITECTURE.md` / `_docs/latency.md` が実装結果と一致する。

---

# 15. 次に行う作業

```text
Task: O0A-00
内容: repository inventory と current behavior map
コード変更: なし
次へ進む条件: repository-map.md が完成し、人間が O0A-01 を許可すること
```
