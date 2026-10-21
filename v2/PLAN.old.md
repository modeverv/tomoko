# PLAN.md

この PLAN は、v1 で得た知見を元に Tomoko v2 を新しく実装するための手順書である。

v1 のソース、テスト、probe、メモリ、ログ、アーキテクチャは `v1/` に退避されている。
v2 では v1 をそのまま継続実装するのではなく、任意タイミングで自然に話す体験を中心に、
プロセス分離と DB-backed state を前提に作り直す。

## v2 のゴール

- Tomoko が「ターン制チャット」ではなく、常に聞き、必要な時に任意タイミングで話す。
- hot path は薄く速く保ち、人格・文脈・自発性・外界観測はプロセス分離して扱う。
- PostgreSQL を唯一の source of truth とし、LISTEN/NOTIFY は id だけを運ぶ wakeup として使う。
- client は v1 と同様、音声入出力と表示だけを担当し、状態判断は持たない。
- LLM に制御フローを渡さず、発話タイミング・床制御・候補採用は deterministic な計算モデルで決める。
- GPU が実測で足りなくなるまでは、汎用推論 queue system は入れない。
- 構造が安定するまでは task 機能を v2 本線に入れない。

## v1 から継承する知見

- 音声 hot loop は primitive のまま扱い、発話境界でだけ DTO に包む。VAD の中で DTO や `datetime.now()` を乱発しない。
- VAD の発話冒頭欠落対策として、idle 中も 500ms 前後の pre-roll buffer を保持する。
- VAP は VAD を置き換えるものではなく、`p_yielding` から無音判定時間を動的に調整する side-channel として扱う。既定値は min 150ms / delta 650ms / threshold 0.90。
- partial STT / v2 advisory / fusion 判定は DB row が本体であり、NOTIFY payload は id のみとする。
- fusion や provisional inference は、まず log-only / offline replay / analysis report で確認してから hot path へ昇格する。
- prompt は圧縮表現を使い、過去 user 履歴は raw text を優先する。派生 prompt を履歴へ重複蓄積させない。
- active session 中に安定する context は session scoped cache で再利用し、PostgreSQL connection は pool する。
- dflash / MLX 系 backend は warm resident 前提で測る。first content / first audio と total latency は分けて記録する。
- メイン会話は Gemma 4 26B A4B を基準線にする。LFM2.5 8B は reasoning/content 分離の都合で現行 main reply には採用しない。
- 即応 short lane は adapter-loaded Gemma 4 E2B OptiQ が最有力。fused quantized model は format 崩れがあったため本線にしない。
- TTS は VOICEVOX PR1823 chunked backend の complete WAV chunk 境界を維持する。raw PCM を client に流さない。segment_length は途切れ回避のため 0.6 秒を基準にする。
- Irodori v3 は true incremental streaming ではなく complete-audio-per-chunk として扱う。
- thinker2 の知見は v2 の user-status / think / candidate process に活かす。ただし raw image や VLM inference は online prompt へ直接混ぜない。
- camera / screenshot inference は latest-frame 優先にし、backlog を古い順に処理しない。
- VLM の JSON 出力は崩れやすい。可能なら固定行 structured output や OCR / OS metadata merge を使う。
- world observation は既存の raw artifact -> normalizer -> interpretation -> candidate 境界を再利用する。
- 自発発話や横槍の threshold は、まず offline initiative sandbox で波形と fire marker を確認してから runtime に入れる。

## v2 プロセス構成

### hot-path-process

音声入出力とモデル実行を担当する透過的なプロセス。

- `/ws` を 1 本だけ持つ。
- browser UI は v1 を再利用し、状態判断を持たせない。
- mic float32 chunk を受け、VAD / STT partial を処理する。
- partial STT は volatile な observation table へ保存し、tomoko-process へ NOTIFY する。
- final STT event は tomoko-process が durable utterance として採用するための材料として渡す。
- tomoko-process から prompt request を受け、メイン会話 LLM / short reaction LLM / TTS を実行する。
- gate は持たない。発話してよいか、どの prompt を実行するかは tomoko-process が決める。
- モデルは起動時に preload / warm-up する。

