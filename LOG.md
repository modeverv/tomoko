# LOG.md

## 2026-06-18 セッション8

### やること（開始時に書く）
- VOICEVOX の発話速度を 2 倍にし、Tomoko を早口にする。

### やったこと
- `VoicevoxChunkedTtsBackend` の既定 `speedScale` を `2.0` にした。
- `create_default_real_prompt_executor()` が `TOMOKO_V2_VOICEVOX_SPEED` を読み、未指定時は `2.0` を使うようにした。
- `Makefile` に `TOMOKO_V2_VOICEVOX_SPEED ?= 2.0` を追加し、`v2-llm-tts-smoke` に渡すようにした。
- `README.md` と `MEMORY.md` に v2 VOICEVOX speed 既定値を追記した。

### 詰まったこと・解決したこと
- 現在このシェルでは VOICEVOX runtime が起動していないため、実音声の聴感確認は未実施。

### 検証
- `uv run pytest tests/unit/test_v2_audio_tomoko_prompt.py::test_voicevox_audio_query_uses_configured_double_speed tests/unit/test_v2_audio_tomoko_prompt.py::test_default_real_prompt_executor_uses_voicevox_double_speed -q`
  - 2 passed

### 次のセッションでやること
- `make tmux-runtime` 後に実会話または `make v2-llm-tts-smoke` で 2 倍速の聴感を確認する。

## 2026-06-18 セッション7

### やること（開始時に書く）
- 各プロセスの tmux console を見て何が起きているかわかるよう、runtime / hot-path / audio / STT の標準出力ログを増やす。
- client UI に STT final と TTS result をタイムライン表示する。

### やったこと
- `server.runtime` の long-lived process が `process_start` / `heartbeat` / `process_stop` / `readiness` を標準出力にも出すようにした。
- hot-path `/ws` で `ws_connected`, `audio_bytes`, `client_event`, `stt_observation`, `durable_utterance`, `model_delta`, `model_complete`, `tts_result`, `audio_chunk`, `prompt_complete` を console-visible にした。
- audio conversation 境界で `vad_segment`, `stt_start`, `stt_done`, `blank_final_stt_ignored`, `prompt_built` を出すようにした。
- Apple Speech backend で `apple_speech_start` / `apple_speech_done` を出すようにした。
- `/ws` の prompt 実行結果に `tts_result` JSON event を追加し、TTSに渡した最終テキスト・chunk数・byte数を client に送るようにした。
- client UI に timeline section を追加し、STT final と TTS result を時刻付きで表示するようにした。
- client の browser console に websocket event / audio chunk / audio play を出すようにした。

### 詰まったこと・解決したこと
- `tts_result` は binary audio chunk より前に送ることで、client / test が TTS内容をテキストイベントとして確実に拾えるようにした。
- hot-path の live uvicorn `--reload` が変更を検知し、実起動中の server process も更新済み。

### 検証
- `uv run pytest tests/unit/test_v2_runtime_foundation.py::test_hot_path_websocket_uses_prompt_executor_for_text_prompt tests/unit/test_v2_runtime_foundation.py::test_client_renders_stt_and_tts_timeline -q`
  - 2 passed
- `make check`
  - unit: 38 passed, 1 deselected
  - ruff: passed
- `make v2-conversation-smoke`
  - console-visible log に `audio_bytes -> vad_segment -> stt_start/stt_done -> stt_observation -> durable_utterance -> model_complete -> tts_result -> audio_chunk -> prompt_complete` が出ることを確認
- `node --check client/main.js`
  - passed

### 次のセッションでやること
- 実ブラウザ会話で timeline と hot-path pane を見ながら、話し続ける原因が blank STT / repeated non-empty transcript / prompt repeat のどれかを特定する。

## 2026-06-18 セッション6

### やること（開始時に書く）
- ヘッドセット前提では Tomoko 発話中に mic を抑止する前回対応が barge-in を壊すため、echo suppression を戻す。
- 発話ループの別原因として、空 final STT が durable utterance / prompt request になる経路を確認し、空 STT を会話ターンにしない。

### やったこと
- セッション5の server-owned echo suppression を hot-path から外し、Tomoko 発話中も mic bytes は VAD/STT へ流れる状態に戻した。
- `TomokoProcessCore.adopt_final_observation()` で空白 final STT を durable utterance として採用しないようにした。
- blank final STT を落とした時に `blank_final_stt_ignored` を server log に残すようにした。
- unit test を「Tomoko音声中にmicを落とす」ではなく「空 final STT は prompt にならない」契約へ差し替えた。

