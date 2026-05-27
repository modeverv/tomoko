# Tomoko

ローカル推論で動く、記憶と人格を持つ一人用の音声対話システムです。

Tomoko はブラウザのマイク入力を 1 本の WebSocket で受け取り、VAD / STT / 会話生成 / TTS /
再生制御をすべてサーバー側の状態機械に集約して、声で返答します。会話ログ、会話セッション、
要約、人格 snapshot、自発発話候補、外部観察の解釈は PostgreSQL に保存されます。

## 現在の位置づけ

このリポジトリは、商品化されたチャットボットではなく、個人環境で「そこにいる感じ」を作るための
実験的な runtime です。セットアップやモデル選定は重めですが、その代わり次の方針を優先しています。

- 音声対話の体験品質を最優先する
- 会話と記憶の source of truth をローカル PostgreSQL に置く
- ブラウザは薄い入出力端末にし、状態判断をクライアントへ逃がさない
- 会話 hot path は lean に保ち、重い解釈や要約は background worker へ逃がす
- LLM / STT / TTS backend は `InferenceRouter` 経由で差し替え可能にする
- 最終的な会話制御は `server/session.py` の `TomoroSession` に集約する

詳細な設計判断は [ARCHITECTURE.md](ARCHITECTURE.md)、実装計画と未完了項目は [PLAN.md](PLAN.md)、
セッションごとの作業履歴は [LOG.md](LOG.md)、確定判断と気づきは [MEMORY.md](MEMORY.md) を参照してください。

## できること

- ブラウザからマイク音声を float32 chunk として `/ws` に送る
- Silero VAD で発話区間を検出する
- STT backend を切り替えながら日本語音声を transcript 化する
- wake word / attention mode / follow-up に応じて参加判断する
- LM Studio / MLX / Ollama などの backend で会話返答を streaming 生成する
- emotion 行を分離し、画面表示と立ち絵切り替えに使う
- TTS chunk を WebSocket binary として返し、ブラウザで gapless に近い再生をする
- playback telemetry と barge-in / stop-intent / turn-taking judge で割り込みを扱う
- `conversation_sessions` と `conversation_logs` で会話のまとまりと原本を保存する
- session summary / embedding / persona lexicon / persona state を background worker で更新する
- thinker / journalist / world observation pipeline から自発発話候補や日記材料を作る
- `logs/backend-trace.jsonl` で STT / LLM / TTS / embedding の queue wait と first chunk を切り分ける

## 主要アーキテクチャ

```text
Browser
  AudioWorklet
  WebSocket /ws
  playback telemetry
        |
        v
server.edge.main
  WebSocket adapter
  VAD / STT / debug recording
  JSON event and binary audio I/O
        |
        v
TomoroSession
  attention mode
  conversation session lifecycle
  playback / barge-in / stop-intent
  initiative / arrival final gate
  turn-taking final control
        |
        +--> ContextSnapshotBuilder
        |      same session turns
        |      session summaries
        |      memory hits
        |      lexicon / persona slices
        |
        +--> InferenceRouter
               conversation LLM
               STT / VAD / TTS / embedding
               summary / candidate / diary roles

PostgreSQL
  ambient_logs
  conversation_sessions
  conversation_logs
  conversation_embeddings
  persona_lexicon_versions
  persona_state_versions
  utterance_candidates
  arrival_candidates
  diary
  stop_intent_observations
  world_observation_interpretations

background-process/
  summarize_pending_sessions.py
  update_persona_snapshots.py
  run_thinker.py
  run_journalist.py
  run_turn_taking_worker.py
  ingest_world_observations.py
  interpret_world_observations.py
```

設計上の制約として、新しい機能も原則 `/ws` の message type として増やします。REST endpoint を増やす前に
必ず [ARCHITECTURE.md](ARCHITECTURE.md) を確認してください。

## 現行 default backend

`config/central_realtime.toml` の現在の default は次の構成です。

