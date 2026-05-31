# Tomoko

Tomoko は、ローカル推論で動く、記憶と人格を持つ一人用の音声対話 runtime です。

ブラウザから届くマイク音声を 1 本の WebSocket `/ws` で受け取り、VAD / STT / 参加判定 /
会話生成 / TTS / 再生制御をサーバー側に集約して、声で返答します。会話原本、会話セッション、
要約、人格 snapshot、自発発話候補、日記材料、外部観察の解釈は PostgreSQL に保存します。

これは商品化されたチャットボットではなく、個人環境で「そこにいる感じ」を作るための実験的な
runtime です。セットアップやモデル選定は重めですが、その代わり体験品質、ローカル性、
状態所有の明確さを優先しています。

## Core Principles

- 通信は原則 1 本の WebSocket `/ws` に集約する
- ブラウザは薄い入出力端末にし、状態判断をクライアントへ逃がさない
- 会話制御の最終 owner は `server/session.py` の `TomoroSession`
- 会話と記憶の source of truth は PostgreSQL
- 会話 hot path は lean に保ち、要約・人格更新・候補生成・日記・外部観察解釈は background worker へ逃がす
- LLM / STT / TTS / embedding は `InferenceRouter` と config で差し替える
- 音声 hot path では余計な wrapper を作らず、境界で DTO 化する
- 新しい抽象は ownership / ordering が明確になる場合だけ足す

詳細な設計判断は [ARCHITECTURE.md](ARCHITECTURE.md)、実装計画は [PLAN.md](PLAN.md)、
作業履歴は [LOG.md](LOG.md)、確定判断は [MEMORY.md](MEMORY.md) を参照してください。

## What Works Now

- ブラウザの AudioWorklet から float32 音声 chunk を `/ws` に送る
- Silero VAD で発話区間を切る
- Apple Speech / Whisper MLX / WhisperKit / faster-whisper などの STT backend を切り替える
- transcript filter、wake word、attention mode、follow-up gate で参加判定する
- LM Studio / MLX / Ollama 系 backend で会話返答を streaming 生成する
- emotion 行を表示・立ち絵更新の信号として扱う
- VOICEVOX / Kokoro / Irodori / say などの TTS backend で音声を返す
- playback telemetry、barge-in、stop intent、turn-taking worker で割り込みと停止を扱う
- `conversation_sessions` と `conversation_logs` で会話のまとまりと原本 turn を保存する
- session summary、turn embedding、persona lexicon / state snapshot を background worker で更新する
- thinker / journalist / world observation pipeline から自発発話候補や日記材料を作る
- MaAI tap で会話中の backchannel suggestion を受け、`gesture_audio` lane として通常 turn から分離して鳴らす
- `logs/server-debug.log` と `logs/backend-trace.jsonl` で STT / LLM / TTS / playback / gate を切り分ける

## Runtime Shape

```text
Browser
  AudioWorklet
  WebSocket /ws
  JSON events + binary audio
  playback telemetry
        |
        v
server.edge.main
  websocket adapter
  VAD / STT / transcript filter
  debug recording
  MaAI audio tap
        |
        v
TomoroSession
  attention mode
  conversation session lifecycle
  participation / follow-up gate
  playback / barge-in / stop intent
  candidate / arrival final gate
  turn-taking final control
  output lane policy
        |
        +--> ContextSnapshotBuilder
        |      recent turns
        |      session summaries
        |      memory hits
        |      calendar slice
        |      lexicon / persona slices
        |
        +--> InferenceRouter
        |      conversation LLM
        |      memory extraction
        |      summary / candidate / diary roles
        |      STT / TTS / embedding
        |
        +--> GestureAudioEmitter
               MaAI backchannel as gesture_audio

PostgreSQL
  ambient_logs
  conversation_sessions
  conversation_logs
  conversation_embeddings
  persona_lexicon_versions
  persona_state_versions
  utterance_candidates
  arrival_candidates
  diary_entries
  stop_intent_observations
  world_observation_*
  calendar_events

background-process/
  summarize_pending_sessions.py
  embed_conversation_turns.py
  update_persona_snapshots.py
  run_thinker.py
  run_journalist.py
  run_turn_taking_worker.py
  ingest_world_observations.py
  interpret_world_observations.py
  import_gcal.py
```

## Output Lanes

Tomoko の出力は「人間発話への返答」だけではありません。MaAI 相槌や将来の割り込み発話を混ぜると、
音声を鳴らすこと、床を取ること、conversation log に保存すること、次の入力判定へ戻すことは別責務になります。

現行の `OutputLane` は次の意味で読みます。