### 詰まったこと・解決したこと
- 前回の推定は、ヘッドセット前提では barge-in を壊す過剰対策だった。
- 別原因として、VAD がノイズを speech segment として切り出し、Apple Speech が空文字を返し、その空発話を prompt へ流す経路が見つかった。

### 検証
- `uv run pytest tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 14 passed
- `make check`
  - unit: 37 passed, 1 deselected
  - ruff: passed
- `git diff --check`
  - passed
- `make v2-conversation-smoke`
  - fake runtime で通常の non-empty STT conversation path が引き続き通ることを確認
- live hot-path uvicorn `--reload` が変更を検知し、server process が再起動済みであることを確認

### 次のセッションでやること
- 実ブラウザ会話で `blank_final_stt_ignored` が出るか、または non-empty transcript が繰り返されているかを確認する。
- non-empty transcript が繰り返される場合は、STT結果・VAD RMS・segment長をログへ追加して原因を切り分ける。

## 2026-06-18 セッション5

### やること（開始時に書く）
- live runtime log で発話ループを確認し、Tomoko の TTS 出力がユーザー発話として STT/TomokoProcess に戻っているなら server-owned echo suppression を実装する。

### やったこと
- `tmux` の VOICEVOX / dflash / hot-path logs を確認し、Tomoko 応答「こんにちは。準備はできているよ...」系が連続で `audio_query` / `chat/completions` に再投入されていることを確認した。
- ループ停止のため hot-path process を一度止めた。
- `HotPathAudioConversation` に Tomoko TTS 送出 WAV の duration + grace 中だけ mic bytes を VAD 前で破棄する echo suppression を追加した。
- suppression 開始時に `VADProcessor.reset()` で pre-roll / 発話中バッファを落とし、Tomoko 音声が次の SpeechSegment に混入しないようにした。
- hot-path tmux window を復帰し、dflash / VOICEVOX / Apple Speech / OCR readiness が ready であることを確認した。

### 詰まったこと・解決したこと
- `server-debug.log` には transcript / audio_complete などの application event が出ておらず、VOICEVOX と dflash の tmux pane が実際の発話ループの証拠になった。
- client に発話判定を持たせず、server が「自分が送った音声のWAV長」を根拠に mic 入力を抑止する設計にした。

### 検証
- `uv run pytest tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 15 passed
- `make check`
  - unit: 38 passed, 1 deselected
  - ruff: passed
- `git diff --check`
  - passed
- `make v2-conversation-smoke`
  - fake runtime で `transcript -> durable_utterance -> model_delta -> model_complete -> audio_complete -> prompt_complete` を確認
- `make v2-llm-tts-smoke`
  - real dflash/VOICEVOX で `text="了解。"` / `audio_chunks=1` / first audio bytes 35372 を確認

### 次のセッションでやること
- 実ブラウザでスピーカー出力ありの状態で会話し、Tomoko 発話直後に同一応答が transcript / durable_utterance として再投入されないことを確認する。
- 必要なら `tomoko_echo_grace_ms` の 800ms を実ログで調整する。

## 2026-06-18 セッション4

### やること（開始時に書く）
- STT / TTS / OCR / LLM の実 runtime が root v2 で揃っているかを実コードで確認し、不足があれば実装する。
- hot-path-process と tomoko-process を起動して会話チェックできる状態にする。
- VAD が発話先頭へ過去チャンクを pre-roll 連結しているか確認し、不足なら実装する。
- 実装済み Phase について `PLAN.md` のチェックボックスを更新する。

### やったこと
- root v2 に Apple Speech STT sidecar runtime を追加した。
  - `scripts/apple_speech_stt/AppleSpeechSTT.swift`
  - `scripts/apple_speech_stt/Info.plist`
  - `server/audio/stt.py` の `AppleSpeechStreamingBackend`
- root v2 に Vision.framework OCR sidecar runtime を追加した。
  - `scripts/vision_ocr/VisionOCR.swift`
  - `server/user_status/ocr_runtime.py` は Vision OCR を優先し、失敗時に tesseract fallback する。