### tomoko-process

v1 の TomoroSession final owner に相当する人格・床制御プロセス。

- final STT event を durable utterance として DB に保存する。
- 無音期間や speech boundary を見て conversation_session_id を発行・継続・終了する。
- prompt を作り、hot-path-process に model request を NOTIFY する。
- 各種 context / user status / candidate / calendar を LISTEN または polling で取り込む。
- 無音時、自発発話、短い反応、畳み掛け、停止要求への裁定を deterministic model で行う。
- LLM を使って「話してよいか」を決めない。
- 意味の飽和度、VAP、floor availability、candidate pressure、user presence を統合して最終判断する。

### think-process

候補生成と思い出しを担当する background cognition process。

- 会話 summary / embedding と現在文脈を結びつけ、remember candidate を作る。
- world information / calendar / user status と会話 session を結びつけ、candidate を作る。
- 必要に応じて info-aquire-process へ調査依頼を出し、完了後に candidate を作る。
- 直接発話しない。candidate DB へ積むだけにする。

### info-aquire-process

外部情報取得を担当する。

- Google Calendar を取得し DB に保存する。
- world information を既存 operator / Chrome / Perplexity 経由で取得し DB に保存する。
- think-process からの調査依頼を受け、結果を DB に保存して NOTIFY する。

### user-status-aquire-process

ユーザーと画面状態の観測を担当する。

- screenshot / OCR / front app / window title / URL / presence を定期取得する。
- OCR で拾える文字情報を主材料に、ユーザーが何をしているかを推定する。
- 画像そのものは短命 artifact とし、DB には structured observation と summary を保存する。
- 初期頻度は 1 分に 1 回を基準にする。

### summary-process

会話要約と embedding を担当する。

- 会話原本ではなく索引として summary を作る。
- summary は「キーワード + 結論 1 文」を基本単位にする。
- 例: `ユーザーはDDDに懐疑的である`
- summary / embedding / persona state は hot path から切り離す。

### evaluation-process

v2 の体験評価を記録・分析する。

- first content / first audio / total latency を分ける。
- turn-taking naturalness、false participation、memory naturalness、persona consistency を後から join できる形で保存する。
- 人間評価を gold label とし、機械ログは説明変数として扱う。

## 共通完了条件

各 Phase は、原則として以下を満たして完了とする。

- [ ] 実装前に失敗するテストを追加する。
- [ ] 該当 Phase の unit test が通る。
- [ ] DB を変更した Phase は integration test が通る。
- [ ] latency に影響する Phase は first content / first audio / total latency を `_docs/latency.md` に追記する。
- [ ] 新しい process / state transition は structured log に残す。
- [ ] `LOG.md` にやったこと、詰まったこと、次にやることを追記する。
- [ ] 設計判断が確定した場合は `MEMORY.md` に追記する。

## Phase V2.0: repo control plane bootstrap

root に v2 用の作業制御ファイルと最小ディレクトリを作る。

### 実装手順

- [x] root `README.md` を作り、v2 の起動方法、process map、v1 参照方針を書く。
- [x] root `MEMORY.md` を作り、v1 から継承する確定判断と v2 で否定する判断を書く。
- [x] root `LOG.md` を v2 の作業ログとして運用開始する。
- [x] root `PLAN.md` を v2 の source of truth とする。
- [x] v2 用の `server/`, `client/`, `config/`, `tests/`, `scripts/`, `background-process/`, `reports/` を作る。
- [x] `v1/` は参照専用とし、移植は必要なファイル単位で明示的に行う。
- [x] `pyproject.toml` / `uv.lock` / `mise.toml` の現状を確認し、v2 root で test / lint が動くようにする。
- [x] Makefile を root に作り、`make test-unit`, `make lint`, `make check`, `make db-up`, `make db-stop` を定義する。