| 役割 | backend | 補足 |
|---|---|---|
| 会話 LLM | `lmstudio_gemma4_26b_a4b` | LM Studio OpenAI 互換 API の `gemma-4-26b-a4b-it-mlx` |
| 会話 fallback | `local_gemma4_e2b_mlx` | MLX VLM の `mlx-community/gemma-4-e2b-it-4bit` |
| STT | `local_apple_speech_ja` | Apple Speech 比較 lane。品質・失敗分類はまだ調整中 |
| STT 比較候補 | `local_whisper_mlx_large_turbo_q4` | 実会話品質が良かった MLX Whisper lane |
| STT 比較候補 | `local_whisperkit_serve_large_turbo_632m_cpu_ne` | WhisperKit turbo 632MB + CPU/ANE lane |
| VAD | `silero_vad` | 16kHz / 32ms chunk / silence 800ms |
| TTS | `voicevox_tsumugi` | 起動済み VOICEVOX Engine の春日部つむぎ speaker id 8 |
| TTS 比較候補 | `kokoro_mlx` | first audio が速い local MLX TTS lane |
| embedding | `local_bge_m3` | `BAAI/bge-m3` / 1024 dimensions |

LM Studio は `http://192.168.11.66:1234` の OpenAI 互換 API を想定しています。
別マシンや別ポートで動かす場合は `config/central_realtime.toml` の該当 backend URL を変更してください。

VOICEVOX を使う場合は、VOICEVOX Engine が `http://127.0.0.1:50021` で応答している必要があります。
VOICEVOX を使わない比較では `tts_backend = "kokoro_mlx"` へ切り替えます。

## セットアップ

必要なもの:

- Apple Silicon Mac 推奨
- mise
- Docker / Docker Compose
- LM Studio
- VOICEVOX Engine

初回セットアップ:

```bash
make deps
make db-up
make download-models
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

## よく使うコマンド

```bash
make deps                    # uv sync
make db-up                   # PostgreSQL 起動
make db-stop                 # PostgreSQL 停止
make db-dump                 # logs/db-dumps/ に pg_dump
make server                  # runtime 起動
make server-reload           # reload 付き runtime
make server-debug            # DEBUG ログ付き runtime
make test-unit               # pytest -m unit
make lint                    # ruff check .
make check                   # lint + unit
make bench-stt               # STT latency perf
make soak-stt                # STT soak
make soak-voice-stack        # STT/TTS/LLM 横負荷 soak
```

background 系:

```bash
make session-summarizer      # pending conversation session を要約
make persona-updater         # persona lexicon/state snapshot 更新
make persona-seed-initial    # 初期 persona snapshot seed
make thinker                 # 自発発話 candidate / arrival candidate 生成
make thinker-once            # thinker を 1 回だけ実行
make journalist              # diary worker
make journalist-once         # diary worker を 1 回だけ実行
make turn-taking-worker      # local turn-taking judge worker
make turn-taking-worker-once # rule sample を 1 回実行
make information-ingest-once # informations/work の raw markdown ingest
make information-interpret   # world observation interpretation worker
```

## 開発状況

| 領域 | 状態 | 補足 |
|---|---|---|
| M1: 話せる Tomoko | 実装済み | `/ws`、VAD、STT、LLM streaming、emotion、TTS、ブラウザ再生 |
| M2: 記憶がある Tomoko | 実装済み | conversation session、summary、embedding、persona JSONB snapshot、ContextSnapshotBuilder |
| M3: 自分から話す Tomoko | 実装中 | candidate 生成と発話経路は成立。Phase 10.10/10.11 の実ブラウザ評価が残り |
| M4: インフラ安定化 | 一部実装 | backend trace、edge split scaffold、stop-intent、turn-taking worker |
| M5: 家族の Tomoko | 未着手 | 複数部屋・複数人の本格運用はまだ先 |

直近の注力点:

- Phase 10.10: 自発発話を「候補を読む」から「自然な会話の入口」へ調整する
- Phase 10.11: VAD state だけで pending reply を捨てず、rule-first + local worker で turn-taking を判定する
- 明示的な記憶想起では query embedding を共有し、session summary を優先して context build timeout を減らす
- 実ブラウザ会話で `server-debug.log` と `backend-trace.jsonl` を見ながら STT / LLM / TTS / playback のどこが体験を支配しているかを分ける

## データとログ

重要な DB テーブル:

- `ambient_logs`: 会話に参加しなかった観測発話
- `conversation_sessions`: 会話のまとまり、summary、summary embedding
- `conversation_logs`: user / tomoko の原本 turn
- `conversation_embeddings`: turn-level embedding
- `persona_lexicon_versions`: 用語集・関係性 marker の versioned JSONB snapshot
- `persona_state_versions`: 人格状態の versioned JSONB snapshot
- `utterance_candidates`: Tomoko が自分から話す候補
- `arrival_candidates`: 入室・接続時の一言候補
- `stop_intent_observations`: stop / interrupt の観測
- `world_observation_interpretations`: 外部観察 Markdown から解釈した候補材料

主なログ:

- `logs/server-debug.log`: 状態遷移、transcript、reply、playback、candidate、turn-taking の人間向けログ
- `logs/backend-trace.jsonl`: backend call の JSONL trace
- `logs/thinker.log`: candidate generation
- `logs/session-summarizer.log`: session summary worker
- `logs/persona-updater.log`: persona snapshot worker
- `logs/journalist.log`: diary worker
- `logs/turn-taking-worker.log`: turn-taking worker
- `logs/world-observations.log`: external observation ingest / interpretation

`backend-trace.jsonl` は次のように抽出できます。

```bash
jq 'select(.trace=="tomoko_backend_call" and .role=="conversation")' logs/backend-trace.jsonl
jq 'select(.trace=="tomoko_backend_call" and .kind=="tts")' logs/backend-trace.jsonl
jq 'select(.trace=="tomoko_backend_call" and .kind=="stt")' logs/backend-trace.jsonl
```

## テスト

unit test は常に通す前提です。

```bash
make test-unit
make lint
make check
```

個別に見ることが多いテスト:

```bash
.venv/bin/python -m pytest -m unit tests/unit/test_phase88_context_snapshot.py -q
.venv/bin/python -m pytest -m unit tests/unit/test_phase105_session_runtime.py -q
.venv/bin/python -m pytest -m unit tests/unit/test_phase106_initiative_policy.py -q
.venv/bin/python -m pytest -m unit tests/unit/test_turn_taking_judge.py -q
.venv/bin/python -m pytest -m unit tests/unit/test_turn_taking_worker_client.py -q
```

perf / integration はローカル middleware や実モデルに依存します。

```bash
.venv/bin/python -m pytest -m integration
.venv/bin/python -m pytest -m perf --tb=short
```

## 外部観察 pipeline

`informations/work` に置いた raw Markdown は、直接会話 prompt へ流しません。
Phase 18 では次の境界を守ります。

```text
raw Markdown artifact
  -> validator / normalizer
  -> world_observation_interpretations
  -> thinker / journalist consumption
```

raw artifact は source of truth ではなく、DB に保存された validated interpretation を runtime が参照します。

## _reference/ について

`_reference/` は過去実装の経験を残す場所です。現在の実装へそのまま取り込むためのコードではありません。

- `_reference/unity/MyAIRoomScript.cs`: Unity 版の音量閾値 VAD、録音状態、OGG 送信、分散した状態 flag
- `_reference/server/api.py`: Base64 / OGG to MP3 / REST 一括返却の旧サーバー

Tomoko の現行設計は、これらで苦しかった点の逆をやっています。

- 音声は float32 chunk として WebSocket で流す
- REST の一括 API ではなく `/ws` streaming で扱う
- クライアントに状態判断を置かず、`TomoroSession` に集約する
- TTS / playback / barge-in / stop は turn id / chunk id / telemetry で扱う

## ライセンス

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

## Made with LLM

This project is made with LLMs and worked through
[llm-orchestrator](https://github.com/modeverv/llm-orchestrator).