- `/ws` の音声 bytes を `HotPathAudioConversation` へ流すようにし、VAD pre-roll -> STT observation -> tomoko durable utterance -> prompt -> TTS WAV chunk の smoke 経路を作った。
- `make v2-conversation-smoke` を追加し、hot-path server と tomoko heartbeat process を実際に起動して fake audio conversation を確認できるようにした。
- `server.runtime readiness` が Apple Speech / Vision OCR availability を具体的に返すようにした。
- `README.md` / `MEMORY.md` / `_docs/latency.md` を追記した。

### 詰まったこと・解決したこと
- VAD pre-roll 自体は既に `VADProcessor(pre_roll_ms=500)` で実装済みだったが、`/ws` 音声 bytes からその経路を使っていなかった。今回 `HotPathAudioConversation` を追加して実際の `/ws` 音声経路へ接続した。
- `make v2-conversation-smoke` 初回は tomoko heartbeat process 停止時に `KeyboardInterrupt` traceback が出た。`server.runtime process` の SIGINT を通常停止ログとして扱うよう修正した。
- LLM / VOICEVOX endpoint はこの作業時点では未起動。起動 launcher は前回追加済みで、実 first content / first audio は `make tmux-runtime` 後に `make v2-llm-tts-smoke` で測る。

### 検証
- `make check`
  - unit: 35 passed, 1 deselected
  - ruff: passed
- `make v2-conversation-smoke`
  - hot-path uvicorn と tomoko heartbeat process を起動
  - fake audio bytes から `transcript`, `durable_utterance`, `model_delta`, `model_complete`, `audio_complete`, `prompt_complete` を確認
  - binary WAV chunk 1 件 / 16 bytes
- `uv run python -m server.runtime readiness`
  - Apple Speech: `binary=true`, `source=true`, `plist=true`, `swiftc=true`
  - OCR: `screencapture=true`, `vision_ocr=true`, `tesseract=true`, `osascript=true`
  - LLM `8081` / `8082`: false
  - VOICEVOX `50122`: false
- `make v2-ocr-smoke`
  - Vision-first OCR path で 2739 chars 抽出
  - metadata app `Codex`, YouTube URL/title detected
- `bash -n scripts/wait_runtime_dependencies.sh scripts/run_llm.sh scripts/run_llm_stop.sh scripts/run_voicevox.sh`
  - passed

### 次のセッションでやること
- `make tmux-runtime` で dflash / VOICEVOX を実起動し、`make v2-runtime-ready` を true にする。
- 実 runtime 起動後に `make v2-llm-tts-smoke` を実行し、first content / first audio / total latency を `_docs/latency.md` に追記する。

## 2026-06-18 セッション3

### やること（開始時に書く）
- root `Makefile` を v1 と同等の操作感に拡張する。
- v1 の `llm-run` / `voicevox-run` / readiness / tmux runtime を参照し、v2 root に VOICEVOX / dflash LLM / OCR の実 runtime launcher と readiness smoke を用意する。
- 実 runtime provider は v2 の process 分離に合わせ、hot-path 側の model executor / runtime readiness / user-status OCR に接続する。

### やったこと
- root `Makefile` を v1 の主要 target 名に合わせて拡張した。
  - `server` / `server-debug` / `gateway` / `edge-kitchen`
  - `tmux-runtime` / `run` / `stop` / `a`
  - `llm-run` / `llm-stop` / `voicevox-run` / `v2-runtime-ready`
  - background 系の v2 alias と dry-run
  - `v2-ocr-smoke` / `v2-llm-tts-smoke`
- `scripts/run_llm.sh` / `scripts/run_llm_stop.sh` / `scripts/run_voicevox.sh` / `scripts/wait_runtime_dependencies.sh` を追加した。
- dflash LLM は v1 と同じ 31B `8081` / 26B `8082` の tmux window 構成にし、26B は既定で `v1/loras/lora/fused_model` を参照する。
- VOICEVOX は v1 と同じ sibling `async-voicevox/run_streaming_voicevox.command` を既定 launcher として使う。
- `OpenAICompatibleChatBackend` と `VoicevoxChunkedTtsBackend` を `server/hot_path/model_executor.py` に追加し、fake backend ではなく実 dflash / VOICEVOX endpoint を叩けるようにした。
- `server/user_status/ocr_runtime.py` と `scripts/v2_ocr_smoke.py` を追加し、`screencapture` + `tesseract` + `osascript` による OCR / OS metadata 経路を作った。
- `server.runtime readiness` を実 URL / binary availability を見る形に変更した。
- `README.md` と `config/v2.toml` に実 runtime の既定値を追記した。