### 完了条件

- [x] `make check` が空の骨格で通る。
- [x] root の `README.md` / `MEMORY.md` / `LOG.md` / `PLAN.md` を future agent が読める。
- [x] `v1/` を変更していないことを `git diff -- v1` で確認する。

## Phase V2.1: shared DTO and schema contracts

層間 DTO と structured output schema を先に固定する。

### 実装手順

- [x] `server/shared/models.py` を作り、v2 の DTO を一箇所に集約する。
- [x] `AudioSpeechSegment`, `PartialTranscriptObservation`, `FinalTranscriptEvent`, `DurableUtterance`, `PromptRequest`, `ModelOutputEvent`, `AudioChunkOut`, `FloorObservation`, `SpeechDecision`, `UserStatusObservation`, `ContextSnapshot`, `CandidateSeed`, `CandidateRecord`, `SessionSummary` を定義する。
- [x] hot loop 例外を明記し、VAD chunk / audio callback は DTO 化しない。
- [x] `server/shared/schemas.py` を作り、LLM / VLM に要求する schema を定数として一覧化する。
- [x] schema は v1 の失敗を踏まえ、2-3 key 程度の小さい形を基本にする。
- [x] JSON が崩れやすい VLM 用に fixed-line output parser の schema も用意する。
- [x] DTO round-trip / default value / slots 指定の unit test を追加する。

### 完了条件

- [x] DTO がすべて `server/shared/models.py` にある。
- [x] hot loop 以外の層間受け渡しで primitive を直接流さない方針が test / docs に残る。
- [x] `pytest -m unit tests/unit/test_v2_models.py` が通る。

## Phase V2.2: v2 database schema

v1 と異なる DB schema を additive に作る。

### 実装手順

- [x] `docker/postgres/init/100_v2_core.sql` を作る。
- [x] `v2_process_heartbeats` を作る。
- [x] `v2_stt_observations` を作る。partial / final event 材料を append-only で保存する。
- [x] `v2_utterances` を作る。tomoko-process が採用した durable user utterance を保存する。
- [x] `v2_conversation_sessions` を作る。無音期間に基づく session boundary を保存する。
- [x] `v2_prompt_requests` を作る。tomoko-process から hot-path-process への prompt execution request を保存する。
- [x] `v2_model_output_events` を作る。LLM delta / complete / discard / error を保存する。
- [x] `v2_audio_output_events` を作る。TTS chunk / playback command / stop を保存する。
- [x] `v2_floor_observations` を作る。VAD / VAP / playback / user speaking / tomoko speaking を保存する。
- [x] `v2_speech_decisions` を作る。なぜ話す / 待つ / 短く反応する / 止まると判断したかを保存する。
- [x] `v2_context_snapshots` を作る。
- [x] `v2_candidates` を作る。source / priority / urgency / intrusion / maturity / lifecycle を持つ。
- [x] `v2_user_status_observations` を作る。
- [x] `v2_world_documents`, `v2_world_items`, `v2_world_interpretations` を作る。
- [x] `v2_session_summaries` と embedding table を作る。
- [x] `v2_eval_turns` / `v2_eval_scores` を作る。
- [x] 全テーブルに `created_at` と必要な `source_event_id` / `trace_id` を持たせる。
- [x] NOTIFY channel は `v2_stt_observation`, `v2_prompt_request`, `v2_model_output`, `v2_candidate`, `v2_user_status`, `v2_info_ready`, `v2_summary_ready` に限定する。
- [x] NOTIFY payload は UUID 文字列だけにする。

### 完了条件

- [ ] DDL が既存 DB に再実行可能である。
- [ ] PostgreSQL integration test で全主要 table の insert / select / foreign key を確認する。
- [x] LISTEN/NOTIFY の payload が id のみであることを test で固定する。

## Phase V2.3: process runtime foundation