| lane | 意味 | turn audio | conversation log |
|---|---|---:|---:|
| `reply_turn` | user transcript への通常返答 | yes | yes |
| `initiative_turn` | thinker / arrival candidate からの自発発話 | yes | yes |
| `interrupting_turn` | 将来の、人間発話中に床を取る発話 | yes | yes |
| `gesture_audio` | MaAI 相槌など、会話 turn ではない音声 gesture | no | no |
| `stop_ack` | stop / interrupt への短い ack | no | no |

`AudioTurnController` が扱うのは turn audio だけです。`gesture_audio` は `GestureAudioEmitter` が
TomoroSession の read-only snapshot を見て release し、`audio_start` / `audio_end`、通常 playback state、
echo grace、conversation log には混ぜません。

## TomoroSession Boundary

`server/session.py` の `TomoroSession` は、当面 1 枚の stateful control core として維持します。
すぐに `server/session/` package へ戻す対象ではありません。

TomoroSession が所有するもの:

- `/ws` 由来の audio / transcript / playback telemetry / client lifecycle の受け口
- `attention_mode`、VAD state、playback state、turn id、active conversation session id
- conversation session の開始・終了と user / tomoko turn の保存順序
- participation / candidate / arrival / turn-taking / barge-in / stop-intent の最終 gate
- context build、LLM reply、TTS、WebSocket send を起動する順序
- stale result discard、reply task、TTS queue、playback timing
- output lane と conversation log 保存 policy

外へ出してよいものは、ownership が明確な helper / state holder に限ります。

- `server/session_latency.py`: latency probe state
- `server/session_carryover.py`: retrieved context carryover
- `server/session_payloads.py`: JSON-safe payload / playback payload coercion
- `server/session_candidate_policy_helpers.py`: candidate policy payload shaping
- `server/session_key_helpers.py`: candidate request id formatter
- `server/session_memory_helpers.py`: session summary / context snapshot の memory 整形
- `server/gateway/gesture_audio.py`: MaAI gesture audio release
- `server/gateway/audio_turn.py`: turn audio ownership

当面やらないこと:

- `server/session.py` の大規模 package split
- dispatcher / effects / event_runner / maps / OutputDemand / Watcher の復活
- DB write ordering、reply orchestration、TTS/audio hot path、candidate final gate、conversation lifecycle を巻き込む抽出

分割する場合は PLAN.md に専用 Phase を立て、characterization test で現状挙動を固定し、
1 Phase 1 責務で進めます。

## Default Backends

`config/central_realtime.toml` の現在の default は次の構成です。

| 役割 | backend | 補足 |
|---|---|---|
| 会話 LLM | `lmstudio_gemma4_26b_a4b` | LM Studio OpenAI 互換 API の `gemma-4-26b-a4b-it-mlx` |
| 会話 fallback | `local_gemma4_e2b_mlx` | MLX の `mlx-community/gemma-4-e2b-it-4bit` |
| session summary | `lmstudio_gemma4_26b_a4b` | online hot path ではなく background |
| short memory extraction | `lmstudio_gemma4_31b` | post-reply background lane |
| STT | `local_whisper_mlx_large_turbo_q4` | MLX Whisper large turbo q4 |
| STT 比較候補 | `local_apple_speech_ja` | macOS Speech framework |
| STT 比較候補 | `local_whisperkit_serve_large_turbo_632m_cpu_ne` | WhisperKit serve + CPU/ANE |
| VAD | `silero_vad` | 16kHz / 32ms chunk |
| TTS | `voicevox_tsumugi` | VOICEVOX Engine speaker id 8 |
| TTS 比較候補 | `kokoro_mlx` | local MLX TTS |
| embedding | `local_bge_m3` | `BAAI/bge-m3` / 1024 dimensions |

LM Studio は `http://192.168.11.66:1234` の OpenAI 互換 API を想定しています。
別マシンや別ポートで動かす場合は config の backend URL を変更してください。

VOICEVOX を使う場合は、VOICEVOX Engine が `http://127.0.0.1:50021` で応答している必要があります。
Apple Speech STT を使う場合は macOS の Speech Recognition permission が必要です。

## Setup

必要なもの:

- Apple Silicon Mac 推奨
- mise
- Docker / Docker Compose
- LM Studio
- VOICEVOX Engine
- macOS Speech Recognition permission

初回セットアップ:

```bash
make deps
make db-up
make download-models
make prepare
```

optional / custom license を含むモデルを明示的に取得する場合:

```bash
make download-optional-models
```

通常起動:

```bash
make server
```

ブラウザで開く:

```text
http://127.0.0.1:8000
```

開発中の debug 起動:

```bash
make server-debug
```

`make server-debug` は DEBUG ログを `logs/server-debug.log` に追記します。会話品質や遅延を調べるときは、
まずこのファイルと `logs/backend-trace.jsonl` を見ます。

複数 worker をまとめて起動する screen helper もあります。