### 詰まったこと・解決したこと
- `Makefile` は `v2-runtime tmux-runtime:` の複数 target 表記にしたため、既存 unit test の単純文字列期待を更新した。
- OCR smoke は実行でき、現在画面の OCR / metadata から YouTube 視聴中と判定した。
- LLM / VOICEVOX endpoint は未起動だったため `readiness` は false。launcher command、`dflash` binary、fused model path、VOICEVOX command の存在確認まで実施した。

### 検証
- `make check`
  - unit: 30 passed, 1 deselected
  - ruff: passed
- `git diff --check`
  - passed
- `uv run python -m server.runtime readiness`
  - database false
  - LLM `8081` / `8082` false
  - VOICEVOX `50122` false
  - OCR: `screencapture=true`, `tesseract=true`, `osascript=true`
- `make v2-ocr-smoke`
  - screenshot saved under `logs/user-status/...-screen.png`
  - OCR text 2472 chars
  - activity_label `watching_video`
  - metadata app `pycharm`, YouTube URL/title detected
- `command -v dflash`
  - `/Users/seijiro/.local/share/mise/installs/python/3.14/bin/dflash`
- `test -d v1/loras/lora/fused_model`
  - main fused model ok
- `test -f /Users/seijiro/Sync/sync_work/by-llms/async-voicevox/run_streaming_voicevox.command`
  - voicevox command ok
- `make -n llm-run voicevox-run v2-llm-tts-smoke`
  - expected dflash / VOICEVOX / real smoke commands printed
- `uv run pytest -m integration -q`
  - 1 skipped, 30 deselected (`TEST_DATABASE_URL` 未設定)

### 次のセッションでやること
- `make tmux-runtime` で dflash / VOICEVOX / v2 processes を実起動し、`make v2-runtime-ready` が true になることを確認する。
- runtime 起動後に `make v2-llm-tts-smoke` を実行して、dflash text delta -> VOICEVOX WAV chunk の実測を `_docs/latency.md` に追記する。

### 追記（実 runtime 接続の追加確認）
- `server.hot_path.app` の `/ws` が `prompt` / `text_prompt` / `user_text` を受けた時に `PromptExecutor` を呼び、`model_delta` / `model_complete` と binary WAV chunk を返す経路を追加した。
- `client/main.js` は server から届く binary WAV chunk を `decodeAudioData()` で再生するようにした。client 側で状態判定は増やしていない。
- 追加 unit `test_hot_path_websocket_uses_prompt_executor_for_text_prompt` を作り、fake executor 注入で `/ws` -> model event -> WAV bytes -> completion の契約を確認した。
- 再検証:
  - `make check`: unit 31 passed, 1 deselected / ruff passed
  - `make v2-ocr-smoke`: OCR text 2422 chars, metadata app `Google Chrome`, Gmail URL detected, activity_label `watching_video`
  - `uv run pytest -m integration -q`: 1 skipped, 31 deselected (`TEST_DATABASE_URL` 未設定)

## 2026-06-18 セッション2

### やること（開始時に書く）
- root `PLAN.md` を上から順番に実装する。
- まず V2.0 の root control plane と v2 用ディレクトリを作り、その上に DTO / DB schema / runtime helper / process scaffold / evaluation hook までを段階的に積む。
- 外部実機依存の Apple Speech / VOICEVOX / Calendar / OCR / live conversation smoke は、コードと smoke hook を先に用意し、実行できない検証は明示して残す。

### やったこと
- root `README.md` / `MEMORY.md` / `Makefile` / `config/v2.toml` と v2 用 `server/` / `client/` / `tests/` / `scripts/` / `background-process/` / `reports/` を作った。
- `server/shared/models.py` に v2 DTO を集約し、hot loop 例外は VAD 側 primitive のまま扱う実装にした。
- `server/shared/schemas.py` / `notify.py` / `db.py` / `process.py` / `logging.py` を作り、small schema、fixed-line parser、id-only NOTIFY、psycopg pool helper、heartbeat、JSONL logger を用意した。
- `docker/postgres/init/100_v2_core.sql` を追加し、v2 core table と `v2_notify_id(channel_name, event_id)` を定義した。
- hot-path browser shell、VAD pre-roll、streaming STT observation 変換、tomoko-process の session/floor/prompt core、model/TTS fake execution pathを実装した。
- short reaction、initiative motivation、user status、info acquire、summary、candidate generation、prompt cancellation、floor holding、follow-up、stop arbitration、evaluation logging/report の deterministic scaffold を実装した。
- `make v2-runtime` / `v2-stop` / `v2-info-once` / `v2-initiative-sim` / `v2-floor-bench` / `v2-report-latest` を追加した。
- `_docs/latency.md` に v2 scaffold smoke と live first audio 未測定であることを追記した。