各 process が DB pool / heartbeat / logging / recovery polling を共通利用できるようにする。

### 実装手順

- [x] `server/shared/db.py` に `psycopg_pool.AsyncConnectionPool` ベースの DSN pool helper を作る。
- [x] `server/shared/notify.py` に id-only notify / listen helper を作る。
- [ ] LISTEN が落ちた時の recovery polling interval を process ごとに持つ。
- [x] `server/shared/process.py` に heartbeat writer と graceful shutdown helper を作る。
- [x] `server/shared/logging.py` に JSONL structured logger を作る。
- [x] process は起動時に dependency readiness を確認し、ready になるまで hot-path server を起動しない。
- [x] `make v2-hot-path`, `make v2-tomoko`, `make v2-think`, `make v2-info`, `make v2-user-status`, `make v2-summary`, `make v2-runtime`, `make v2-stop` を作る。
- [x] tmux helper は v1 の知見を使い、止める時は window へ Ctrl-C を送ってから session を落とす。

### 完了条件

- [ ] pool reuse / close の unit test が通る。
- [x] notify helper が id-only payload を強制する。
- [ ] fake process の heartbeat が DB に保存される。
- [x] `make -n v2-runtime v2-stop` が期待順序を表示する。

## Phase V2.4: browser and websocket shell

v1 UI を薄い端末として移植する。

### 実装手順

- [x] v1 `client/` から AudioWorklet / playback queue / hidden audio sink の必要部分だけ移植する。
- [x] `/ws` は 1 本だけにする。
- [x] browser は mic float32 を送る、binary audio chunk を再生する、JSON event で表示を更新するだけにする。
- [x] client-side state decision / retry / gate は入れない。
- [x] `audio_control stop` / `audio_start` / `audio_end` / transcript display / debug marker を event として扱う。
- [x] output device selector は client-only UI とし、server state へ混ぜない。
- [x] v2 hot-path-process の websocket adapter は FastAPI 境界に閉じる。

### 完了条件

- [x] websocket adapter の unit test / protocol test が通る。
- [x] client に会話状態判断が入っていないことを code review で確認する。
- [x] browser smoke で mic bytes send / audio receive / JSON receive を確認する。

## Phase V2.5: VAD, VAP, and streaming STT observations

hot-path-process が音声を受け、partial STT observation を DB に載せる。

### 実装手順

- [ ] v1 の Silero VAD wrapper と VADProcessor を移植し、pre-roll 500ms を保持する。
- [x] VAD hot loop は primitive のままにする。
- [x] Apple Speech streaming STT backend を移植し、partial / final event を扱う。
- [x] STT frontend の実験 filter は production default で OFF にする。
- [ ] MaAI / VAP tap を移植し、`p_yielding` と recommended silence ms を取得する。
- [ ] `p_yielding` は `v2_stt_observations` / `v2_floor_observations` に保存する。
- [x] VAP hybrid は min 150 / delta 650 / threshold 0.90 を config に出す。
- [x] VAP が無い環境では固定 VAD にフォールバックする。
- [ ] partial observation insert 後、`v2_stt_observation` に observation id を NOTIFY する。
- [ ] final event は hot-path の材料 table に append し、durable utterance 化は tomoko-process が行う。

### 完了条件

- [x] pre-roll が発話先頭へ連結される unit test が通る。
- [x] Apple Speech streaming partial の unit / smoke test が通る。
- [x] VAP p_yielding が observation に保存される test が通る。
- [x] `pytest -m unit` が通る。

## Phase V2.6: tomoko-process session and floor core

tomoko-process が final STT event を採用し、会話 session と床状態を持つ。

### 実装手順