```bash
make screen-runtime       # server, turn-taking, thinker, summarizer, embedder, persona updater
make screen-runtime-full  # 上記 + journalist + information interpretation
make screen-attach
make screen-stop
```

## Common Commands

```bash
make deps                    # uv sync
make db-up                   # PostgreSQL 起動
make db-stop                 # PostgreSQL 停止
make db-dump                 # logs/db-dumps/ に pg_dump
make prepare                 # 現行 config の起動前準備
make server                  # runtime 起動
make server-reload           # reload 付き runtime
make server-debug            # DEBUG ログ付き runtime
make log-report              # server-debug.log から HTML report
make monitor                 # local monitor dashboard
make system-monitor          # mactop headless 由来の GPU pressure JSONL sampler
make lint                    # ruff check .
make test-unit               # pytest -m unit
make check                   # lint + unit
```

Background workers:

```bash
make session-summarizer
make session-summarizer-once
make turn-embedder
make turn-embedder-once
make persona-seed-initial
make persona-updater
make persona-updater-once
make thinker
make thinker-once
make journalist
make journalist-once
make turn-taking-worker
make turn-taking-worker-once
make information-ingest-once
make information-ingest-dry-run
make information-interpret
make information-interpret-once
make gcal
make background-once
make background-dry-run
```

Smoke / perf:

```bash
make bench-stt
make soak-stt
make soak-voice-stack
make smoke-maai-tap
make smoke-maai-real
make smoke-maai-dialogue
make smoke-maai-material
```

`make soak-stt` は Ctrl-C まで走る長時間 soak です。有限確認だけなら `make bench-stt` や
`_tools/soak_voice_stack_scenarios.py --max-cycles 1` を使います。

GPU pressure を latency log と同じ時間軸で見る場合は、別 terminal で次を起動します。

```bash
make system-monitor
make monitor
```

`system-monitor` は optional provider として mactop v2 の `--headless --count` JSON を呼び、
`logs/system-metrics.jsonl` に GPU active%、GPU power、GPU frequency、ANE power、memory、thermal を保存します。
mactop が未インストールでも runtime は壊さず、`available=false` sample として記録します。

## Background Data Pipelines

### Conversation Memory

`conversation_logs` は user / tomoko turn の原本です。会話のまとまりは `conversation_sessions` が持ちます。
session summary と embedding は原本ではなく、検索と文脈復元のための派生データです。

主な worker:

- `summarize_pending_sessions.py`: closed session を要約し、summary embedding を作る
- `embed_conversation_turns.py`: turn-level embedding を補完する
- `update_persona_snapshots.py`: persona lexicon / state の versioned JSONB snapshot を更新する

### Thinker / Journalist

`run_thinker.py` は diary、world observation、time-based source などから `utterance_candidates` と
`arrival_candidates` を作ります。TomoroSession は ambient / idle / playback / floor policy を見て、
候補を発話してよいか最終 gate します。

`run_journalist.py` は会話・候補・外部観察から日記材料を作ります。日記は runtime の原本ではなく、
Tomoko の内面ログ・回想材料として扱います。

### World Observations

`informations/work` に置いた raw Markdown は、直接会話 prompt へ流しません。

```text
raw Markdown artifact
  -> validator / normalizer
  -> world_observation_documents / items / interpretations
  -> thinker / journalist consumption
```

raw artifact は source of truth ではなく、DB に保存された validated interpretation を runtime が参照します。
integration test では共有DBの global topN に fixture が入ることを前提にせず、作った fixture id を直接確認します。

### Google Calendar

Google Calendar 取り込みでは private iCal URL を git に入れません。
`config/gcal_urls.example.txt` を `config/gcal_urls.txt` にコピーし、1 行 1 URL で private iCal URL を置きます。
`config/gcal_urls.txt` は gitignore 済みです。

```bash
make gcal
```

予定は PostgreSQL の `calendar_events` に保存され、会話中の deep context だけが DB から予定を読みます。

## Data And Logs

重要な DB テーブル:

- `ambient_logs`: 会話に参加しなかった観測発話
- `conversation_sessions`: 会話のまとまり、summary、summary embedding
- `conversation_logs`: user / tomoko の原本 turn
- `conversation_embeddings`: turn-level embedding
- `persona_lexicon_versions`: 用語集・関係性 marker の versioned JSONB snapshot
- `persona_state_versions`: 人格状態の versioned JSONB snapshot
- `utterance_candidates`: Tomoko が自分から話す候補
- `arrival_candidates`: 入室・接続時の一言候補
- `diary_entries`: 日記・内省材料
- `stop_intent_observations`: stop / interrupt の観測
- `world_observation_documents`, `world_observation_items`, `world_observation_interpretations`: 外部観察
- `calendar_events`: iCal 由来の予定