### 詰まったこと・解決したこと
- root `MEMORY.md` が存在しなかったため、v1 `MEMORY.md` と root `LOG.md` を参照してから V2.0 として root `MEMORY.md` を作成した。
- scripts を `python scripts/foo.py` で実行すると `server` package が import path に乗らなかったため、`scripts/__init__.py` を追加し Make target を `python -m scripts...` に変更した。
- FastAPI shell は `index.html` だけでは `/client/main.js` が 404 になるため、`/client` static mount を追加した。
- integration test は `TEST_DATABASE_URL` が未設定のため skip になる。実 DB schema の insert/select/FK/NOTIFY 確認は DB 起動後に実行する。
- V2.20 の 10 分 live conversation smoke は外部 runtime 依存のため未実行。readiness hook と report hook までは実装済み。

### 検証
- `make check`
  - unit: 28 passed, 1 deselected
  - ruff: passed
- `uv run pytest -m integration -q`
  - 1 skipped, 28 deselected (`TEST_DATABASE_URL` 未設定)
- `make -n v2-runtime v2-stop`
  - hot-path / tomoko / info / user-status / summary / think の tmux 起動順と Ctrl-C 停止順を確認した。
- `make v2-info-once`
  - sample calendar DTO map を出力した。
- `make v2-initiative-sim`
  - synthetic high-pressure scenario で 4 秒以降 `would_initiate=true` になることを確認した。
- `make v2-floor-bench`
  - 600/800/1000/1200/1500ms pause の holding decision を出力した。
- `uv run python -m server.runtime readiness`
  - DB / LLM / VOICEVOX / Apple Speech / OCR の readiness expectations を出力した。
- `make v2-report-latest`
  - `reports/v2-latest.html` を生成した。
- `git diff --check`
  - passed
- `git diff -- v1`
  - no diff
- `uv run uvicorn server.hot_path.app:app --host 127.0.0.1 --port 8020`
  - 起動済み。`/` と `/client/main.js` の HTTP smoke が通った。

### 次のセッションでやること
- DB を起動して `TEST_DATABASE_URL=... uv run pytest -m integration -q` を実行する。
- Apple Speech / VOICEVOX / LLM runtime を起動した状態で V2.20 の 10 分 live conversation smoke を行い、first content / first audio / total latency を `_docs/latency.md` に追記する。

## 2026-06-18 セッション1

### やること（開始時に書く）
- v2 を始めるため、v1 の `PLAN.md` / `MEMORY.md` / `LOG.md` と root の v2 設計メモを読み、v2 の実装手順を root `PLAN.md` に書く。
- root にはまだ `PLAN.md` / `LOG.md` / `MEMORY.md` が無いため、v1 の記録を参照元として扱い、v2 用の `PLAN.md` と `LOG.md` を作る。

### やったこと
- v1 の `MEMORY.md` / `LOG.md` / `PLAN.md`、root `ARCHITECTURE.md`、`_docs/v2.md`、`_docs/v2-alpha.md`、`_docs/v2-2.md`、`_docs/thinkerv2.md`、`_docs/evaluation.md` を確認した。
- root `PLAN.md` を新規作成し、v1 から継承する知見、v2 の process map、Phase V2.0 から V2.20 までの実装手順と完了条件を書いた。
- root `LOG.md` を新規作成し、このセッションの開始記録と完了記録を残した。

### 詰まったこと・解決したこと
- root には `PLAN.md` / `LOG.md` / `MEMORY.md` が存在しなかったため、AGENTS.md の作業開始手順は v1 側の記録を参照して満たし、v2 用には root `PLAN.md` / `LOG.md` を新規作成した。
- 今回は計画ドキュメントのみの作業で、v2 実装コードはまだ無いため unit test は実行していない。

### 検証
- `git diff --check -- PLAN.md LOG.md`
  - passed
- `wc -l PLAN.md LOG.md`
  - `PLAN.md` 586 lines / `LOG.md` 25 lines

### 次のセッションでやること
- `PLAN.md` の Phase V2.0 に従い、root `README.md` / `MEMORY.md` / v2 用ディレクトリ / root Makefile を作る。