- [x] `server/tomoko/main.py` を作る。
- [ ] `v2_stt_observation` を LISTEN し、final event を durable utterance として `v2_utterances` に保存する。
- [x] 無音期間を元に `v2_conversation_sessions` を開始・継続・終了する。
- [x] v2 は「turn」を主概念にせず、utterance / floor / session / prompt request を主概念にする。
- [x] floor state は `listening`, `user_speaking`, `tomoko_speaking`, `holding`, `idle_gap` のように明示する。
- [x] VAD / VAP / p_yielding / playback / user status / candidate pressure を `FloorSignal` として合成する。
- [x] LLM を使わない `SpeechDecisionModel` を作る。
- [x] decision は `silence`, `prepare_only`, `short_reaction`, `full_reply`, `initiative`, `hold_floor`, `yield_floor`, `stop` のいずれかに分類する。
- [ ] decision と score breakdown を `v2_speech_decisions` と JSONL に保存する。
- [x] 初期段階では `short_reaction`, `initiative`, `hold_floor` は log-only にする。

### 完了条件

- [ ] final STT event から durable utterance が保存される integration test が通る。
- [x] session boundary が無音期間で発行される unit test が通る。
- [x] SpeechDecisionModel の代表ケース test が通る。
- [x] log-only decision が hot-path 発話を変えないことを test で固定する。

## Phase V2.7: context cache and prompt builder

tomoko-process が prompt request を作る。

### 実装手順

- [x] `ContextSnapshotBuilderV2` を作る。
- [x] recent utterance / session summary / memory hit / calendar map / user status / candidates を読む。
- [x] calendar は info-aquire-process が DB に入れ、tomoko-process は 1 分ごとの DTO map として memory に持つ。
- [x] 時計質問に calendar context を入れない。
- [x] stable context は prompt 前半、current utterance は明示位置、volatile recall は後半に置く。
- [x] 過去 user 履歴は raw text を使い、prompt content を自己増殖させない。
- [x] prompt section は v1 の圧縮表現を継承する。
- [x] task context は v2 初期では入れない。
- [ ] prompt request を `v2_prompt_requests` に保存し、`v2_prompt_request` へ id を NOTIFY する。
- [x] prompt build trace と elapsed ms を保存する。

### 完了条件

- [x] prompt snapshot test が通る。
- [ ] same-session cache hit / session change cache miss の unit test が通る。
- [ ] prompt build が budget 内に収まる microbench を残す。

## Phase V2.8: hot-path model execution and TTS

hot-path-process が prompt request を受けて LLM / TTS / audio output を実行する。

### 実装手順

- [ ] `v2_prompt_request` を LISTEN する model executor を hot-path-process 内に作る。
- [x] メイン会話 LLM は Gemma 4 26B A4B + dflash + MTP draft を基準にする。
- [ ] model は process 起動時に preload / warm-up する。
- [ ] KV cache / prefill API は backend 内に閉じ、turn/utterance 相当の request scope で破棄する。
- [x] LLM delta を `v2_model_output_events` と websocket text event に流す。
- [x] TTS は VOICEVOX chunked complete-WAV-per-chunk contract を維持する。
- [x] segment_length は 0.6 秒を基準にし、途切れと first audio の両方を測る。
- [x] raw PCM は client に送らない。
- [x] client disconnect は通常終了として扱い、以後の audio chunk / audio_end / reply_done 送信を止める。
- [ ] `audio_control stop` を受けたら TTS queue と playback を停止する。

### 完了条件

- [x] fake backend で prompt request -> model output -> TTS chunk -> websocket send の unit test が通る。
- [x] VOICEVOX chunked contract test が通る。
- [ ] first content / first audio / total latency を `_docs/latency.md` に記録する。

## Phase V2.9: short reaction lane

0.5 秒以内に「何か返ってくる」体感を作る。

### 実装手順