主なログ:

- `logs/server-debug.log`: 状態遷移、transcript、reply、playback、candidate、turn-taking の人間向けログ
- `logs/backend-trace.jsonl`: backend call の JSONL trace
- `logs/system-metrics.jsonl`: mactop headless 由来の GPU / power / memory / thermal sample
- `logs/thinker.log`: candidate generation
- `logs/session-summarizer.log`: session summary worker
- `logs/turn-embedder.log`: turn embedding worker
- `logs/persona-updater.log`: persona snapshot worker
- `logs/journalist.log`: diary worker
- `logs/turn-taking-worker.log`: turn-taking worker
- `logs/world-observations.log`: external observation ingest / interpretation

`backend-trace.jsonl` の例:

```bash
jq 'select(.trace=="tomoko_backend_call" and .role=="conversation")' logs/backend-trace.jsonl
jq 'select(.trace=="tomoko_backend_call" and .kind=="tts")' logs/backend-trace.jsonl
jq 'select(.trace=="tomoko_backend_call" and .kind=="stt")' logs/backend-trace.jsonl
```

## Tests

Unit test は常に通す前提です。

```bash
make lint
make test-unit
make check
```

Integration / perf は local PostgreSQL、実モデル、macOS backend に依存します。

```bash
.venv/bin/pytest -m integration -q
.venv/bin/pytest -m perf --tb=short -q
```

直近の確認例:

```text
.venv/bin/pytest -m unit -q
508 passed, 17 deselected

.venv/bin/pytest -m integration -q
9 passed, 516 deselected
```

perf の `say` backend latency test は macOS の実行タイミングで 800ms 閾値をまたぐことがあります。
単発の失敗は `logs/backend-trace.jsonl` と再実行で jitter か実装破損かを切り分けます。

## Current Development Posture

| 領域 | 状態 | 補足 |
|---|---|---|
| M1: 話せる Tomoko | 実装済み | `/ws`、VAD、STT、LLM streaming、emotion、TTS、ブラウザ再生 |
| M2: 記憶がある Tomoko | 実装済み | session、summary、embedding、persona JSONB snapshot、ContextSnapshotBuilder |
| M3: 自分から話す Tomoko | 実装中 | thinker / arrival candidate と initiative lane は成立。自然さは継続調整 |
| M4: インフラ安定化 | 一部実装 | backend trace、edge split scaffold、turn-taking worker、monitor、smoke |
| M5: 家族の Tomoko | 未着手 | 複数部屋・複数人の本格運用はまだ先 |

直近の注力点:

- MaAI 相槌を `gesture_audio` lane として通常 reply / playback / echo 判定から分離する
- user speech が続いている時の未完 transcript を通常 reply にしない
- output lane / floor ownership を明示し、将来の `interrupting_turn` を別 Phase で扱える形にする
- shared DB に依存する integration test を fixture id 直接確認へ寄せる
- 実ブラウザ会話では `server-debug.log` と `backend-trace.jsonl` で STT / LLM / TTS / playback の支配要因を分ける

## Reference Code

`_reference/` は過去実装の経験を残す場所です。現行実装へそのまま取り込むためのコードではありません。

- `_reference/unity/MyAIRoomScript.cs`: Unity 版の音量閾値 VAD、録音状態、OGG 送信、分散した状態 flag
- `_reference/server/api.py`: Base64 / OGG to MP3 / REST 一括返却の旧サーバー

Tomoko の現行設計は、これらで苦しかった点の逆をやっています。

- 音声は float32 chunk として WebSocket で流す
- REST の一括 API ではなく `/ws` streaming で扱う
- クライアントに状態判断を置かず、`TomoroSession` に集約する
- TTS / playback / barge-in / stop は turn id / chunk id / telemetry で扱う

## License

Tomoko 本体のコードは MIT License です。モデル重み、VOICEVOX、外部ライブラリはそれぞれのライセンスに従います。
モデル重みは repository に同梱せず、ユーザー操作で取得します。

代表的な依存と注意点:

- Whisper / WhisperKit / faster-whisper: MIT
- Kokoro-82M: Apache-2.0
- BGE-M3: MIT
- Irodori-TTS v3: MIT
- Qwen / Gemma 系の現在の設定対象: Apache-2.0
- LFM2.5: `lfm1.0` custom model license
- Supertonic-3 CoreML: OpenRAIL-family license
- VOICEVOX Engine: LGPL v3 / 別ライセンス。Tomoko は起動済み Engine へ HTTP で接続する
- psycopg: LGPL-3.0-only dependency

Copyright (c) 2026 modeverv

## Made With LLM

This project is made with LLMs and worked through
[llm-orchestrator](https://github.com/modeverv/llm-orchestrator).