- [ ] Gemma 4 E2B OptiQ adapter-loaded backend を short reaction lane として定義する。
- [x] short lane prompt は `EMOTION:<label>` と 1 文短文を強制する。
- [x] proposal は `backchannel`, `short_confirmation`, `light_ack`, `wait_signal` に限定する。
- [ ] short lane は main reply と別 request とし、tomoko-process の decision が許した時だけ起動する。
- [ ] short reaction が出た後も、main 26B reply を並行生成できるようにする。
- [ ] main reply が不要になった場合は discard する。
- [x] short reaction が誤っていた場合、後続 partial / final / stop で stale discard する。
- [ ] まず log-only + offline replay で発火点を確認し、次に音声なし dry-run、最後に TTS を有効化する。

### 完了条件

- [x] short lane format validation test が通る。
- [ ] warmed short lane で first content / first audio が 500ms 以内を狙えるか測る。
- [ ] 誤発火時に discard / stop できる unit test が通る。
- [ ] main reply lifecycle と競合しないことを integration test で確認する。

## Phase V2.10: initiative motivation production model

offline initiative sandbox の知見を production に移す。

### 実装手順

- [x] v1 の `initiative_policy` と `v2-alpha` sandbox を読み、production 用 `InitiativeMotivationModel` を作る。
- [x] curiosity / teasing / attachment / unspoken / candidate / floor / intrusion / rejection の pressure を EMA で持つ。
- [ ] pressure は DB に保存し、process restart 後に復元できるようにする。
- [ ] candidate pressure は `v2_candidates` から読む。
- [x] user presence / screen context / silence_sec / p_yielding / fusion score を speakability 入力にする。
- [x] 初期は log-only で `would_initiate` と score breakdown を保存する。
- [x] `make v2-initiative-sim` で直近 session を replay し、production model と sandbox model の差分を見る。
- [ ] 人間が report を見て納得するまで、実発話には使わない。

### 完了条件

- [x] offline replay で fire marker が再現できる。
- [ ] pressure gain を変えると fire marker が変わる unit / browser-side test が通る。
- [ ] 実会話 log-only で唐突な発火候補を検出できる。

## Phase V2.11: user-status-aquire-process

画面と人間状態を v2 DB に保存する。

### 実装手順

- [x] macOS screenshot capture provider を作る。
- [x] Apple Vision OCR または利用可能な OCR backend を調査し、まず OCR text を DB に保存する。
- [x] macOS front app / window title / Chrome title / URL を補助 metadata として保存する。
- [x] camera presence は opt-in にし、初期は `present` / `absent` の 2 値だけを返す。
- [x] raw screenshot / camera frame は短命 artifact にし、retention を持つ。
- [x] screen activity は VLM だけに頼らず、OCR / OS metadata merge を基本にする。
- [x] YouTube など動画視聴の判定は初期では画像理解に頼らず、OCR / title / URL で判断する。
- [x] latest-frame 優先にし、古い backlog は skip する。
- [ ] `v2_user_status_observations` に保存し、id を NOTIFY する。

### 完了条件

- [x] screenshot + OCR + OS metadata の unit / smoke test が通る。
- [x] raw artifact retention test が通る。
- [ ] user status observation が tomoko-process の decision input に入る。
- [ ] hot path latency に影響しないことを確認する。

## Phase V2.12: info-aquire-process

calendar と world information を v2 DB に保存する。

### 実装手順

- [x] Google Calendar import を v2 table へ移植する。
- [x] private iCal URL は git に入れない。
- [x] calendar event は tomoko-process が 1 分ごとの DTO map として読める形にする。
- [ ] world observation は v1 の operator / raw artifact / normalizer / interpretation の境界を再利用する。
- [ ] info-aquire-process は Tomoko DB への validated artifact / interpretation write を所有する。
- [ ] think-process から research request を受け、完了後に interpretation id を NOTIFY する。
- [x] low confidence / stale / sensitive / private / do_not_speak は candidate 化しない。

### 完了条件

- [ ] calendar import integration test が通る。
- [ ] world observation dry-run / ingest / interpretation integration test が通る。
- [x] `make v2-info-once` で calendar / world の更新ができる。

## Phase V2.13: summary-process and memory index

会話原本から summary / embedding / memory index を作る。

### 実装手順

- [ ] session close または一定無音後に summary job を作る。
- [x] summary LLM は Gemma 4 31B + dflash + MTP draft を基準にする。
- [x] summary はキーワード + 結論 1 文を基本にする。
- [x] summary は原本ではなく索引として扱い、会話復元には durable utterance を残す。
- [ ] embedding は summary と utterance の両方に付ける。
- [ ] persona lexicon / state は versioned JSONB snapshot として扱う。
- [x] summary failure は hot path に影響させない。

### 完了条件

- [ ] closed session -> summary -> embedding の integration test が通る。
- [ ] summary prompt snapshot test が通る。
- [ ] summary が prompt context に入りすぎない budget test が通る。

## Phase V2.14: think-process candidate generation

記憶・外界・画面・会話から「言いたいこと」を candidate に積む。

### 実装手順

- [x] `CandidateSeed` / `CandidateRecord` store を実装する。
- [ ] conversation summary embedding と current context を結びつけて remember candidate を作る。
- [ ] world interpretation と current session を結びつけて candidate を作る。
- [ ] user-status observation から screen context candidate を作る。
- [x] calendar reminder は deterministic source として作る。
- [ ] timer / alarm は v1 の知見どおり、通常 candidate ではなく due notification として強い扱いにする設計を別 Phase まで保留する。
- [x] dedupe は context_tags / source_key / time window で行う。
- [x] candidate は tomoko-process の final decision を通るまで発話しない。

### 完了条件

- [x] candidate generation unit test が通る。
- [x] duplicate candidate が抑制される。
- [x] active / expired / spoken / dismissed lifecycle が保存される。
- [x] initiative simulation に candidate が出る。

## Phase V2.15: prompt execution lifecycle and cancellation

早期推論、破棄、停止、client disconnect を一貫して扱う。

### 実装手順

- [x] `PromptRequest` に `scope`, `decision_id`, `utterance_id`, `candidate_id`, `priority`, `cancel_policy` を持たせる。
- [x] provisional / short / main / initiative / follow-up を request scope で分ける。
- [x] 新しい partial / final / stop / user speaking により stale になった request を discard する。
- [ ] discard した LLM / TTS / audio output は JSONL と DB に残す。
- [x] hot-path-process は tomoko-process の cancel request を待たず、自分で gate しない。
- [x] cancel は idempotent にする。
- [x] client disconnect 後の send は通常終了として扱う。

### 完了条件

- [ ] provisional reply が final transcript と整合した時だけ promote される。
- [x] intent divergence / semantic split / user stop で discard される。
- [ ] cancel が TTS queue と playback command まで届く。

## Phase V2.16: floor-holding consecutive utterances

畳み掛けるような連続発話を、まず simulation と log-only で作る。

### 実装手順

- [x] `scripts/v2_floor_bench.py` を作り、Tomoko 発話後の user reaction / silence シナリオを生成する。
- [x] HOLDING 状態機械を純関数として実装する。
- [x] pause_ms は 600 / 800 / 1000 / 1200 / 1500ms を比較する。
- [x] hold_score は desire / floor availability / fatigue / stop pressure で計算する。
- [x] tomoko-process に `holding` floor state を log-only で追加する。
- [x] 発話完了後、ユーザーが床を取りに来なければ「続けていたはず」を記録する。
- [x] user speaking が入ったら即 yield し、追撃候補を破棄する。
- [x] log-only で誤続行率を見てから実発話を有効化する。
- [x] 連続発話は最大 N 回 / 合計 T 秒で hard cap する。

### 完了条件

- [x] bench report で誤続行率 / 譲りすぎ率 / pause 分布が見える。
- [x] holding state transition unit test が通る。
- [ ] 実会話 log-only で「続けていたはず」イベントを分析できる。

## Phase V2.17: follow-up generation while speaking

自分が話している間に次の一手を先読みする。

### 実装手順

- [x] Tomoko 発話再生開始時に follow-up candidate generation を背景タスクで開始する。
- [x] 入力は直前の自分の発話列 + ユーザー無反応条件 + candidate context にする。
- [x] 出力は短い追撃候補に限定する。
- [x] ユーザーが反応したら follow-up queue を破棄する。
- [ ] TT-v2.10c の prefill 知見を活かし、再生中に context を温める。
- [x] follow-up generation は E2B 級の速い model を優先する。

### 完了条件

- [x] follow-up queue lifecycle unit test が通る。
- [x] user reaction で discard される。
- [ ] HOLDING 有効化後に pause 内で再生可能な latency を測る。

## Phase V2.18: bounded stop and obedience arbitration

自然言語の「黙って」系指示を、無条件ルールから有界な裁定へ格上げする。

### 実装手順

- [x] stop intent classifier を移植する。
- [x] stop 指示の強さを `soft`, `normal`, `hard` に分ける。
- [x] compliance pressure を EMA で管理する。
- [x] 初回だけ desire が十分高ければ一手通す余地を持たせる。
- [x] 2 回目以降は必ず obey になる重み制約を unit test で固定する。
- [x] 明示 UI stop / system stop は裁定せず無条件に従う。
- [x] 裁定結果は obey_score / desire_score / pressure と一緒に log-only で保存する。
- [x] log-only で観測してから実挙動へ昇格する。

### 完了条件

- [x] 2 回目 stop で必ず止まる test が通る。
- [x] 明示 stop command が無条件に効く。
- [x] 不従順後の compliance pressure 残効が test される。

## Phase V2.19: evaluation logging

体験品質を後から評価できるログを作る。

### 実装手順

- [x] `logs/evals/*.jsonl` または `v2_eval_turns` に機械メトリクスを保存する。
- [x] `speech_end_to_first_text_ms`, `speech_end_to_first_audio_ms`, `turn_total_latency_ms` を保存する。
- [x] VAD / STT / LLM / TTS / context / playback / decision breakdown を保存する。
- [x] 人間評価の JSON import path を作る。
- [x] evaluation report で responsiveness / attended_feeling / turn_taking_naturalness / interruption_robustness / memory_naturalness / persona_consistency / recovery_quality を見られるようにする。
- [x] 単一指標ではなく、false participation / echo reaction / memory mismatch も同時に見る。

### 完了条件

- [x] 1 session の machine log と human score を join できる。
- [x] latency regression と quality regression を同じ report で見られる。

## Phase V2.20: live runtime acceptance

v2 を日常会話できる状態にする。

### 実装手順

- [x] `make v2-runtime` で hot-path / tomoko / info / user-status / summary / think を起動する。
- [x] readiness check で DB / LLM / VOICEVOX / Apple Speech / optional OCR が確認できる。
- [ ] browser から会話し、発話、short reaction、main reply、initiative log-only、user status observation が同じ session に紐づく。
- [x] `make v2-report-latest` で最新 session の timeline を HTML で確認できる。
- [ ] v1 との比較として first audio / false participation / interruption / memory naturalness を記録する。
- [ ] master へ merge / push する前に unit / integration / focused perf を通す。

### 完了条件

- [ ] 10 分以上の live conversation smoke で process crash がない。
- [ ] first audio の P50 / P95 が `_docs/latency.md` に残る。
- [ ] user stop / client disconnect / model error / TTS error から復帰できる。
- [x] v2 の次 Phase が `LOG.md` に明記される。

## 後回しにすること

- [ ] v2 初期から task ledger を入れること。
- [ ] 汎用 GPU inference queue を入れること。
- [ ] raw image を online conversation prompt へ直接入れること。
- [ ] REST endpoint を増やすこと。
- [ ] client-side state decision を増やすこと。
- [ ] LLM に floor control の最終判断を任せること。
- [ ] v1 の `server/session.py` 相当の巨大 monolith をそのまま root へ戻すこと。
