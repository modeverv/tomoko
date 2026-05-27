## 2026-05-26 セッション1

### やること（開始時に書く）
- backend 依頼/応答の debug trace を JSONL として `logs/backend-trace.jsonl` に出す
- LM Studio request lifecycle に `start` / `queue_acquired` / `response_headers` / `first_delta` / `done` / `error` を出す
- local LLM / TTS backend も同じ `tomoko_backend_call` trace 語彙で start / first / done / error を出し、GPU/queue 詰まりを推測できるようにする
- PLAN.md に backend trace Phase を追記してから実装する

### やったこと
- PLAN.md に Phase 13.5 backend call JSONL trace を追記し、完了チェックを更新した
- `server/shared/inference/trace.py` を追加し、`logs/backend-trace.jsonl` へ 1 行 1 JSON の `tomoko_backend_call` trace を出すようにした
- `chat_stream_with_trace_role()` / `chat_stream_structured_with_trace_role()` を追加し、既存 fake backend と互換性を保ちながら role を渡せるようにした
- `LMStudioBackend` に URL 単位 process-local semaphore と lifecycle trace を追加した
  - `start`
  - `queue_acquired` + `wait_ms`
  - `response_headers`
  - `first_delta`
  - `done`
  - `error`
- local LLM backend に同じ trace 語彙を追加した
  - `GemmaMLXBackend`
  - `MLXLMBackend`
  - `OllamaBackend`
- TTS backend に同じ trace 語彙を追加した
  - `SayBackend`
  - `KokoroMLXBackend`
  - `VoicevoxBackend`
  - `VoicevoxStreamBackend`
- STT / embedding backend に request 単位の trace を追加した
  - `FasterWhisperSTT`
  - `MlxWhisperSTT`
  - `WhisperCoreMLSTT`
  - `WhisperKitServeSTT`
  - `SentenceTransformerEmbeddingBackend`
- 会話 / 要約 / candidate / stop intent / initiative / diary / world observation から role を渡すようにした

### 詰まったこと・解決したこと
- 既存 unit test には `chat_stream(..., trace_role=...)` を受けない fake backend が多い
  → 直接 signature を変えた呼び出しにせず、helper が `trace_role` 対応可否を見て渡す形にした
- LM Studio の複数 backend 名は同じ URL を共有しうる
  → backend 名単位ではなく `lmstudio:<url>` を `queue_key` にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_backend_trace.py tests/unit/test_lm_studio_backend.py tests/unit/test_gemma_mlx_backend.py tests/unit/test_mlx_lm_backend.py tests/unit/test_voicevox_tts.py tests/unit/test_kokoro_mlx_tts.py tests/unit/test_phase4_thinking.py tests/unit/test_world_observation_normalizer.py tests/unit/test_world_observation_interpreter.py`
  - 34 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_backend_trace.py tests/unit/test_stt_backends.py tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py`
  - 31 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を走らせ、`jq 'select(.trace=="tomoko_backend_call")' logs/backend-trace.jsonl` で会話 / background の重なりと LM Studio queue wait を確認する

## 2026-05-26 セッション2

### やること（開始時に書く）
- Kokoro の方が first binary 到着は速い前提を明確にしつつ、体感確認用に default TTS を通常 VOICEVOX へ戻す
- cancellable synthesis の初回 worker 遅延を比較に混ぜないため、`voicevox_tsumugi_stream` ではなく `voicevox_tsumugi` を採用する

### やったこと
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の `tts_backend` を `voicevox_tsumugi` に変更した
- config 契約テストの期待値も `voicevox_tsumugi` に戻した
- README / MEMORY.md に、cancellable stream ではなく通常 VOICEVOX を試す理由を追記した

### 詰まったこと・解決したこと
- `/cancellable_synthesis` は最初の binary 到着を速めるものではなかった
  → VOICEVOX 体感確認では通常 `/synthesis` backend を使い、Kokoro との速度差は backend trace で別途見る

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_voicevox_tts.py`
  - 21 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check config/central_realtime.toml config/edge_kitchen.toml tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py`
  - pass
- `git diff --check`
  - pass

## 2026-05-26 セッション3

### やること（開始時に書く）
- STT で `WhisperKit + openai_whisper-large-v3-v20240930_turbo_632MB + cpuAndNeuralEngine` 相当を試した履歴があるか確認する
- exact model / compute units を明示できる `whisperkit_serve` backend 設定を追加する
- central realtime の active STT をこの backend に切り替え、unit test で契約を固定する

### やったこと
- 過去ログ上では `WhisperKit serve small`、`WhisperKit serve large-v3-v20240930_626MB`、`local_whisper_mlx_large_turbo_q4` は試していたが、画像の turbo 632MB exact lane は active config として固定していなかったことを確認した
- `WhisperKitServeSTT` に `compute_units` を追加し、`whisperkit-cli serve` 起動時に `--audio-encoder-compute-units` / `--text-decoder-compute-units` を渡すようにした
- `config/central_realtime.toml` に `local_whisperkit_serve_large_turbo_632m_cpu_ne` を追加した
  - model: `large-v3-v20240930_turbo_632MB`
  - port: `127.0.0.1:50062`
  - compute units: `cpuAndNeuralEngine`
- central realtime の active `stt_backend` をこの backend に切り替えた
- PLAN.md / MEMORY.md / `_docs/latency.md` に判断と未実測事項を追記した

### 詰まったこと・解決したこと
- WhisperKit CLI help 上は `cpuAndNeuralEngine` が default だったが、実験条件を明示するため config から渡す形にした
- 既存 `local_whisper_mlx_large_turbo_q4` は品質が良かった比較候補として残した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py`
  - 16 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、`logs/backend-trace.jsonl` の STT `total_ms`、実 transcript、mactop 等の CPU/ANE/GPU 使用状況を確認する

## 2026-05-25 セッション51

### やること（開始時に書く）
- LM Studio の OpenAI 互換 API で `gemma-4-e4b-it-mlx` を使う backend 設定を追加する
- central realtime の会話 backend に `lmstudio_gemma4_e4b` を採用し、既存 Gemma E2B / LFM fallback を崩さない
- config / router / LM Studio backend の unit test で採用モデルを固定する

### やったこと
- `config/central_realtime.toml` に `lmstudio_gemma4_e4b` backend を追加した
- active `conversation_backend` を `lmstudio_gemma4_e4b` に切り替えた
- `conversation_fallback` を `local_gemma4_e2b_mlx` にし、LM Studio 側が落ちた/遅い場合も Gemma 系 local fallback に留めた
- config / router の unit test を E4B 採用前提へ更新した
- README / MEMORY.md / `_docs/latency.md` に採用モデルと smoke 実測を追記した

### 詰まったこと・解決したこと
- 既存の LM Studio backend 実装は OpenAI 互換 SSE に対して十分汎用だった
  → Python 実装は増やさず、backend spec と契約テストの追加に限定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_router.py tests/unit/test_lm_studio_backend.py`
  - 16 passed
- `.venv/bin/python -m pytest -m unit`
  - 326 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- LM Studio E4B short smoke
  - backend: `lmstudio_gemma4_e4b`
  - model: `gemma-4-e4b-it-mlx`
  - first delta 313.5ms / total 314.6ms
  - output: `はい。`

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、E4B の応答品質・口調・first audio 体感を確認する

## 2026-05-25 セッション41

### やること（開始時に書く）
- 現行 `local_whisperkit_serve_large` が実サンプルで空文字を返す問題を受け、STT backend を MLX Whisper large turbo q4 へ切り替える
- `local_whisper_mlx_large_turbo_q4` を設定に追加し、config 契約テストで固定する
- HF Hub の未認証 download と有料 plan / token の関係を確認して説明する

### やったこと
- `config/central_realtime.toml` の active `stt_backend` を `local_whisper_mlx_large_turbo_q4` に変更した
- `local_whisper_mlx_large_turbo_q4` backend を追加し、`mlx-community/whisper-large-v3-turbo-q4` を使うようにした
- config 契約テストを MLX large turbo q4 前提に更新した

### 詰まったこと・解決したこと
- `local_whisperkit_serve_large` は実サンプルで空文字を返し、会話ログ上でも partial は出るのに final が空文字になる挙動があった
  → WhisperKit serve large は一旦採用から外し、実測で精度と速度のバランスが良かった MLX large turbo q4 を採用する
- HF Hub の未認証 download は匿名アクセス扱いで rate limit が低い
  → まず HF_TOKEN を渡すだけでも匿名より上限が上がり、さらに PRO / Team / Enterprise で上限が上がる
- 実ブラウザ体感では STT 品質が劇的に改善し、会話として成り立ち始めている感触があった
- 「ココココ」系の hallucination っぽい出力時に GPU が完全にフルに使われていた
  → large 系 decoder が無音/ノイズ/短い断片で粘っている可能性があるため、次は STT 前の segment 抑制や no-speech 系閾値を確認する

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_stt_backends.py`
  - 16 passed
- `mise exec -- uv run ruff check config/central_realtime.toml tests/unit/test_phase0_config.py`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 300 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` を再起動し、`local_whisper_mlx_large_turbo_q4` の startup warm-up と実会話 transcript を確認する

## 2026-05-25 セッション42

### やること（開始時に書く）
- ルート直下に git 管理外の `work/` を切り、実環境ノイズや読み上げ評価の録音 artifact を保存できるようにする
- 既存 `/ws` の上で debug 録音を開始/停止し、今の UI から noise / read-aloud 録音を取れるようにする
- 読み上げ用の文章を UI に出し、録音後に configured STT の transcript と処理時間を返す

### やったこと
- `.gitignore` に `work/` を追加した
- `DebugAudioRecorder` を追加し、`work/audio-recordings/<recording_id>.wav` と `.json` metadata を保存するようにした
- `/ws` に `debug_recording_start` / `debug_recording_stop` JSON event を追加した
- 録音中の audio chunk は通常の `TomoroSession.process_audio_chunk()` へ流さず、debug recorder にだけ渡すようにした
- 読み上げ評価録音では、保存した audio を configured STT にかけて transcript / STT elapsed を UI と metadata に返すようにした
- 現行 UI に `Noise 1s` / `Read 5s` / `Next` ボタン、読み上げ文、debug 結果欄を追加した
- `tests/unit/test_debug_recording.py` と `/ws` debug recording unit test を追加した

### 詰まったこと・解決したこと
- フル unit で `test_phase106_initiative_policy.py` の候補期限が 2026-05-25 12:10 固定になっており、現在時刻では expired 判定になっていた
  → テスト候補の `created_at` を `datetime.now(UTC)` にし、常に未来の `expires_at` になるように修正した

### 検証
- `node --check client/main.js`
  - pass
- `mise exec -- uv run pytest -m unit tests/unit/test_debug_recording.py tests/unit/test_phase1_echo.py`
  - 6 passed
- `mise exec -- uv run ruff check server/edge/debug_recording.py server/edge/main.py tests/unit/test_debug_recording.py tests/unit/test_phase1_echo.py`
  - pass
- `mise exec -- uv run pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_debug_recording.py tests/unit/test_phase1_echo.py`
  - 19 passed
- `mise exec -- uv run pytest -m unit`
  - 303 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` のブラウザで `Noise 1s` と `Read 5s` を実行し、`work/audio-recordings/` の WAV/JSON と UI transcript を確認する

## 2026-05-25 セッション36

### やること（開始時に書く）
- `vad_silence_ms` を 1000ms に変更し、ユーザー発話の途中で返答へ入りにくくする
- 単なる待機ではなく、返答がまだ表示/音声出力されていない段階で新しい `listening` が来たら stale reply としてキャンセルし、人間の続き発話を優先する
- STT backend を `local_whisperkit_serve_small` に切り替え、WhisperKit serve/CoreML 経路で確認できるようにする

### やったこと
- `config/central_realtime.toml` の `vad_silence_ms` を 1000ms に変更した
- central runtime の `stt_backend` を `local_whisperkit_serve_small` に変更した
- `TomoroSession` に未出力 reply の stale cancel を追加した
  - 新しい `listening` が来た時、reply text / emotion / audio がまだ出ていなければ古い reply task をキャンセルする
  - すでに出力が始まった reply は既存の barge-in / stop-intent 制御に任せる
- config 契約と stale cancel の unit test を追加した

### 詰まったこと・解決したこと
- 最初は「少し待つ」案に見えたが、小手先対応になるため否定した
  → session 管制として、未出力の古い reply を stale result として捨てる方針にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_streaming_tts_pipeline.py tests/unit/test_phase0_config.py tests/unit/test_stt_backends.py`
  - 19 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 297 passed, 17 deselected
- `TOMOKO_STT_BENCH_BACKENDS=local_whisperkit_serve_small mise exec -- uv run pytest -m perf --tb=short tests/perf/test_stt_latency.py -s`
  - 1 passed
  - warm 7003.5ms / measured 211.9ms

### 次のセッションでやること
- `make server-debug` の実ブラウザ確認で、発話途中の分割返答が減ったか確認する
- WhisperKit serve 切り替え後の mactop ANE/CPU/GPU の見え方を実機で確認する

# LOG.md

実装セッションの時系列ログ。セッションをまたいだ引き継ぎのために書く。

---

## 2026-05-27 セッション1

### やること（開始時に書く）
- LM Studio の自動ロード前提で Gemma 4 E4B / 26B / 31B 候補を同一プロンプトで叩く
- first token / total latency と、返答の意味性・会話の踏み込みを比較する
- Tomoko の default 会話モデルを大きくした時の効果とリスクを判断する

### やったこと
- LM Studio `/v1/models` で `gemma-4-e4b-it-mlx` / `gemma-4-26b-a4b-it-mlx` / `gemma-4-31b-it-mlx` が利用可能なことを確認した
- Tomoko の base persona 相当 prompt と同一会話 context で、3モデルの streaming latency と返答内容を比較した
- 会話後処理 worker 相当の「会話から知見を抽出する」prompt でも、3モデルの出力を比較した

### 詰まったこと・解決したこと
- 31B は短い音声返答では first content が 0.9〜1.4s、total が 2.3〜2.9s になり、hot path には重い
- 26B A4B は初回 model switch / load では約10sかかったが、ロード直後の短い会話返答は first content 0.28〜0.33s、total 0.48〜0.63s で E4B と同程度だった
- 短い音声返答では 31B の品質優位は小さく、26B A4B の方が速度・意味性のバランスが良かった
- 会話後処理の知見抽出では、E4B より 26B / 31B の方が「意味ある会話=新しい知見」「即応の口と後で考える頭の分離」などを記憶候補として具体化できた

### 次のセッションでやること
- hot path 全面置換ではなく、まず deep / background role だけ `gemma-4-26b-a4b-it-mlx` に向ける構成を検討する
- LM Studio 同一 URL semaphore のため、background 26B/31B が conversation E4B を塞がないよう queue / process 分離を検討する

## 2026-05-27 セッション2

### やること（開始時に書く）
- LM Studio の実ログを見ながら Gemma 4 31B / 26B / E4B に同一リクエストを投げる
- formatted input / output に `<|think|>` / thought channel / reasoning field が出ているか確認する
- 31B の遅さが thinking 由来か、モデルサイズ・ロード・生成速度由来かを切り分ける

### やったこと
- `lms log stream --source model --json --stats` を開き、LM Studio の formatted input / output を直接確認した
- `gemma-4-31b-it-mlx` に `"think": false`、`think` 省略、`"think": true` の3パターンを投げた
- system prompt 先頭に `<|think|>` を明示したパターンも投げた

### 詰まったこと・解決したこと
- 31B の formatted input はどのパターンでも末尾が `<|channel>thought\n<channel|>` になった
  - これは Gemma 4 model card の「thinking disabled 時も空 thought block tag を出す」挙動に近い
  - 実 output は本文のみで、thought 本文は生成されなかった
- OpenAI 互換 streaming delta でも `reasoning` / `reasoning_content` / `thinking` field は出なかった
- `"think": true` はこの LM Studio / Gemma 4 MLX 経路では formatted input を変えなかった
- `<|think|>` を system prompt に明示しても、実 output は `17 × 23 = 391` のような本文だけで thought は出なかった

### 結論
- 今回の実測ログ上、`gemma-4-31b-it-mlx` が長い thinking を生成して遅くなっている証拠はない
- 31B の 1秒台 first content は、hidden thinking よりモデルサイズ / MLX decode 速度 / prompt 処理の影響と見るのが妥当
- LM Studio の Gemma 4 template は空 thought channel prefix を入れるが、これは no-think 用の assistant prefix と判断する

## 2026-05-27 セッション3

### やること（開始時に書く）
- Tomoko と数ターン会話済みの状態を dummy messages と補助文脈で再現する
- E4B / 26B A4B / 31B が文脈を使って意味のある返答を返せるか比較する

### やったこと
- base persona に、会話から得られた補助文脈を足した system prompt を作った
- 「意味のある会話」「朝レビュー・夜設計」「即応の口と後で考える頭」の3シナリオを messages として投げた
- 3モデルの latency と返答内容を比較した

### 結論
- E4B は速いが、踏み込み要求に対して「情報が足りない」に逃げやすい
- 26B A4B は「作業中の会話」と「振り返りの外の会話」が混ざっている、という盲点を返せており、意味性が最も良かった
- 31B は初回ロード/切替が約16s、ロード後も first content 約1.3〜1.5s / total 約2.7〜3.5s で重い
- 31B の返答は悪くないが、26B A4B より明確に優れてはいなかった

## 2026-05-27 セッション4

### やること（開始時に書く）
- 明日の実機会話比較のため、central realtime の active conversation backend を Gemma 4 26B A4B MLX に切り替える
- E4B は比較・復帰用 backend として残す
- latency を犠牲にしても 26B を試せるよう、config / contract test / docs の期待値を更新する

### やったこと
- `config/central_realtime.toml` の `conversation_backend` を `lmstudio_gemma4_26b_a4b` に変更した
- `lmstudio_gemma4_26b_a4b` backend を追加し、model を `gemma-4-26b-a4b-it-mlx` にした
- 26B の live trial を妨げないよう、`max_latency_ms` は 5000ms に広げた
- `lmstudio_gemma4_e4b` は比較・復帰用として残した
- README / MEMORY.md / `_docs/latency.md` に 26B 採用理由と未実測事項を追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_router.py`
  - 12 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を走らせ、26B の first text / first audio / 会話の意味性を確認する
- 戻す場合は `config/central_realtime.toml` の `conversation_backend` を `lmstudio_gemma4_e4b` に戻す

## 2026-05-25 セッション51

### やること（開始時に書く）
- VOICEVOX Engine の stream / cancellable synthesis 対応状況を確認する
- 既存ブラウザ再生契約を壊さずに使える `voicevox_stream` TTS backend を追加する
- central / edge の default TTS を stream 版 VOICEVOX へ切り替え、unit test で固定する

### やったこと
- `VoicevoxStreamBackend` を追加し、VOICEVOX Engine の `/cancellable_synthesis` を優先して使うようにした
- 実行中の VOICEVOX Engine 0.25.2 では experimental feature が default 無効で `/cancellable_synthesis` が 404 になるため、`/synthesis` fallback を入れた
- Tomoko の現ブラウザは binary chunk ごとに `decodeAudioData()` するため、backend は完全な WAV chunk を返す契約を維持した
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の `tts_backend` を `voicevox_tsumugi_stream` に変更した
- README / MEMORY / `_docs/latency.md` に stream endpoint と fallback の扱いを記録した

### 詰まったこと・解決したこと
- OpenAPI 上は `/cancellable_synthesis` が存在するが、実 AudioQuery では `{"detail":"実験的機能はデフォルトで無効になっています。使用するには引数を指定してください。"}` で 404 になった
  → 現環境では fallback を必須とし、真の部分音声再生は PCM framing / client playback 変更の別作業に切り出す

### 検証
- `curl -sS --max-time 3 http://127.0.0.1:50021/openapi.json`
  - `/cancellable_synthesis` の存在を確認
- `.venv/bin/python -m pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase14_edge_split.py`
  - 17 passed
- `.venv/bin/python -m ruff check server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py server/shared/inference/tts/__init__.py tests/unit/test_phase14_edge_split.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 327 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- 実 smoke
  - text: `うん、わかった。少し待ってね。`
  - first chunk 347.5ms / total 347.6ms / 24kHz mono / 2837.3ms audio
  - output: `logs/voicevox-tsumugi-stream-smoke.wav`

## 2026-05-25 セッション50

### やること（開始時に書く）
- VOICEVOX Engine の stream / cancellable synthesis 対応状況を確認する
- 既存ブラウザ再生契約を壊さずに使える `voicevox_stream` TTS backend を追加する
- central / edge の default TTS を stream 版 VOICEVOX へ切り替え、unit test で固定する

## 2026-05-25 セッション49

### やること（開始時に書く）
- 起動済み `voicevox.app` / VOICEVOX Engine を使う TTS backend を追加する
- 春日部つむぎの speaker/style を default にし、Tomoko の `tts_backend` として設定で使えるようにする
- VOICEVOX 側の推論は外部 app / Engine に任せ、Tomoko 側では GPU/MLX TTS を使わない HTTP adapter として実装する

### やったこと
- `VoicevoxBackend` を追加し、VOICEVOX Engine の `/audio_query` / `/synthesis` を叩いて WAV を返す TTS backend にした
- 春日部つむぎの speaker id `8` を default にし、`春日部つむぎ` / `春日つむぎ` / `tsumugi` alias でも指定できるようにした
- `config/central_realtime.toml` と `config/edge_kitchen.toml` の `tts_backend` を `voicevox_tsumugi` に変更した
- README に、VOICEVOX は起動済み外部 Engine を使い、本体・音源・出力音声は各規約に従うことを追記した
- `_docs/latency.md` に `voicevox.app` 実 smoke の first / total latency を記録した

### 詰まったこと・解決したこと
- `voicevox.app` は `127.0.0.1:50021` で `/speakers` に応答しており、春日部つむぎは speaker id `8` だった
- Tomoko 側では GPU/MLX TTS を起動せず、CPU 側で動いている外部 VOICEVOX Engine へ HTTP で依頼する形にした

### 検証
- `curl -sS --max-time 2 http://127.0.0.1:50021/speakers`
  - `春日部つむぎ` / `id=8` を確認
- `.venv/bin/python -m pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py`
  - 18 passed
- `.venv/bin/python -m pytest -m unit`
  - 324 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- 実 smoke
  - text: `うん、わかった。少し待ってね。`
  - first chunk 364.7ms / total 364.9ms / 24kHz mono / 2837.3ms audio
  - output: `logs/voicevox-tsumugi-smoke.wav`

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、VOICEVOX TTS の体感・音質・回り込みを確認する
- 長文で first audio が遅い場合は、TTS flush 単位や VOICEVOX audio_query の pause / speed 調整を見直す

## 2026-05-25 セッション40

### やること（開始時に書く）
- stop-intent worker で rule / embedding signal を先に保存し、LLM classifier 失敗だけで observation 全体を error にしない
- LM Studio 500 などの LLM 側一時失敗を degraded optional として扱う unit test を追加する

### やったこと
- `StopIntentClassifierWorker` が rule / embedding signal を保存・emit してから optional LLM classifier を実行するようにした
- LLM classifier が例外を出した場合、`method="llm"` / `predicted_kind="none"` / `confidence=0.0` の degraded signal を保存し、observation は `completed` にするようにした
- LLM 失敗時にも rule / embedding / degraded LLM signal が残る unit test を追加した

### 詰まったこと・解決したこと
- LM Studio 500 は stop-intent の補助 LLM だけの失敗だったため、deterministic な rule signal を巻き込まない処理順へ変更した

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_intent_queue.py`
  - 5 passed
- `mise exec -- uv run ruff check server/gateway/stop_intent.py tests/unit/test_stop_intent_queue.py`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 300 passed, 17 deselected

### 次のセッションでやること
- 実 `make server-debug` で LM Studio 500 が再発した時、`stop_intent_observations.status=completed` と degraded LLM signal が残ることを DB で確認する

## 2026-05-25 セッション35

### やること（開始時に書く）
- 外部観測 interpretation に `tomoko_private_reaction` と `candidate_seed_text` を追加する
- thinker / journalist が一般要約ではなく Tomoko の内心反応と発話候補の種を使えるようにする
- 既存 DB に DDL を反映し、実データを再 interpretation する

### やったこと
- `world_observation_interpretations` に `tomoko_private_reaction` / `candidate_seed_text` を追加した
- `WorldObservationInterpretation` / store / `world_observation_trace` / DB integration test を新フィールドに対応させた
- interpreter の structured output schema と prompt に、内心メモと自発発話候補の種を必須フィールドとして追加した
- thinker の world observation source は `candidate_seed_text` を優先し、なければ `tomoko_private_reaction` / `interpretation_text` に fallback するようにした
- journalist input は `tomoko_private_reaction` と `candidate_seed_text` を diary material に含めるようにした
- 再実行前後の SELECT を `logs/world-observation-observe/2026-05-25-before-private-reaction-rerun-fixed.json` と `logs/world-observation-observe/2026-05-25-after-private-reaction-rerun-fixed.json` に保存した

### 詰まったこと・解決したこと
- 既存 DB へ `CREATE OR REPLACE VIEW world_observation_trace` を適用した時、view の既存列 `reason_json` の位置に `tomoko_private_reaction` を挿入する形になり PostgreSQL が拒否した
  → `DROP VIEW IF EXISTS world_observation_trace` してから view を作り直す DDL にした
- view 更新失敗後に interpretation delete が走ったため、一時的に DB 上の interpretation は 0 件になった
  → DDL 修正後に再度 `make information-interpret-once` を実行し、10 件生成し直した

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko -v ON_ERROR_STOP=1 < docker/postgres/init/013_world_observations.sql`
- `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
  - `296 passed, 17 deselected`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase180_world_observations_db.py tests/integration/test_phase87_persona_snapshots_db.py`
  - `2 passed`
- `git diff --check`

### 観測
- 実DBの `world_observation_trace` で `tomoko_private_reaction` と `candidate_seed_text` が埋まることを確認した
- 例: `MLXの統合の話、少しだけ気になったよ。` のような短い発話種が生成された

### 次のセッションでやること
- 反応がまだ硬い場合は、`tomoko_private_reaction` の文体制約をさらに会話寄りにし、`candidate_seed_text` を候補生成の scoring に組み込む

## 2026-05-25 セッション34

### やること（開始時に書く）
- 外部観測 interpretation が一般要約に寄りすぎている問題を補正する
- `reason_json` に `persona_basis` / `user_basis` / `speakability_basis` / `avoid_overclaim` を必須化する
- `speakability_hint` を `short_now` / `later` / `diary` / `avoid` の enum にする
- interpreter prompt に `base_persona.md` 本文を渡す
- 初期 persona snapshot seed を DB に入れ、外部観測を再 interpretation して反応を見る

### やったこと
- `WorldObservationInterpretation.from_json()` で `speakability_hint` enum と `reason_json` 必須キーを検証するようにした
- LM Studio structured output schema でも `speakability_hint` enum と `reason_json` 必須キーを強制した
- 外部観測 interpreter prompt に `base_persona.md` 本文を追加し、口調コピーではなく関心・距離感の判断材料として使うよう明記した
- `_tools/seed_initial_persona_snapshot.py` と `make persona-seed-initial` を追加した
- initial seed として warmth / curiosity / restraint / local inference / MLX / voice interaction / life texture を persona snapshot に入れた
- 再実行前後の外部観測 interpretation を `logs/world-observation-observe/2026-05-25-before-rerun.json` と `logs/world-observation-observe/2026-05-25-after-rerun.json` に保存した

### 詰まったこと・解決したこと
- 最初の seed tool は `_tools` 配下から実行した時に `server` import path が通らなかった
  → 既存 `_tools` と同じく repo root を `sys.path` に追加した
- `COPY TO STDOUT` で保存した JSON が `\n` escaped の1行になった
  → 観測用ファイルとして読みやすいよう pretty JSON へ整形し直した

### 検証
- `make persona-seed-initial`
  - `persona_seed inserted state_id=d4fcf6a7-8937-4f13-94c6-67b0d07445e3 lexicon_id=aae8f139-4158-42c4-8e53-0c066429ffcf`
- `docker exec tomoko-postgres psql ... DELETE FROM world_observation_interpretations`
- `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- `docker exec tomoko-postgres psql ... SELECT count(*) ... FROM world_observation_interpretations`
  - `interpretations = 10`, `with_state = 10`, `with_lexicon = 10`
- `mise exec -- uv run pytest -m unit`
  - `296 passed, 17 deselected`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase180_world_observations_db.py tests/integration/test_phase87_persona_snapshots_db.py`
  - `2 passed`
- `mise exec -- uv run ruff check .`

### 観測
- `reason_json` は4キーが入り、persona / user / speakability / overclaim の根拠が分離された
- `interpretation_text` は「少しだけ気になる」「後で会話の種として置く」など、以前より Tomoko の距離感が出た
- ただしまだ全体としては控えめで、強い個性というより「静かに受け取る」方向に寄っている

### 次のセッションでやること
- もっと面白くするなら、`tomoko_private_reaction` や `candidate_seed_text` のような発話候補寄りの別フィールドを追加する

## 2026-05-25 セッション33

### やること（開始時に書く）
- Phase 18 の外部観測 interpreter に、Tomoko が何者かを短く示す system prompt grounding を追加する
- `tomoko_interest` / `relevance_to_user` / `speakability_hint` が一般的なアシスタント像ではなく Tomoko の関心で解釈されることを unit test で固定する
- `base_persona.md` を短い core persona として厚くし、persona snapshot の扱いルールと serialized JSON を LLM prompt に常に流す
- persona snapshot が 0 件でも、空状態を表す JSON fallback を prompt に入れる
- 既存の外部観測レコードを削除し、実 Markdown を再 ingest / interpret する

### やったこと
- `WorldObservationInterpreter` の system prompt に `Tomoko profile` を追加した
- Tomoko を「一人のユーザーと暮らすローカル推論ベースの日本語音声対話システム」として短く定義した
- ユーザーとの関係、関心領域、ニュース解説者ではないこと、話題候補としての出し方を prompt に明記した
- interpreter unit test で grounding が system prompt に含まれることを固定した
- `prompts/base_persona.md` に、Tomoko のあたたかさ、好奇心、遊び心、遠慮深さ、ユーザーとの関係性を短く追記した
- `server/shared/persona_prompt.py` を追加し、persona snapshot の扱いルールと empty fallback JSON を一箇所にまとめた
- 会話 prompt は persona slice / lexicon terms を serialized JSON として常に渡すようにした
- 外部観測 interpreter は最新 persona snapshot 全体、または空 fallback JSON を system prompt に渡すようにした

### 詰まったこと・解決したこと
- structured output は JSON 形状を固定できるが、Tomoko が何者かという意味の grounding は別途必要だった
  → normalizer ではなく、Tomoko の関心を採点する interpreter にだけ短い profile を入れる形にした
- DB に persona snapshot が 0 件の時、prompt から persona 情報セクション自体が消えると LLM が一般アシスタント像で補完しやすい
  → 空の snapshot JSON を明示し、「まだ学習済みデータがない」という状態を渡す形にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_world_observation_interpreter.py tests/unit/test_lm_studio_backend.py tests/unit/test_world_observation_normalizer.py`
- `mise exec -- uv run ruff check server/world_observations/interpreter.py tests/unit/test_world_observation_interpreter.py server/shared/inference/backends/lm_studio.py server/world_observations/normalizer.py tests/unit/test_lm_studio_backend.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_world_observation_interpreter.py tests/unit/test_phase87_persona_snapshots.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py`
- `mise exec -- uv run ruff check prompts/base_persona.md server/shared/persona_prompt.py server/gateway/thinking/fast.py server/world_observations/interpreter.py tests/unit/test_world_observation_interpreter.py tests/unit/test_phase87_persona_snapshots.py`
- `docker exec tomoko-postgres psql ... DELETE FROM world_observation_documents WHERE raw_file_path = 'informations/work/2026-05-25-world-observation.md'`
- `make information-ingest-once`
  - `world_observation_ingest processed=1 archived=1 failed=0 skipped=0`
- `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- `docker exec tomoko-postgres psql ... SELECT status, count(*) FROM world_observation_documents GROUP BY status`
  - `completed = 1`
- `docker exec tomoko-postgres psql ... SELECT count(*) ... FROM world_observation_interpretations`
  - `interpretations = 10`, `with_state = 0`, `with_lexicon = 0`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
  - `296 passed, 17 deselected`

### 次のセッションでやること
- 必要なら `make information-interpret-once` の実データ出力を見て、`tomoko_interest` / `relevance_to_user` の偏りを確認する

## 2026-05-25 セッション32

### やること（開始時に書く）
- セッション31の Perplexity / Computer Use 実行結果を踏まえ、`informations/prompts/daily_world_observation.md` を更新する
- Markdown artifact と frontmatter delimiter が安定するように prompt を明確化する

### やったこと
- `daily_world_observation.md` に成果物名、Markdown document 出力、code fence 禁止を明記した
- frontmatter delimiter は `---` 固定とし、`***` や水平線で代用しないよう明記した
- 本文構造を topic heading / 事実 / 推測・含意 / source_hint に寄せた
- Perplexity の copy button ではなく `Markdown形式でダウンロード` を使う前提を prompt 内にも追記した

### 詰まったこと・解決したこと
- セッション31では copy button 由来の Markdown が画面表示向けに整形されることがあった
  → prompt 側でも download 用 Markdown として成立する形を優先するようにした

### 検証
- `git diff -- informations/prompts/daily_world_observation.md LOG.md` で変更範囲を確認した

### 次のセッションでやること
- 次回の Perplexity 実行で artifact title / frontmatter / source_hint が安定するか確認する

## 2026-05-25 セッション31

### やること（開始時に書く）
- `informations/prompts/daily_world_observation.md` を使って Computer Use 経由で Perplexity に外部観測レポートを依頼する
- 得られた Markdown を `informations/work/2026-05-25-world-observation.md` に保存する
- 保存後に `make information-ingest-dry-run` を実行し、受け入れ可否と問題点を確認する

### やったこと
- Computer Use で Perplexity に外部観測レポートを依頼し、Markdown 形式で成果物をダウンロードした
- ダウンロードした `world_observation_2026-05-25.md` を `informations/work/2026-05-25-world-observation.md` に保存した
- 保存した Markdown に対して validator と ingest dry-run を実行した

### 詰まったこと・解決したこと
- Computer Use の `type_text` は長い日本語 prompt の入力が崩れた
  → Chrome の入力欄へ clipboard 経由で prompt を入れて送信した
- Perplexity の copy button で取れる Markdown は画面表示向けに整形されることがあった
  → download menu の `Markdown形式でダウンロード` を使い、frontmatter delimiter が正しいファイルを保存した

### 検証
- `mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/work/2026-05-25-world-observation.md`
  - valid: true
  - issues: []
- `make information-ingest-dry-run`
  - `world_observation_ingest processed=1 archived=0 failed=0 skipped=1`
  - `would_ingest informations/work/2026-05-25-world-observation.md`

### 次のセッションでやること
- 必要なら `make information-ingest-once` で実取り込みし、`make information-interpret-once` で Tomoko 用の解釈生成へ進める

## 2026-05-25 セッション30

### やること（開始時に書く）
- Phase 18 全体として、外部観測 Markdown と Tomoko 解釈パイプラインを実装する
- 18.0 から 18.8 まで、raw artifact directory、schema validator、DB store、normalizer、ingest job、interpreter、thinker / journalist 接続、operator recipe、trace hardening をテスト可能な単位で進める
- `/ws` / `TomoroSession` の hot path へ外部情報取得や LLM normalize を入れず、background / local job に閉じる

### やったこと
- `informations/` directory contract、`.gitignore`、README、Perplexity prompt、Codex operator recipe、sample artifact を追加した
- raw Markdown frontmatter validator、validator CLI、normalizer、ingest job、interpreter worker、trace inspect CLI を追加した
- `world_observation_documents` / `world_observation_items` / `world_observation_interpretations` / `world_observation_trace` を追加した
- `PostgresWorldObservationStore` / `InMemoryWorldObservationStore` を実装し、checksum idempotent import と archive / failed file movement を接続した
- `utterance_candidates.metadata_json` を追加し、world observation candidate の document / item / interpretation trace を保存できるようにした
- thinker に `WorldObservationSource`、journalist input に world observation interpretation 素材を接続した
- `PLAN.md` の Phase 18 checkbox と実装結果、`MEMORY.md`、`_docs/latency.md` を追記した

### 詰まったこと・解決したこと
- Phase 18.6 の trace は当初 `context_tags` だけで足りるように見えた
  → PLAN の `utterance_candidates.metadata_json` 契約を満たすため、候補テーブルに JSONB metadata を追加した
- `normalizer` の schema validation は pydantic を増やさず、既存 DTO 方針に合わせて dataclass + 手動 validation にした
- Perplexity / Computer Use の実 UI 操作は不安定な外周手順なので、operator recipe として文書化し、test 対象から外した

### 検証
- `mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/samples/sample-world-observation.md`
- `make information-ingest-dry-run`
- `git check-ignore informations/work/example.md informations/archived/example.md informations/failed/example.md`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### 次のセッションでやること
- 実 Perplexity 出力を `informations/work` に置き、`make information-ingest-dry-run` → `make information-ingest-once` → `make information-interpret-once` → `make thinker-once` の実データ smoke を行う
- 必要なら `world_observation_trace` で candidate / diary / conversation への接続を実データで確認する

## 2026-05-25 セッション29

### やること（開始時に書く）
- Supertonic F1 の stop ack を諦める前に、`はい` / `止めます` の分割生成 + WAV 結合を試す
- `はい、発話を止めます` など短すぎない制御文も生成し、Whisper で明瞭性を比較する
- Supertonic F1 で安定する候補があれば、Kyoko 版ではなく Supertonic F1 版 `assets/audio/stop_ack.wav` へ差し替える

### やったこと
- Supertonic F1 で `はい` / `止めます` / `はい、発話を止めます` の分割生成と WAV 結合を試した
- 追加で `はい、止めますね。` など、短すぎない候補を生成した
- 人間の聞き取りで `logs/stop-ack-supertonic-retry/phrase_tomemasu_ne.wav` を採用する判断になったため、`assets/audio/stop_ack.wav` へコピーした
- `StopAckAudioProvider` の control response text を `はい、止めますね` に更新した
- `tests/unit/test_stop_ack_audio.py` を Supertonic F1 採用版の 44.1kHz mono WAV 検証に更新した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に、Kyoko fallback を否定して選定済み Supertonic F1 版を採用する追記を入れた

### 詰まったこと・解決したこと
- `local_whisper_mlx_small` は短い Supertonic F1 の候補を安定して文字起こしできなかった
  → 今回は固定アセットの最終判断として、STT 結果より人間の聞き取りを優先した
- 既存の `はい、止めます` は末尾が欠けて聞こえやすかった
  → `はい、止めますね。` にして発話を少し伸ばし、末尾の不自然な切れを避けた

### 検証
- `file logs/stop-ack-supertonic-retry/phrase_tomemasu_ne.wav`
- `python` wave inspect: 44.1kHz / 16-bit / mono / 1756.8ms / 154,996 bytes
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで Supertonic F1 stop ack `はい、止めますね` が最後まで自然に聞こえることを確認する

## 2026-05-25 セッション28

### やること（開始時に書く）
- `assets/audio/stop_ack.wav` が「はい、止めます」の末尾「す」を欠いて聞こえる問題を、STT と波形で確認する
- 固定 WAV 自体が欠けている場合は Supertonic F1 で再生成または tail padding を入れて差し替える
- `StopAckAudioProvider` のテストに音声長 / tail silence を追加し、固定 WAV が末尾切れしにくいことを保証する

### やったこと
- 現行 Supertonic F1 版 `assets/audio/stop_ack.wav` を `local_whisper_mlx_small` にかけ、`四四四` と誤認識されることを確認した
- `はい、止めます。`、`はい、止めまーす。`、F2-F5 などの Supertonic 候補を生成して STT 比較したが、安定しなかった
- 明瞭性優先で `say -v Kyoko` 版へ戻し、`sox ... pad 0 0.30` で末尾 300ms の無音を追加した
- `tests/unit/test_stop_ack_audio.py` に 16kHz mono / duration / tail silence の検証を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に、Supertonic F1 採用を否定して Kyoko + tail silence 採用へ補正する追記を入れた

### 詰まったこと・解決したこと
- MLX Whisper は短い Supertonic 音声にかなり弱く、文字起こしだけでは完全な音質評価にはならない
  → ただし current asset の誤認識と Kyoko 版の安定認識の差が大きいため、control response は明瞭性優先で判断した
- 末尾が聞こえない問題が生成音声由来かブラウザ再生由来かを分けるため、固定 WAV に tail silence を入れて再生側の切れにも余裕を持たせた

### 検証
- `mise exec -- uv run python _tools/bench_stt_backends.py --backends local_whisper_mlx_small --runs 3 --audio-file assets/audio/stop_ack.wav --output logs/stop-ack-kyoko-clear/stt.json`
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_stop_intent_db.py`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで Kyoko stop ack が最後まで聞こえることを確認する

## 2026-05-25 セッション27

### やること（開始時に書く）
- `assets/audio/stop_ack.wav` を Supertonic-3 CoreML F1 voice style で再生成して差し替える
- Kyoko 生成と記録していた Phase 10.9 の実装結果・latency note を Supertonic F1 生成へ補正する
- 固定 WAV 読み込みテストと `ruff check .` / `pytest -m unit tests/unit/test_stop_ack_audio.py` で確認する

### やったこと
- `_tools/bench_tts_backends.py --targets supertonic_coreml_f1 --text 'はい、止めます' --output-dir logs/stop-ack-supertonic-f1` で Supertonic-3 CoreML F1 の固定 WAV を生成した
- `logs/stop-ack-supertonic-f1/supertonic_coreml_f1.wav` を `assets/audio/stop_ack.wav` へコピーして、Kyoko 版を置き換えた
- `PLAN.md` / `_docs/latency.md` / `MEMORY.md` に、Kyoko 生成を否定して Supertonic F1 採用へ補正する追記を入れた

### 詰まったこと・解決したこと
- Supertonic F1 の出力は 16kHz ではなく 44.1kHz mono PCM WAV だった
  → 既存の fixed WAV provider と Web Audio 再生経路は WAV container をそのまま扱うため、Supertonic 生成物をそのまま採用した

### 検証
- `file assets/audio/stop_ack.wav`
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check .`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで stop ack の音量・声質・再生タイミングを確認し、必要なら固定 WAV の speed / total_step を調整する

## 2026-05-25 セッション26

### やること（開始時に書く）
- Phase 10.9 全体として、online parallel stop-intent queue と固定 WAV 停止応答を実装する
- PostgreSQL queue / store、background classifier worker、`SessionEvent(type="stop_intent_classified")`、固定 WAV `StopAckAudioProvider` をテスト先行で接続する
- `pytest -m unit tests/unit/test_stop_intent_queue.py tests/unit/test_stop_ack_audio.py tests/unit/test_phase105_session_runtime.py`、integration DB test、全 unit で完了確認する

### やったこと
- `stop_intent_observations` / `stop_intent_shadow_signals` DDL と `stop_intent_shadow_analysis` view を追加した
- `PostgresStopIntentStore` / `InMemoryStopIntentStore`、rule / embedding / LLM classifier、`StopIntentClassifierWorker` を追加した
- LLM classifier は `asyncio.Semaphore(1)` で最大1同時にし、PostgreSQL 側は `FOR UPDATE SKIP LOCKED` で二重処理を避けるようにした
- `TomoroSession` が stop / wait / withdraw 候補 transcript から observation insert command を作り、classifier result を `stop_intent_classified` event として stale check するようにした
- 高信頼 stop 採用時は reply/TTS cancel、`audio_control stop`、固定 WAV「はい、止めます」の audio turn 送信で停止応答を完了するようにした
- `assets/audio/stop_ack.wav` を追加した。生成コマンドは `say -v Kyoko --data-format=LEI16@16000 -o assets/audio/stop_ack.wav 'はい、止めます'`
- `PLAN.md` の Phase 10.9 チェックボックス、`MEMORY.md`、`_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- `process_transcript()` は既存の direct async path なので、SessionEvent の reducer だけでは observation insert を表せなかった
  → `SessionCommand(type="insert_stop_intent_observation")` を internal command として作り、hot path では DB insert のみに閉じた
- 固定 WAV は通常返答ではなく control response なので、`conversation_logs` には保存せず audio turn と playback telemetry だけ既存経路に乗せた

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_intent_queue.py tests/unit/test_stop_ack_audio.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_stop_intent_db.py`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- Chrome 実セッションで「その話いったん置いといて」「今は聞けない」系が observation と shadow signal に残り、間に合う場合だけ fixed WAV stop ack へ切り替わることをログで確認する

## 2026-05-25 セッション25

### やること（開始時に書く）
- Phase 10.8 全体として、`AudioTurnController` を公開 API だけで進む純粋な制御対象に寄せる
- `TomoroSession` の audio turn pass-through helper と private helper 依存テストを削り、reply / precomputed reply の出力順序を public behavior で固定する
- `pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py` と全 unit で完了確認する

### やったこと
- `TomoroSession` の audio turn pass-through helper を削除し、reply generation / precomputed reply / hard interrupt が `AudioTurnController` の public API を直接使うようにした
- `tests/unit/test_session_concurrency.py` を private helper 直呼びから public behavior 検証へ補正した
- `PLAN.md` の Phase 10.8 チェックボックスを完了へ更新し、`ARCHITECTURE.md` / `MEMORY.md` に責務境界を追記した

### 詰まったこと・解決したこと
- precomputed reply は先に attention event を出すため、テストでは `reply_text` 以降の audio output 順序を検証する形にした
- Phase 10.8 のチェックボックス更新時に Phase 10.9 の同名検証項目へ一時的に巻き込みが出たため、未着手状態へ戻した

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

### 次のセッションでやること
- Phase 10.9 を実装する場合は、stop-intent queue の DDL / store / fixed WAV command 境界をテスト先行で固定する

## 2026-05-25 セッション24

### やること（開始時に書く）
- Phase 10.7 全体として、candidate runtime hard gate の所有者を `TomoroSession` に集約する
- `CandidateSpeakPolicy` / `CandidateCommandRunner` から runtime state 依存を外し、外側は soft decision と event 変換だけを担当する形へ戻す
- policy test と session final gate test を先に固定し、`pytest -m unit` で完了確認する

### やったこと
- `CandidateSpeakPolicy.evaluate()` から `TomoroRuntimeState` 依存を外し、soft score と candidate metadata 条件だけを見る形にした
- `CandidateCommandRunner` から `session.get_now_state()` を読む runtime gate を削除し、candidate fetch / policy decision / event post に寄せた
- `TomoroSession` の candidate final gate reason を `gate_reason` として emission payload に残し、candidate loaded 後の gate block を log に出すようにした
- policy が `speak` でも attention / VAD / playback / audio target の状態で `TomoroSession` が止める unit test を追加した
- `PLAN.md` の Phase 10.7 チェックボックスと実装結果、`MEMORY.md` の確定判断を追記した

### 詰まったこと・解決したこと
- candidate text readiness / expiry は runtime hard gate ではなく candidate metadata 条件として policy 側に残した
- fetch 前の早期 skip は `TomoroSession` 内の DB fetch 削減であり、runner / adapter 側の authoritative gate ではない形を維持した

### 検証
- `mise exec -- uv run ruff check server/gateway/initiative_policy.py server/gateway/candidate_commands.py server/session.py tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- Phase 10.8 に進む場合は、`AudioTurnController` への薄い delegate と private helper 依存テストを public behavior 検証へ寄せる

## 2026-05-25 セッション23

### やること（開始時に書く）
- markdown 編集制限の一時解除を受け、online parallel stop-intent classifier と固定 WAV stop acknowledgement の Phase を PLAN.md に追記する
- LLM 推論は最大1同時のキュー、データソースは PostgreSQL、会話停止は `TomoroSession` 経由で行う方針を明文化する

### やったこと
- `PLAN.md` に Phase 10.9「online parallel stop-intent queue と固定 WAV 停止応答」を追記した
- 既存ルール即停止を維持しつつ、embedding / LLM classifier を PostgreSQL queue 経由の online background worker として扱う方針を明文化した
- LLM 推論は最大1同時、classifier result は stale check 付き `SessionEvent` として `TomoroSession` に戻す方針にした
- 高信頼 stop が間に合った場合は、TTS / reply が進んでいても固定 WAV「はい、止めます」で会話停止を完了する方針にした

### 詰まったこと・解決したこと
- queue を新しい外部基盤にせず、PostgreSQL の observation / signal table と `FOR UPDATE SKIP LOCKED` で軽量に始める方針にした
- 固定 WAV は通常会話ログではなく control response として扱い、回り込みは既存 playback echo protection に乗せる方針にした

### 検証
- `git diff --check -- PLAN.md LOG.md`

### 次のセッションでやること
- Phase 10.9 実装時は DDL / store / worker concurrency test から始め、`TomoroSession` へは stale-safe な advisory event と fixed WAV command を最後に接続する

## 2026-05-25 セッション22

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、情報の流れが崩れている `TomoroSession` / `AudioTurnController` 境界の整理 Phase を PLAN.md に追記する
- candidate 発話 gate の所有者を `TomoroSession` に寄せ、`CandidateSpeakPolicy` / runner / main 側の runtime hard gate を削る Phase を PLAN.md に追記する

### やったこと
- `PLAN.md` に Phase 10.7「candidate runtime gate の所有者を TomoroSession に集約する」を追記した
- `PLAN.md` に Phase 10.8「AudioTurnController を純粋な制御対象に寄せる」を追記した
- Phase 10.6 の runtime hard gate 方針と Phase 6.6.4 の thin delegate 温存方針を、追記で明示的に補正した

### 詰まったこと・解決したこと
- candidate policy は soft decision に寄せ、runtime hard gate の正は `TomoroSession` にだけ置く方針に整理した
- audio turn は `TomoroSession` の意味判断から命令される制御対象とし、private helper 直呼びや薄い delegate を削る方針に整理した

### 検証
- `git diff --check -- PLAN.md LOG.md`

### 次のセッションでやること
- Phase 10.7 を実装する場合は、policy test から runtime hard gate 期待を削除し、`TomoroSession` final gate の unit test を先に固定する
- Phase 10.8 を実装する場合は、`tests/unit/test_session_concurrency.py` を private helper 依存から public behavior 検証へ移す

## 2026-05-25 セッション21

### やること（開始時に書く）
- Phase 10.6 配下の残りとして、ユーザーフィードバックを source / topic / emotional_need 別に保持して policy に反映する
- `judge_initiative_candidate` command に実 LLM judge runner を接続し、境界ケースだけ JSON judge を使えるようにする
- `TomoroSession` は引き続き final gate のみを担当し、DB read / LLM 実行は runner 側に閉じる

### やったこと
- `initiative_feedback_signals` DDL と `PostgresCandidateFeedbackStore` / `InMemoryCandidateFeedbackStore` を追加した
- `CandidateFeedbackScope` / `CandidateFeedbackSummary` DTO を追加し、source / topic / emotional_need bucket ごとに feedback を集計できるようにした
- `TomoroSession.start_precomputed_reply()` に `feedback_scope` を渡せるようにし、自発発話後の「それ今じゃない」「静かにして」「うん、なに？」系 transcript を feedback signal として保存するようにした
- `CandidateCommandRunner` が active candidate ごとに feedback summary を読み、metadata の `feedback_penalty` / `feedback_boost` と speakability に反映してから `CandidateSpeakPolicy` を実行するようにした
- `InitiativeLLMJudge` を追加し、`judge_initiative_candidate` command が設定済み judge を通して JSON result を `SessionEvent` として戻せるようにした
- `server/edge/main.py` で central / edge gateway の candidate runner に feedback store と LLM judge を接続した
- ローカル PostgreSQL に `docker/postgres/init/011_initiative_feedback.sql` を適用した

### 詰まったこと・解決したこと
- feedback を source だけで効かせると同じ source の別 topic まで強く抑制される
  → source は弱め、topic は強め、emotional_need bucket は中程度に重み付けして summary 化した
- 境界ケースの LLM judge に渡す desire / speakability snapshot は command payload へ直接持たせず、policy decision の signals から復元できるようにした

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase106_initiative_feedback_db.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで自発発話後に「それ今じゃない」などを返し、次回 candidate score / decision log が下がることをログで確認する

## 2026-05-25 セッション20

### やること（開始時に書く）
- Phase 10.6 TomokoDesire / Speakability model を、DTO・load average 更新器・Personality 補正・CandidateSpeakPolicy・runtime 接続の順で実装する
- 自発発話を highest priority candidate 直行ではなく、desire / speakability / candidate metadata の決定的 policy で説明できる形にする
- `TomoroSession` には重い DB / LLM 判断を持たせず、既存の final gate と stale result policy を維持する

### やったこと
- `TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` / `CandidateSpeakMetadata` / `CandidateSpeakDecision` DTO を追加した
- `DesireLoadAverages` / `SpeakabilityLoadAverages` / `CandidateSpeakPolicy` を追加し、desire / speakability / personality / candidate metadata から `speak` / `wait` / `needs_llm_judge` を決定できるようにした
- `CandidateCommandRunner` が active candidate fetch 後に policy snapshot を組み立て、decision log を残して `TomoroSession` へ返すようにした
- `TomoroSession` で `policy_wait` と `initiative_llm_judge_requested` を扱い、judge 待ちの request id を stale result check に残すようにした
- LLM judge 用 JSON schema prompt builder / parser を追加し、未設定・malformed result は安全側に `wait` へ倒すようにした
- `PLAN.md` の Phase 10.6 チェックボックスと実装結果、`MEMORY.md` の確定判断を追記した

### 詰まったこと・解決したこと
- 最初の実装では `needs_llm_judge` に入る前に initiative request id を消していた
  → judge result が戻るまで request id を保持し、final `wait` / `speak` で clear するようにした
- `policy_wait` では candidate を dismiss しない
  → desire / speakability が変われば次回以降に話せる余地を残すため

### 検証
- `mise exec -- uv run ruff check server/gateway/initiative_policy.py server/gateway/candidate_commands.py server/session.py server/shared/models.py tests/unit/test_phase106_initiative_policy.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### 次のセッションでやること
- 実会話ログから rejection / acceptance feedback を source / topic / emotional_need に結びつける永続化を追加する
- 必要になった時だけ、`judge_initiative_candidate` command に実 LLM judge runner を接続する

## 2026-05-25 セッション19

### やること（開始時に書く）
- 複数クライアント同時対応を見据え、`TomoroSession` が WebSocket 実体ではなく接続状況の抽象 state を持てるようにする
- 接続がない時に initiative / arrival の online 発話が始まらない hard gate を追加する
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に接続状態と output target の方針を追記する

### やったこと
- `ClientConnection` / `ConnectedOutputState` DTO と `ClientConnectionRegistry` を追加した
- `TomoroRuntimeState.output_state` と `connected_output_state_changed` event を追加し、Session が接続 snapshot を観測できるようにした
- initiative / arrival の candidate fetch gate に `audio_target_available` を追加した
- `/ws` / `/edge/ws` で接続時の output state を Session に渡すようにした
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に接続状態と output target の方針を追記した

### 詰まったこと・解決したこと
- `TomoroSession` に WebSocket object や送信先一覧を持たせる案は否定し、adapter / gateway 側の registry が facts だけを集約する形にした
- 現時点では WebSocket 接続ごとに Session を作る構造を維持し、long-lived central Session 化は別 Phase に残した

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- long-lived central Session へ進む場合は、registry snapshot を既存 Session へ継続的に流す drain loop と output routing policy を別 Phase として切る

## 2026-05-25 セッション18

### やること（開始時に書く）
- markdown 編集制限の一時解除を受け、自発発話の「話したい欲」「発話可能性」「ユーザーフィードバック」「LLM 状況判断」の設計を文書化する
- `TomoroSession` にオンライン推論を増やさず、状態管理は決定的に保つ方針を `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に反映する

### やったこと
- `ARCHITECTURE.md` に「自発発話の欲求と発話可能性モデル」を追加した
- `TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` / `CandidateSpeakPolicy` の責務を整理した
- LLM judge は常時判定器ではなく、score が境界帯の時だけ JSON で状況判断する方針にした
- `PLAN.md` に Phase 10.6「TomokoDesire / Speakability model」を追加し、DTO、load average 更新器、personality drift、policy、LLM judge、runtime 接続に分解した
- `MEMORY.md` に確定判断として、自発発話を desire / speakability / policy に分ける方針を追記した

### 詰まったこと・解決したこと
- 45 秒 idle timer は「固定間隔で話す仕組み」ではなく、候補取得・発話判断の poll 間隔として残す整理にした
- `ambient_logs` がないことは人がいない証拠ではなく、presence signal の弱い一部として扱う方針にした
- ランダム性は状態遷移へ直接入れず、personality mood が desire gain / decay / threshold を補正する形にした

### 検証
- `git diff --check`

### 次のセッションでやること
- Phase 10.6 実装時は DTO と純粋判定器の unit test から始める
- runtime 接続前に、手元の候補・presence・feedback サンプルで score の妥当性を確認する

## 2026-05-25 セッション17

### やること（開始時に書く）
- MLX STT lane と CoreML STT lane を、Supertonic CoreML TTS + LFM MLX 会話推論の横負荷下で比較できる負荷ベンチを追加する
- Ctrl-C まで継続しつつ、STT latency だけでなく横負荷側の elapsed も同時に見られるようにする

### やったこと
- `_tools/soak_voice_stack_scenarios.py` を追加し、MLX STT stack と CoreML STT stack を同じ Supertonic CoreML TTS + LFM MLX 横負荷で交互に測れるようにした
- default scenario は `local_whisper_mlx_small` と `local_whisperkit_serve_small` の STT lane だけを変え、TTS は `supertonic_coreml_f1`、会話推論は `local_lfm25_12b_jp_mlx` に固定した
- 実行中は STT avg / p95 / max と load avg / p95 を表示し、sample / error / summary を `logs/voice-stack-soak.jsonl` に追記するようにした
- smoke 用に `--max-cycles` を追加し、通常は 0 のまま Ctrl-C まで継続する仕様にした
- default 横負荷を Supertonic TTS x2 + LFM conversation x6 に増やし、`--load-tts-repeats` / `--load-conversation-repeats` / workers 指定でさらに詰められるようにした
- `make soak-voice-stack` と README の説明を追加した
- `tests/unit/test_voice_stack_soak_tool.py` で scenario 構成と STT/load 集計を固定した

### 詰まったこと・解決したこと
- 「Supertonic CoreML + CoreML TTS」は同じ CoreML TTS lane を指すものとして扱い、default の CoreML TTS load backend を `supertonic_coreml_f1` にした
- 既存 `ConcurrentLoadRunner` を再利用し、同じ TTS/LLM load backend を scenario ごとに重複ロードしないよう load key で共有した
- 1回ずつの横負荷では load が 80-90ms で終わり、M4 Max では GPU / ANE を詰めきれていなかった
  → voice stack 専用の `StackLoadRunner` に切り替え、測定ごとに同じ backend instance で繰り返し load を走らせるようにした

### 検証
- `mise exec -- uv run ruff check _tools/soak_voice_stack_scenarios.py tests/unit/test_voice_stack_soak_tool.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_voice_stack_soak_tool.py tests/unit/test_stt_soak_tool.py tests/unit/test_stt_bench_tool.py`
- `mise exec -- uv run python _tools/soak_voice_stack_scenarios.py --max-cycles 1 --status-interval-sec 2 --output logs/voice-stack-soak-smoke.jsonl`
  - MLX STT stack: 109.6ms、load 95.7ms、error 0
  - CoreML STT stack: 212.8ms、load 89.9ms、error 0
- `mise exec -- uv run python _tools/soak_voice_stack_scenarios.py --max-cycles 1 --status-interval-sec 2 --output logs/voice-stack-soak-stress-smoke.jsonl`
  - MLX STT stack: 305.3ms、load 1286.6ms、error 0
  - CoreML STT stack: 429.3ms、load 1258.1ms、error 0
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- 長時間の実判断では `make soak-voice-stack` を数分以上回し、STT lane の差だけでなく LFM first-token 側の tail も別途見る

## 2026-05-25 セッション16

### やること（開始時に書く）
- Ctrl-C で止めるまで STT を生成し続ける負荷ベンチスクリプトを追加する
- 既存の STT backend / concurrent load 設定を再利用し、長時間の avg / p95 / throughput を見られるようにする

### やったこと
- `_tools/soak_stt_backends.py` を追加し、Ctrl-C / SIGTERM で止めるまで STT backend を継続実行できるようにした
- 実行中は avg / min / max / recent p95 / qps / error count を定期表示し、sample / error / summary を `logs/stt-soak.jsonl` に追記するようにした
- 既存の STT benchmark と同じ backend 指定、sample 音声生成、`--load-tts-backend` / `--load-conversation-backend` の横負荷指定を再利用した
- `make soak-stt` と README の実行例を追加した
- `tests/unit/test_stt_soak_tool.py` で percentile / running stats / recent-window summary を固定した

### 詰まったこと・解決したこと
- 最初に `ruff check` の対象へ `Makefile` を混ぜてしまい syntax error 表示が出た
  → Python ファイルだけを対象にし、最終的には `ruff check .` で全体検証した
- smoke では `local_whisper_mlx_small` を 12.7 秒回し、125 回、avg 101.4ms、recent p95 104.7ms、error 0 で Ctrl-C summary 出力を確認した

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_soak_tool.py tests/unit/test_stt_bench_tool.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run python _tools/soak_stt_backends.py --backends local_whisper_mlx_small --status-interval-sec 2 --output logs/stt-soak-smoke.jsonl`

### 次のセッションでやること
- 実運用の負荷確認では WhisperKit serve を起動したうえで `local_whisper_mlx_small,local_whisperkit_serve_small` を長時間回し、TTS / 会話推論横負荷ありの分布を見る

## 2026-05-25 セッション15

### やること（開始時に書く）
- Supertonic-3 CoreML F1 を正式な TTS backend として追加する
- default TTS を Kokoro MLX から Supertonic CoreML F1 へ切り替える
- startup warm-up と bench tool から同じ backend を使えるようにし、実測を `_docs/latency.md` に記録する

### やったこと
- `server/shared/inference/tts/supertonic_coreml.py` を追加し、Supertonic-3 CoreML を `TTSBackend` として使えるようにした
- `FluidInference/supertonic-3-coreml` の `.mlpackage` は `models/supertonic-3-coreml` に実ファイルコピーして使うようにした
- `Reza2kn/supertonic-3-coreml` の `F1` voice style を自動補完して使うようにした
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の default `tts_backend` を `supertonic_coreml_f1` に切り替えた
- `_tools/bench_tts_backends.py` と STT concurrent load validator に `supertonic_coreml` を追加した
- `coremltools` と `huggingface-hub` を直接依存に追加した
- `models/` を `.gitignore` に追加し、CoreML model assets を repo に同梱しないようにした
- README の default TTS と optional model download 説明を Supertonic 採用に合わせて更新した

### 詰まったこと・解決したこと
- Supertonic の model card / CoreML package は OpenRAIL family なので、MIT repo に model weights を同梱しない
  → `models/` は gitignore し、`make download-optional-models` または初回起動時の取得に寄せた
- Supertonic は内部的な逐次 audio streaming ではない
  → Tomoko 側の sentence flush ごとに 1 WAV chunk を返す backend として採用した

### 次のセッションでやること
- Chrome 実セッションで Supertonic F1 の発話開始タイミング、回り込み、barge-in 停止を確認する
- 音量や話速が気になる場合は `speed` / `total_step` を実測しながら調整する

### 検証
- `mise exec -- uv run python _tools/bench_tts_backends.py --targets supertonic_coreml_f1 --text 'こんにちは、トモコです。今日は少しだけ話してみます。' --output-dir logs/tts-supertonic-coreml-backend`
  - warm-up 9210.3ms、first/total 104.7ms、1 chunk、audio 4345.2ms
- FastAPI startup warm-up smoke
  - STT 1552.4ms、Supertonic TTS 7963.4ms、LFM conversation 1088.2ms、BGE-M3 embedding 6607.1ms
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

## 2026-05-25 セッション14

### やること（開始時に書く）
- embedding backend を MIT / Apache 系で評判の良い候補へ切り替える
- psycopg の LGPL-3.0-only が実運用で何を要求するか整理する
- LFM / Supertonic など任意ダウンロード系モデルの扱い方を README / Makefile 方針として整理する

### やったこと
- embedding backend を `intfloat/multilingual-e5-small` から `BAAI/bge-m3` へ切り替えた
- `BackendSpec.dimensions` と `BGEM3Backend` を追加し、1024次元 mismatch を起動時に検出できるようにした
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の embedding backend を `local_bge_m3` に変更した
- `docker/postgres/init/006_bge_m3_embeddings.sql` を追加し、旧 e5 embedding を削除して pgvector columns を `vector(1024)` に移行するようにした
- ローカル PostgreSQL に migration を適用し、20件の conversation turn を BGE-M3 で backfill した
- `make download-models` と `make download-optional-models` を追加した
- README にモデル/依存ライブラリのライセンス扱い、psycopg LGPL、LFM / Supertonic optional download 方針を追記した

### 詰まったこと・解決したこと
- BGE-M3 は 1024次元なので、既存の `vector(384)` schema には入らない
  → 旧 embedding を削除し、DB column を `vector(1024)` に移行して再 backfill する migration にした
- LFM / Supertonic は MIT / Apache ではない
  → permissive model download と optional custom/OpenRAIL download を Makefile target で分けた

### 次のセッションでやること
- BGE-M3 採用後の memory search 品質を実会話ログで確認する
- session summary embedding は次回 summarizer 実行時に BGE-M3 で再生成する

### 検証
- BGE-M3 smoke: initial download + first embed 36990.5ms、cached fresh process first embed 7838.9ms、same process warm embed 32.8ms
- DB migration: `conversation_embeddings.embedding` / `conversation_sessions.summary_embedding` が `vector(1024)` になったことを確認
- Backfill: `conversation_embeddings` に `BAAI/bge-m3` 20件、`vector_dims=1024`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase86_session_summary_db.py`
- `git diff --check`

## 2026-05-25 セッション13

### やること（開始時に書く）
- Supertonic-3 CoreML の女性 voice style で日本語 smoke を実行し、音質評価用 WAV を出力する
- `F1`-`F5` の候補を同じ日本語文で生成し、latency と保存先を記録する
- smoke tool から女性 voice style を再現可能に使えるようにする

### やったこと
- `FluidInference/supertonic-3-coreml` には `voice_styles/M1.json` しか無かったため、`F1`-`F5` を含む `Reza2kn/supertonic-3-coreml` から女性 voice style JSON を取得した
- 既存 FluidInference CoreML model と `F1`-`F5` style JSON の互換性を確認し、全 voice で日本語 WAV を出力した
- `_tools/bench_supertonic_coreml_tts.py` に、missing voice style を `Reza2kn/supertonic-3-coreml` から補完する処理を追加した
- `tests/unit/test_supertonic_coreml_smoke_tool.py` に既存 voice style を再ダウンロードしない確認を追加した

### 詰まったこと・解決したこと
- 男性 `M1` は音質評価に不適切
  → `F1`-`F5` の女性 voice style を使い、`logs/supertonic-coreml-smoke/female/<voice>/ja-<voice>-run1.wav` を評価用サンプルにした

### 次のセッションでやること
- 人間が `F1`-`F5` の音質を聞き、許容 voice があれば `TTSBackend` 組み込み候補に進める

## 2026-05-25 セッション12

### やること（開始時に書く）
- Supertonic-3 CoreML TTS を単発 smoke し、日本語入力の生成可否、first/total latency、出力 WAV を確認する
- Tomoko 本体の TTS backend にはまだ組み込まず、`_tools` の候補評価に閉じる
- 結果を `_docs/latency.md` / `MEMORY.md` / `LOG.md` に残す

### やったこと
- HF `FluidInference/supertonic-3-coreml` の `infer.py` と `.mlpackage` 一式を取得した
- `_tools/bench_supertonic_coreml_tts.py` を追加し、CoreML model を通常ディレクトリへ実ファイルコピーしてから smoke できるようにした
- 日本語 `ja` / voice style `M1` / `CPU_AND_NE` で 5 runs 実測した
- 生成 WAV と summary JSON を `logs/supertonic-coreml-smoke/` に保存した
- `tests/unit/test_supertonic_coreml_smoke_tool.py` を追加し、集計 helper を unit test した

### 詰まったこと・解決したこと
- HF cache の `.mlpackage` が symlink になっており、`coremltools` prediction 時に CoreML compiler が `weight.bin` を見失った
  → `shutil.copytree(..., symlinks=False)` で実ファイルとして `logs/supertonic-coreml-smoke/model` に展開してから実行する CLI にした
- smoke 結果は model load 5533.7ms、warm synth avg 102.4ms、4.35秒音声で RTFx 38.5-43.9x

### 次のセッションでやること
- 出力 WAV を人間が聞いて、日本語品質と Tomoko らしい声として使えるかを確認する
- 音質が許容なら `TTSBackend` として組み込み、sentence flush 時の first audio latency を測る

## 2026-05-25 セッション11

### やること（開始時に書く）
- MLX Whisper と WhisperKit serve CoreML の STT latency を 30 runs で実測する
- idle / `kokoro_mlx` TTS 同時 / `kokoro_mlx` + LFM MLX 同時の p50 / p95 / max を比較する
- 平均ではなく tail latency を見て、CoreML STT を固定 200ms 枠として扱えるか確認する

### やったこと
- `_tools/bench_stt_backends.py` を `--runs 30` で3条件実行した
- idle 結果を `logs/stt-mlx-coreml-runs30-idle.json` に保存した
- `kokoro_mlx` TTS 同時負荷結果を `logs/stt-mlx-coreml-runs30-tts.json` に保存した
- `kokoro_mlx` + LFM MLX 同時負荷結果を `logs/stt-mlx-coreml-runs30-tts-llm.json` に保存した
- JSON の raw runs から avg / p50 / p95 / max を集計した

### 詰まったこと・解決したこと
- 前回3 runs では `kokoro_mlx` 同時負荷時に MLX Whisper max 300.5ms が出たが、30 runs では p95 165.2ms / max 167.8ms で再現しなかった
- WhisperKit serve CoreML は全条件で p95 216-222ms 程度に収まり、固定 200ms レーンとしては安定している
- ただし今回の30 runsでは、同時負荷時でも MLX Whisper の p95 が CoreML より速く、default を CoreML に変える理由はまだ弱い

### 次のセッションでやること
- さらに判断する場合は、より長い TTS 文、実録音 WAV、barge-in 中の STT など、実会話に近い負荷で再測定する

## 2026-05-25 セッション10

### やること（開始時に書く）
- STT benchmark CLI を単体 latency だけでなく、MLX LLM / TTS と同時実行した時の latency を測れる形へ拡張する
- CoreML STT が GPU/MLX workload と競合しにくいかを、同じサンプル音声と同じ測定手順で比較できるようにする
- 変更は bench tool と unit test に閉じ、実運用 backend / TomoroSession の挙動は変えない

### やったこと
- `_tools/bench_stt_backends.py` に `--load-tts-backend` / `--load-conversation-backend` / `--load-start-delay-ms` を追加した
- 各 STT 測定 run の直前に TTS / conversation workload task を起動し、その workload が走っている最中の STT latency を測るようにした
- JSON 出力に concurrent workload 設定と、各 run の `load_label` / `load_elapsed_ms` を保存するようにした
- helper unit test に concurrent load label と JSON 保存の確認を追加した
- `kokoro_mlx` 同時負荷、LFM MLX conversation 同時負荷、TTS + LLM 同時負荷の3条件を実測した

### 詰まったこと・解決したこと
- 短い LFM 生成は warm 状態だと 50ms 前後で終わり、STT と重なる時間が短かった
  → TTS 負荷も別条件として測り、MLX GPU/Metal 競合の影響が出るケースを分けて見られるようにした
- `kokoro_mlx` 同時負荷では MLX Whisper が 103ms 台から平均203.5msへ伸び、WhisperKit serve は平均215.9msでほぼ横ばいだった
  → CoreML STT は単体では遅いが、MLX TTS 同時実行時の余白としては評価する意味がある

### 次のセッションでやること
- Chrome 実セッションに近い録音 WAV と、実際の reply 長に近い TTS/LLM load text で tail latency を再測定する

## 2026-05-25 セッション9

### やること（開始時に書く）
- MLX Whisper と CoreML / WhisperKit serve STT backend を手元で比較できるベンチ CLI を追加する
- pytest perf ではなく、通常の `_tools` スクリプトとして warm-up / 複数回測定 / JSON 保存を行えるようにする
- 変更は bench tool と unit test に閉じ、STT backend / TomoroSession の挙動は変えない

### やったこと
- `_tools/bench_stt_backends.py` を追加し、config の backend 名から STT backend を生成して warmup + 複数回測定 + JSON 保存できるようにした
- `--backends` / `--runs` / `--text` / `--audio-file` / `--output` を受け取り、macOS `say` 生成音声または任意 WAV で比較できるようにした
- `tests/unit/test_stt_bench_tool.py` を追加し、backend 名 parsing / 測定値集計 / JSON の日本語保持を unit test で確認した
- `local_whisper_mlx_small` と `local_whisperkit_serve_small` を実測し、結果を `logs/stt-mlx-coreml-bench.json` に保存した

### 詰まったこと・解決したこと
- `write_json_summary()` が表より先に JSON path を表示して読みにくかった
  → CLI は表を先に出し、最後に `JSON: ...` を表示する形にした
- WhisperKit serve backend は今回も auto-start 後に `close()` で終了し、測定後に port 50060 が残っていないことを確認した

### 次のセッションでやること
- CoreML STT を default にする場合は、今回の CLI で Chrome 実セッションに近い録音 WAV を指定して再測定する

## 2026-05-25 セッション8

### やること（開始時に書く）
- WhisperKit `serve` を常駐させて叩く CoreML STT backend を追加する
- 既存の `mlx_whisper` / CLI 起動型 `whisper_coreml` と同じ STT 抽象に載せる
- 同じ `say` 合成音声で MLX Whisper と WhisperKit serve の速度ベンチを行う
- 変更は STT backend / config / bench / unit test に閉じ、TomoroSession には触れない

### やったこと
- `server/edge/pipeline/stt_whisperkit.py` に `WhisperKitServeSTT` を追加した
- `/health` が通る既存 WhisperKit server は再利用し、未起動なら backend が `whisperkit-cli serve` を起動するようにした
- `/v1/audio/transcriptions` に multipart `file` を送る形で transcription を実装した
- `server/edge/pipeline/stt_coreml.py` へ CLI 起動型 CoreML backend を分離し、`stt.py` を factory / MLX / faster-whisper 中心に戻した
- `config/central_realtime.toml` / `config/edge_kitchen.toml` に `local_whisperkit_serve_small` を追加した
- `tests/perf/test_stt_latency.py` の比較対象に `local_whisperkit_serve_small` を追加した
- `MEMORY.md` / `_docs/latency.md` に WhisperKit serve の判断と実測を追記した

### 詰まったこと・解決したこと
- WhisperKit server API は docs だけでなく実物確認した
  → `/` が endpoint 一覧を返し、`/v1/audio/transcriptions` は OpenAI 互換風の multipart `file` で動いた
- `stt.py` に CoreML 系を直接足すと 600 行超えになった
  → `stt_coreml.py` と `stt_whisperkit.py` に分割し、`stt.py` は 300 行未満に戻した

### 次のセッションでやること
- WhisperKit serve を default STT にする場合は、Chrome 実セッションで partial transcription / follow-up 誤起動を確認する
- 常駐 server を edge process の lifecycle で明示的に閉じる shutdown hook を追加するか検討する

### 検証
- WhisperKit serve API smoke: `GET /health` 200、`POST /v1/audio/transcriptions` で `ともこ 3たす3はいくつですか`
- STT perf: MLX warm 1111.8ms / measured 103.8ms、WhisperKit serve auto-start warm 4791.6ms / measured 214.3ms
- `mise exec -- uv run ruff check server/edge/pipeline/stt.py server/edge/pipeline/stt_coreml.py server/edge/pipeline/stt_whisperkit.py tests/unit/test_stt_backends.py tests/perf/test_stt_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py`
- `TOMOKO_STT_BENCH_BACKENDS=local_whisper_mlx_small,local_whisperkit_serve_small mise exec -- uv run pytest -m perf --tb=short tests/perf/test_stt_latency.py -s`

## 2026-05-25 セッション7

### やること（開始時に書く）
- Whisper を CoreML で動かす STT backend を追加し、既存 MLX Whisper と速度ベンチする
- Kokoro を CoreML で動かす TTS backend を追加し、既存 Kokoro MLX と速度ベンチする
- Kokoro は MLX / CoreML の聞き比べ用サンプル WAV を出力する
- 変更は backend 抽象、設定、ベンチツール、unit test に閉じ、TomoroSession の状態機械には触れない

### やったこと
- `WhisperCoreMLSTT` を追加し、`whisper-cli` / `whisperkit-cli` を設定の `command` で差し替えられるようにした
- `KokoroCoreMLBackend` を追加し、Python object の `generate_stream` または `kokoro say` CLI を使えるようにした
- `config/central_realtime.toml` / `config/edge_kitchen.toml` に CoreML STT / TTS backend 定義を追加した
- `_tools/bench_tts_backends.py` に `kokoro_mlx` / `kokoro_coreml` と `--targets` を追加した
- Kokoro CoreML の Japanese voice は `misaki[ja]` で IPA 化して CLI に渡すようにした
- `brew install whisperkit-cli jud/kokoro-coreml/kokoro` で実測用 CLI を導入した
- Kokoro MLX / CoreML の聞き比べ WAV を `logs/kokoro-mlx-coreml-bench/` に出力した
- `MEMORY.md` / `_docs/latency.md` に CoreML backend の制約と実測を追記した

### 詰まったこと・解決したこと
- Kokoro CoreML は Japanese voice に生テキストを渡すと CoreML shape error で落ちた
  → `misaki[ja]` で IPA 化し、`kokoro say --ipa` を使うことで合成できた
- Homebrew `kokoro` 0.11.0 は `--ipa` と `--stream` を同時に使えない
  → Japanese voice では stream を試した後、CLI error を検出して file generation に fallback する
- WhisperKit CLI は CoreML だが、現在の backend は transcription ごとに CLI を起動する
  → 実測値には process / model startup が乗るため、online 採用には `whisperkit-cli serve` など常駐化が必要

### 次のセッションでやること
- CoreML STT を実用候補にするなら、WhisperKit `serve` を起動済み前提にした persistent backend を追加する
- Kokoro CoreML の日本語 streaming が必要なら、`--ipa` + streaming が可能な runtime/API を探すか、MLX Kokoro を継続採用する

### 検証
- Kokoro TTS bench: MLX first 87.9ms / total 88.0ms、CoreML first 4816.4ms / total 4816.5ms
- STT perf: MLX measured 103.0ms、WhisperKit CoreML measured 4755.6ms
- `mise exec -- uv run ruff check server/shared/config.py server/edge/pipeline/stt.py server/shared/inference/tts/kokoro_coreml.py server/shared/inference/tts/__init__.py _tools/bench_tts_backends.py tests/unit/test_stt_backends.py tests/unit/test_kokoro_coreml_tts.py tests/perf/test_stt_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_kokoro_coreml_tts.py tests/unit/test_kokoro_mlx_tts.py tests/unit/test_phase0_config.py`
- `TOMOKO_STT_BENCH_BACKENDS=local_whisper_mlx_small,local_whisperkit_coreml_small mise exec -- uv run pytest -m perf --tb=short tests/perf/test_stt_latency.py -s`

## 2026-05-25 セッション6

### やること（開始時に書く）
- `lfm2.5-1.2b-jp-mlx` の `MLXLMBackend` 実モデル latency を実測する
- cold model load / warm-up / warm first text delta / total を測り、`_docs/latency.md` と `MEMORY.md` に追記する
- 実行構成の切り替えは必要最小限にし、Phase 10.5 の session runtime 実装には触れない

### やったこと
- 最初の `lfm2.5-1.2b-jp-mlx` model id では Hugging Face repo が見つからないことを確認した
- 公式 MLX repo id `LiquidAI/LFM2.5-1.2B-JP-MLX-bf16` に `config/central_realtime.toml` を補正した
- `MLXLMBackend` の `mlx_lm.stream_generate()` 呼び出しを、現行 `mlx-lm` API に合わせて `sampler=make_sampler(...)` 方式へ修正した
- `conversation_backend` を `local_lfm25_12b_jp_mlx` に切り替えた
- LFM backend 単体の cold / warm latency を `logs/lfm25-mlx-latency-smoke.json` に保存した
- FastAPI startup warm-up 経路で STT / TTS / LFM conversation / embedding の warm-up 時間を測り、`logs/lfm25-startup-warmup-smoke.log` に保存した
- `MEMORY.md` / `_docs/latency.md` に実 repo id と実測値を追記した

### 詰まったこと・解決したこと
- `lfm2.5-1.2b-jp-mlx` は短い呼び名としては通じるが、`mlx_lm.load()` に渡せる repo id ではなかった
  → 公式ページで確認できる `LiquidAI/LFM2.5-1.2B-JP-MLX-bf16` を採用した
- `stream_generate(..., temperature=0.0)` は現行 `mlx-lm` で `generate_step()` に通らなかった
  → `make_sampler(temp=0.0)` を作って `sampler=` として渡す形に修正した

### 次のセッションでやること
- Chrome 実セッションで `TomoroSession latency first_reply_text` と `first_audio_chunk` を確認し、体感品質と E2E を記録する

### 検証
- LFM backend smoke: cold first delta 4435.9ms / total 4485.4ms、warm first delta avg 26.6ms / total avg 76.0ms
- Startup warm-up: STT 2318.7ms、Kokoro 662.8ms、LFM conversation 3398.9ms、embedding 7291.9ms、total 13673.4ms
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 追記: ユーザー指定の LM Studio community 4bit 版へ切り替え
- ユーザー指定により `local_lfm25_12b_jp_mlx.model` を `lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit` に変更した
- Hugging Face model card で MLX / 4-bit / `mlx_lm` 利用可能であることを確認した
- LFM 4bit backend 単体の cold / warm latency を `logs/lfm25-4bit-mlx-latency-smoke.json` に保存した
- FastAPI startup warm-up 経路の結果を `logs/lfm25-4bit-startup-warmup-smoke.log` に保存した
- LFM 4bit smoke: cold first delta 19022.9ms / total 19041.4ms、warm first delta avg 20.6ms / total avg 39.0ms
- Startup warm-up: STT 1936.1ms、Kokoro 321.6ms、LFM 4bit conversation 3478.3ms、embedding 7085.3ms、total 12822.4ms
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-25 セッション5

### やること（開始時に書く）
- Phase 10.5 の残チェック項目から、自発発話用の開始理由と priority policy を `TomoroSession` state / command に寄せる
- `wake_word` / `followup` / `initiative` / `arrival` / `resume_unspoken` を runtime state と command payload の共通語彙として固定する
- hard interrupt / withdrawn / human transcript / stale result の優先順位を unit test で明示する

### やったこと
- `StartReason` を追加し、`TomoroRuntimeState.last_start_reason` で直近の開始理由を読めるようにした
- human transcript の `called` / `invited` を runtime 上では `wake_word` / `followup` に正規化した
- conversation session の `start_reason` も `wake_word` / `followup` に寄せた
- initiative / arrival の fetch / start / mark command payload に `start_reason` を追加した
- `resume_unspoken` は共通語彙として予約し、実際の再提示経路はまだ追加しない形にした
- priority policy のうち、hard interrupt > playback echo、withdrawn > follow-up / initiative、human transcript > delayed initiative、current request > stale result を unit test で固定した
- `PLAN.md` の該当チェックボックスを更新し、`MEMORY.md` に判断を追記した

### 詰まったこと・解決したこと
- `called` / `invited` を消すと ambient log や user turn の意味が崩れる
  → 参加モードとしては残し、runtime の開始理由だけ `wake_word` / `followup` に正規化した
- `resume_unspoken` はまだ発話経路がない
  → 今回は型と state の予約語に留め、実際の候補消費は別 Phase へ残した

### 次のセッションでやること
- resume_unspoken を実装する場合は、interrupted turn / diary candidate からの command result に `start_reason="resume_unspoken"` と turn/candidate id を持たせる
- LLM delta / TTS chunk まで stale 判定を広げる場合は、`turn_id` / `chunk_id` の result event 化を進める

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase85_conversation_sessions.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run ruff check server/shared/models.py server/session.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase85_conversation_sessions.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-25 セッション4

### やること（開始時に書く）
- 他セッションで進行中の Phase 10.5 runtime hardening に触れず、`lfm2.5-1.2b-jp-mlx` 用のメイン推論バックエンドを追加する
- 変更範囲は `InferenceRouter` / inference backend / config / unit test に限定し、`TomoroSession` の実装競合を避ける

### やったこと
- `server/shared/inference/backends/mlx_lm.py` を追加し、`mlx_lm.load()` / `mlx_lm.stream_generate()` を使う汎用 `MLXLMBackend` を実装した
- `InferenceRouter` に `type = "mlx_lm"` を追加した
- `config/central_realtime.toml` に `local_lfm25_12b_jp_mlx` backend を追加した
- 進行中の Phase 10.5 と衝突しないよう、default `conversation_backend` は `lmstudio_gemma4_e2b` のまま維持した
- `tests/unit/test_mlx_lm_backend.py` と router test を追加した
- `MEMORY.md` / `_docs/latency.md` に今回の判断と検証を追記した

### 詰まったこと・解決したこと
- 既存 `gemma_mlx` は `mlx-vlm` 専用実装なので、LFM までそこへ寄せるとモデル種別の境界が曖昧になる
  → causal LM 系の汎用 backend として `mlx_lm` type を分けた
- 10.5 作業中に active config を切り替えると実行確認の前提が変わる
  → backend 定義だけ追加し、切り替えは `conversation_backend` の 1 行変更に留めた

### 次のセッションでやること
- Phase 10.5 の作業が落ち着いた後、`conversation_backend = "local_lfm25_12b_jp_mlx"` に切り替えて startup warm-up と first text delta を `_docs/latency.md` に追記する

### 検証
- `mise exec -- uv run ruff check server/shared/inference/backends/mlx_lm.py server/shared/inference/router.py tests/unit/test_mlx_lm_backend.py tests/unit/test_router.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_mlx_lm_backend.py tests/unit/test_router.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-25 セッション3

### やること（開始時に書く）
- Phase 10.5: TomoroSession runtime hardening を実施する
- `post_event()` の内部 event queue / drain loop を追加し、arrival / initiative / playback telemetry / transcript event の処理順を TomoroSession に閉じ込める
- command 結果が再び `SessionEvent` として戻る既存境界を保ちつつ、priority / stale result の見通しを unit test で固定する

### やったこと
- `TomoroSession.post_event()` の内側に `_event_queue` / `_event_drain_lock` / `_drain_events()` / `_process_event()` を追加した
- 複数 `post_event()` が同時に来ても reducer と即時反映 command が enqueue 順に処理されることを unit test で固定した
- `fetch_initiative_candidate` / `fetch_arrival_candidate` command に `request_id` を追加した
- `CandidateCommandRunner` が DB read 結果を event として戻す時に request id を引き継ぐようにした
- 古い initiative / arrival result を `stale_result` として捨てるようにした
- 人間発話などで attention が `ambient` でなくなった後に遅れて届いた initiative result は、既存の `not_speakable` priority で抑制されることを test で固定した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に Phase 10.5 の実装結果を追記した

### 詰まったこと・解決したこと
- `post_event()` を queue 化すると戻り値をどう維持するかが問題になる
  → 各 event に `Future[TransitionResult]` を対応させ、public API はこれまで通り `TransitionResult` を返す形にした
- candidate の DB read 結果が遅れて戻ると、後続の fetch 結果や人間発話後の状態に混ざる可能性があった
  → fetch command payload に request id を入れ、result event 側で一致しないものを stale として捨てるようにした
- 個別 `SessionEvent` dataclass 化は現時点では効果より変更範囲が大きい
  → 文字列 event 契約は維持し、queue / stale result / priority test で見通しを確保する粒度に留めた

### 次のセッションでやること
- `SessionEvent` の payload contract がさらに増えたら、`TranscriptFinalized` / `PlaybackStarted` / `CommandFailed` などの個別 dataclass 化を検討する
- resume_unspoken を実装する時は、今回の request id / stale result と同じ方針で `turn_id` / `candidate_id` を command result に持たせる

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase885_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run ruff check server/session.py server/gateway/candidate_commands.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-25 セッション2

### やること（開始時に書く）
- バックグラウンドプロセス、日記、edge / gateway など別プロセスで起動する機能の Makefile entry をメンテする
- config / log file / once / watch の入口を揃え、dry-run と unit test で壊れにくくする

### やったこと
- `Makefile` に `CENTRAL_CONFIG` / `EDGE_KITCHEN_CONFIG` と各 background process 用ログファイル変数を追加した
- `server` / `gateway` / `edge-kitchen` の起動を config 変数経由に揃え、`gateway-reload` / `edge-kitchen-reload` を追加した
- `session-summarizer` / `persona-updater` / `thinker` / `journalist` の once / watch target が `--config $(CENTRAL_CONFIG)` を明示するようにした
- background process の一括入口として `background-once` / `background-watch` / `background-dry-run` を追加した
- `tests/unit/test_makefile_process_entries.py` を追加し、Makefile の別プロセス入口を unit test で固定した

### 詰まったこと・解決したこと
- `thinker` / `journalist` / summarizer / persona updater は CLI 側に `--config` があるが、Makefile は一部だけ暗黙 default に頼っていた
  → 起動 target から中央 config を明示し、config 切り替え時のズレを減らした
- `make background-watch` を依存 target にすると常駐 process が直列実行で止まる
  → watch は別ターミナルで起動する target 名を表示するだけにし、実行する集合 target は `background-once` に限定した

### 次のセッションでやること
- 実運用では `make gateway` と必要な background process を別ターミナルで起動し、ログファイルの分離が見やすいか確認する
- docker-compose service 化は Phase 9 / Phase 12 の既存判断通り、app image 方針が固まってから行う

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_makefile_process_entries.py`
- `make -n background-dry-run`
- `make -n gateway-reload edge-kitchen-reload background-watch background-once`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-25 セッション1

### やること（開始時に書く）
- Phase 14: edge / gateway 間の WebSocket text event protocol を実装する
- edge と中央をプロセス分離できるように、edge 側 adapter と gateway 側 adapter を追加する
- 中央サーバーにも従来のブラウザ client 機能を残し、単体利用できる状態を維持する
- unit test を先に追加し、Phase 14 の境界と互換性を固定する

### やったこと
- `server/shared/edge_protocol.py` を追加し、edge -> gateway の `hello` / `presence` / `speech` /
  `playback_started` / `playback_ended` JSON protocol を固定した
- 中央サーバーに `/edge/ws` を追加し、既存 `/ws` と `/` client 配信は維持した
- `GatewayEdgeProtocolHandler` を追加し、presence report、primary edge 判定、duplicate 判定、
  stale / duplicate event discard を通して fresh な `speech` だけを `TomoroSession.process_transcript()` へ渡すようにした
- `TomoroSession.process_transcript()` を追加し、ローカル STT 済み transcript と remote edge transcript の入口を共通化した
- edge role かつ `node.gateway_ws_url` がある場合の `/ws` を remote edge adapter として動かし、
  ブラウザ音声を VAD/STT 後に `speech` event として中央へ送るようにした
- edge は gateway から返る `reply_text` / `emotion` / `reply_done` をブラウザへ転送し、
  edge local TTS で audio chunk を生成してブラウザへ送るようにした
- edge role の startup warm-up は `gateway_ws_url` がある場合 STT/TTS までに留めた
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を追記した

### 詰まったこと・解決したこと
- `role="edge"` は既存テストでは旧来の単体サーバーも表していた
  → `node.gateway_ws_url` がある場合だけ remote edge として扱い、中央 inference warm-up を skip する条件もそこに限定した
- central TomoroSession で TTS まで実行すると音声 bytes が edge 外へ出る境界と衝突する
  → `/edge/ws` 経路では gateway 側 `tts_backend=None` とし、返答 text event だけを edge へ返して edge local TTS で再生する形にした

### 次のセッションでやること
- reconnect backoff / heartbeat / connection health UI を Phase 14 hardening として追加する
- 実 LAN で `/edge/ws` の `speech sent -> received`、`reply sent -> received`、first audio latency を `_docs/latency.md` に記録する
- 複数 edge 接続をまたいだ長時間 soak test を追加する

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase14_presence_db.py`
- `make -n edge-kitchen gateway`
- `git diff --check`

## 2026-05-24 セッション57

### やること（開始時に書く）
- Phase 14: エッジ分離 + 回り込み除去を、PLAN の小 Phase に沿って進められるところまで実装する
- Phase 14.0 presence / edge_status の DB / DTO / store 契約を固定する
- Phase 14.1 DirectSpeakerResolver と Phase 14.2 DuplicateSpeechFilter をテスト先行で追加する
- Phase 14.3 / 14.4 は、ブラウザを dumb client のまま保つ前提で local config / Makefile の足場まで進める

### やったこと
- Phase 14.0 presence / edge_status の初段を実装した
  - `presence_reports` / `edge_status` DDL を追加した
  - `PresenceReport` / `EdgeStatus` DTO を追加した
  - `InMemoryPresenceStore` / `PostgresPresenceStore` を追加した
  - 音声 bytes を保存しないことを unit / integration test で固定した
- Phase 14.1 DirectSpeakerResolver を実装した
  - audio level 最大、同値なら recency、さらに同値なら device_id で deterministic に primary edge を選ぶ
  - DB write を持たない純粋判定器として追加した
- Phase 14.2 DuplicateSpeechFilter を実装した
  - 時間窓、device 差、文字列類似度で duplicate を判定する
  - embedding 類似度は使わない
  - hard interrupt keyword は duplicate より優先する
- Phase 14.4 local multi-process smoke の足場を追加した
  - `config/edge_kitchen.toml`
  - `make edge-kitchen` / `make gateway`
  - `TOMOKO_CONFIG` で `server.edge.main` の config path を切り替えられるようにした
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- Phase 14 を一気に完全分離すると、edge / gateway 間 protocol と TomoroSession の配置まで巻き込んで大きくなる
  → 今回は DB 契約と純粋判定器、local 起動足場までに絞った
- ブラウザに判断ロジックを置く方向ではない
  → `edge_kitchen` も Python server として起動し、ブラウザは引き続き音声 chunk / playback telemetry の事実送信に留める

### 次のセッションでやること
- Phase 14 をさらに進めるなら、edge が STT 後 text event を gateway へ送る adapter と、gateway 側で `DirectSpeakerResolver` / `DuplicateSpeechFilter` を実際の online path に挟む
- Phase 15 に進む場合は、今回追加した `edge_kitchen.toml` に edge local LLM fallback を載せる

### 検証
- `mise exec -- uv run ruff check server/shared/presence.py server/gateway/resolver.py server/gateway/dedup.py server/gateway/presence.py server/edge/main.py tests/unit/test_phase14_edge_split.py tests/integration/test_phase14_presence_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase14_edge_split.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase14_presence_db.py`
- `make -n edge-kitchen gateway`
- `mise exec -- uv run python - <<'PY' ...`

## 2026-05-24 セッション56

### やること（開始時に書く）
- Phase 12 を PLAN の順番に進める
- 同日 diary 再生成方針を仮決定し、`MEMORY.md` / `PLAN.md` に追記する
- Phase 12.1 Journalist input builder をテスト先行で実装する
- Phase 12.2 Diary writer、Phase 12.3 DiarySource、Phase 12.4 local process / Makefile を進められるところまで実装する

### やったこと
- Phase 12.0 の同日 diary 再生成方針を version 方式に決めた
  - `diary_entries.diary_version` を追加した
  - 同じ `diary_date` の追加生成は `1, 2, 3...` と版を積む
- Phase 12.1 Journalist input builder を実装した
  - `JournalistInputSnapshot` / session summary / conversation turn / ambient digest / dismissed candidate DTO を追加した
  - `PostgresJournalistSourceReader` で日付範囲の材料を読むようにした
  - ambient は raw 全量ではなく count と短い抜粋だけに絞る
- Phase 12.2 Diary writer を実装した
  - `server/journalist/main.py` に `DiaryWriter` を追加した
  - `InferenceRouter.select("diary", "privacy")` で生成するようにした
  - 空出力は error として扱い、原本を変更しない
- Phase 12.3 DiarySource を実装した
  - 昨日または直近 diary から短い `CandidateSeed` を生成する
  - dedupe key は `diary:<diary_id>` とした
- Phase 12.4 local process / Makefile を実装した
  - `background-process/run_journalist.py`
  - `make journalist-once` / `make journalist`
- `build_default_thinker()` に `DiarySource` を追加し、日記由来 seed が thinker に入るようにした
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 同日 diary を overwrite すると、日記が解釈ログであるにもかかわらず過去解釈を失う
  → `diary_version` を積む方式にした
- docker-compose service 化は thinker と同じく app image 方針が未定で半端になる
  → Phase 12 では local process と Makefile target までを完了範囲にし、service 化は M4 へ送った

### 次のセッションでやること
- Phase 13 に進む場合は InferenceRouter 強化の未チェック項目を確認する
- Journalist を実運用で試す場合は `make journalist-once JOURNALIST_DATE=YYYY-MM-DD` で日記生成を確認する

### 検証
- `mise exec -- uv run ruff check server/shared/diary.py tests/unit/test_phase120_diary_store.py server/journalist/input.py server/journalist/main.py server/thinker/sources/diary.py server/thinker/main.py tests/unit/test_phase121_journalist_input.py tests/unit/test_phase122_journalist_writer.py tests/unit/test_phase123_diary_source.py tests/unit/test_phase124_journalist_process.py tests/unit/test_router.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase120_diary_store.py tests/unit/test_phase121_journalist_input.py tests/unit/test_phase122_journalist_writer.py tests/unit/test_phase123_diary_source.py tests/unit/test_phase124_journalist_process.py tests/unit/test_router.py`
- `mise exec -- uv run ruff check tests/integration/test_phase120_diary_db.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase120_diary_db.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `make -n journalist-once journalist`
- `mise exec -- uv run python background-process/run_journalist.py --help`

## 2026-05-24 セッション55

### やること（開始時に書く）
- Phase 11.3 の cached audio 送信順を PLAN 通り `reply_text` / `audio_start` / binary / `audio_end` / `reply_done` に修正する
- `generated_audio` は first RIFF/WAVE chunk cache のまま維持する判断を `PLAN.md` / `MEMORY.md` に反映する
- multi-chunk 完全事前生成は別テーブル方針として DB/DTO/store の足場を追加する
- Phase 11.0 / 11.3 の未チェック項目をテストで倒す

### やったこと
- `TomoroSession` の通常 TTS 経路と precomputed reply 経路を、`reply_text` / `audio_start` / binary / `audio_end` / `reply_done` の順序へ揃えた
- `UtteranceCandidate` の `maturity=2` を `generated_text` + `generated_audio` 必須として DTO で固定した
- `generated_audio` は first RIFF/WAVE chunk cache として維持し、完全 multi-chunk 事前生成用に `pregenerated_audio_chunks` 別テーブルを追加した
- `PregeneratedAudioChunk` DTO と `InMemoryPregeneratedAudioChunkStore` / `PostgresPregeneratedAudioChunkStore` を追加した
- `PLAN.md` / `MEMORY.md` に、前回の `reply_done` before `audio_end` 方針を否定する追記と別テーブル方針を記録した
- Phase 11.0 / 11.3 / 11.4 の実装済みチェックを更新した

### 詰まったこと・解決したこと
- 既存経路は `reply_done` が `audio_end` より先だったが、今回の人間判断により PLAN 経路を正とした
  → cached audio だけでなく通常 TTS 経路も同じ順序へ修正した
- `generated_audio` に multi-chunk を詰めると first chunk cache と完全事前生成 manifest の意味が混ざる
  → first chunk cache は維持し、multi-chunk は `pregenerated_audio_chunks` へ分離した

### 次のセッションでやること
- Phase 11 をさらに進めるなら、pregenerator が全 chunk を `pregenerated_audio_chunks` に保存し、gateway が順序付き multi-chunk cache を送る実装へ進む
- ただし現時点の online 消費は first chunk cache の `generated_audio` で成立している

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase110_pregenerated_candidate.py tests/unit/test_phase10_candidate_command_runner.py`
- `mise exec -- uv run ruff check server/session.py server/shared/candidate.py tests/unit/test_phase110_pregenerated_candidate.py tests/unit/test_phase10_candidate_command_runner.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase113_pregenerated_audio_consumption.py`
- `mise exec -- uv run ruff check tests/unit/test_phase113_pregenerated_audio_consumption.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

## 2026-05-24 セッション53

### やること（開始時に書く）
- Phase 10: 自発発話 + 入室時の初手を、テスト先行で進められるところまで実装する
- 必要になった場合だけ Phase 10.5 runtime hardening に進む
- Phase 11〜15 は、実装が迷わないように先に `PLAN.md` へタスク分解を追記してから順番に進める
- M4 完了条件を確認し、不足している対応を進める

### やったこと
- Phase 10 の online candidate consumption 初段を実装した
  - `TomoroSession` に `session_started` / `idle_timer_elapsed` / `initiative_candidate_loaded` / `arrival_candidate_loaded` reducer を追加
  - `CandidateCommandRunner` を追加し、candidate fetch / mark / start reply を command として実行するようにした
  - `/ws` 接続時に arrival fetch、45秒 idle loop で initiative fetch を投げるようにした
- Phase 11 を実装しやすい粒度へ `PLAN.md` に追記し、初段を実装した
  - `UtterancePregenerator` を追加
  - `ThinkerProcess.run_once()` / candidate loop に pregeneration step を追加
  - `generated_audio` 付き candidate を gateway で優先し、TTS を呼ばずに cached audio を送れるようにした
- Phase 12 を実装しやすい粒度へ `PLAN.md` に追記し、Phase 12.0 diary store 初段を実装した
  - `diary_entries` DDL
  - `DiaryEntry` / `DiaryStore` / `InMemoryDiaryStore` / `PostgresDiaryStore`
- Phase 13〜15 を実装しやすい粒度へ `PLAN.md` に追記した
- Phase 13.0 / 13.1 の monitor 初段を実装した
  - `inference_metrics` DDL
  - `InferenceMetricSample` / metric stores / `BackendHealthMonitor`
  - router が error metric を fallback 判断対象にできるようにした
- `MEMORY.md` と `_docs/latency.md` に今回の判断・検証結果を追記した

### 詰まったこと・解決したこと
- `generated_audio` に複数 chunk を完全保存するには単一 bytea では表現が弱い
  → Phase 11 初段では「最初の再生可能 RIFF/WAVE chunk cache」として扱い、完全な multi-chunk 事前生成は必要になった時に別テーブルまたは manifest を検討する方針にした
- precomputed reply の `reply_done` / `audio_end` 順序は Phase 11.3 の追記案と既存 TTS 経路でズレがあった
  → 今回は既存経路に合わせて `reply_text` → `audio_start` → binary → `reply_done` → `audio_end` とし、順序変更は既存 TTS 経路全体の互換性確認後に回した
- Phase 12 の同日 diary 再生成方針は overwrite か version か未確定
  → writer 実装前の残項目として `PLAN.md` / `MEMORY.md` に残した

### 次のセッションでやること
- Phase 10 は browser 実測で arrival / idle initiative の first audio latency を `_docs/latency.md` に追記する
- Phase 11 は multi-chunk 事前生成を現行 `generated_audio` のまま進めるか、別テーブル化するか判断する
- Phase 12 は diary 同日再生成方針を決めてから Journalist input builder / writer へ進む
- Phase 13 は metric store の integration smoke と periodic probe runner を追加する
- Phase 14 / 15 は今回の PLAN 分解に沿って DB/DTO から進める
- M4 完了条件は Phase 14 / 15 が未実装のため未達

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase10_session_contract.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase1_echo.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase94_thinker_loop.py tests/unit/test_phase111_pregenerator.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase120_diary_store.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase13_inference_monitor.py tests/unit/test_router.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション54

### やること（開始時に書く）
- Phase 10 配下で人間判断が必要だった項目への回答を `PLAN.md` / `MEMORY.md` に反映する
- initiative / arrival 発話だけでは conversation session を開始しないよう修正する
- 対応する unit test を追加して `pytest -m unit` を確認する

### やったこと
- Phase 10 の人間判断を `PLAN.md` と `MEMORY.md` に反映した
- `TomoroSession.start_precomputed_reply()` から conversation session 開始を外した
  - initiative / arrival 発話では attention だけ `engaged` にする
  - 人間が返事した時に通常の参加判断経路で conversation session を開始する
- 自発発話判断45秒は発話固定間隔ではなく候補取得判断間隔であることを明記した
- Phase 10.5 は今は実施しない、Phase 10 は unit 実装済みで完了扱いにする判断を記録した

### 詰まったこと・解決したこと
- 自発発話で attention を開くことと conversation session を開始することが混ざっていた
  → attention は開くが session は人間の返答で開始する形に分離した

### 次のセッションでやること
- Phase 10 の実 browser 確認は体験確認として別途行う
- Phase 11 以降へ進む場合は PLAN の小 Phase に従う

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase10_session_contract.py`
- `mise exec -- uv run ruff check server/session.py tests/unit/test_phase10_candidate_command_runner.py`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション52

### やること（開始時に書く）
- markdown 編集制限ルールの一時解除を受け、`PLAN.md` の Phase 10 を実装しやすい粒度へ分解する
- `TomoroSession` が candidate / arrival を消費する event / command 境界を明文化する
- LLM がテスト先行で迷わない完了条件とテスト観点を追記する

### やったこと
- `PLAN.md` の Phase 10 を 10.0〜10.4 に分解した
  - 10.0: initiative / arrival の session 契約
  - 10.1: 自発発話 candidate の消費
  - 10.2: arrival candidate の消費
  - 10.3: online adapter / command runner 接続
  - 10.4: regression / 完了判定
- Phase 10 では event queue / drain loop / 個別 event dataclass へ進まず、既存の `SessionEvent` / `SessionCommand` 文字列契約で候補消費を固定する方針を明記した
- timer / WebSocket / DB result は adapter が event に変換し、最終判断は `TomoroSession` に閉じることを明記した
- `MEMORY.md` に Phase 10 分解方針を追記した

### 詰まったこと・解決したこと
- 元の Phase 10 は「timer」「候補 cleanup」「on_session_start」が混ざっており、DB read/write を session 内で直呼びするか、メイン層で behavior 判断するかが曖昧だった
  → 先に event / command 契約を固定し、DB read/write は command runner、判断は `TomoroSession` に寄せる粒度へ分解した

### 次のセッションでやること
- Phase 10.0 実装時は `tests/unit/test_phase10_session_contract.py` を先に追加し、`session_started` / `idle_timer_elapsed` が返す command を固定する

### 検証
- `git diff -- PLAN.md LOG.md`
- `rg -n "Phase 10|Phase 10\\.0|Phase 10\\.1|Phase 10\\.2|Phase 10\\.3|Phase 10\\.4|Phase 10\\.5|session_started|idle_timer_elapsed|fetch_initiative_candidate|fetch_arrival_candidate|start_arrival_reply" PLAN.md`
- `git diff --check -- PLAN.md LOG.md`

## 2026-05-24 セッション51

### やること（開始時に書く）
- Phase 9 全体の完了条件を確認する
- Phase 9.0〜9.4 の実装、テスト、PLAN 上の未チェック項目を突き合わせる
- 不足があれば対応し、`pytest -m unit` など必要な検証を行う

### やったこと
- Phase 9.0〜9.4 の完了条件、実装結果、実ファイル、テストを突き合わせた
- Phase 9.4 の docker-compose service 追加は、現行 compose / app image 方針が未定のため M4 に送る項目であり、Phase 9 の不足として扱わないことを `PLAN.md` / `MEMORY.md` に追記した
- `tests/integration/test_phase90_candidates_db.py` が既存候補データに干渉されて落ちる問題を修正した
  - テスト用 `device_id` / `context_tags` で隔離した
  - 開始時と終了時にテスト用 row を削除し、commit するようにした
  - 既存 fresh arrival candidate と競合しないよう、テスト時刻を将来固定値にした

### 詰まったこと・解決したこと
- Phase 9.0 integration test が、実 DB に残っていた active / fresh candidate を先頭候補として拾って失敗した
  → store の全体 fetch 契約は維持し、テスト側を自分が挿入した row に限定して検証するよう修正した

### 次のセッションでやること
- Phase 10 に進む場合は、`TomoroSession` から candidate / arrival を消費する境界をテストで固定する
- docker-compose service 化は M4 で app image 方針が決まってから扱う

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run python background-process/run_thinker.py --help`
- `make thinker-once`
- `git diff --check`

## 2026-05-24 セッション50

### やること（開始時に書く）
- M3 Phase 9.4: thinker process loop を実装する
- `server/thinker/main.py` と `background-process/run_thinker.py` を追加し、candidate generation と arrival precompute を once / watch で動かせるようにする
- Makefile entry、loop 観測ログ、unit / smoke test を追加する
- `pytest -m unit` と thinker-once smoke で確認する

### やったこと
- `server/thinker/main.py` を追加した
  - `ThinkerProcess` で source → seed 保存 → evaluator → text-ready 保存を実行する
  - `arrival_precompute_loop` と `candidate_generation_loop` を追加し、watch では `asyncio.gather(...)` で並行実行する
  - generated seed count / inserted seed count / kept candidate count / arrival behavior / elapsed_ms / error count を log に出す
- `background-process/run_thinker.py` を追加し、`--once` / `--watch` / interval options を受けるようにした
- `Makefile` に `thinker` / `thinker-once` を追加した
- `tests/unit/test_phase94_thinker_loop.py` と `tests/integration/test_phase94_thinker_smoke.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- source / evaluator の失敗を例外で loop 全体へ伝播させると background process が止まる
  → Phase 9.4 では error count と log に閉じ、次 interval で回復できる形にした
- docker-compose service 追加は、現時点ではアプリ用 Docker image / Dockerfile がないため半端な定義になる
  → local process entrypoint と Makefile までで止め、M4 のインフラ安定化で app image 方針を決めてから追加する

### 次のセッションでやること
- Phase 10 に進む場合は、`TomoroSession` から candidate / arrival を消費する境界を先にテストで固定する
- docker-compose の thinker service は、app image 方針が決まってから追加する

### 検証
- `mise exec -- uv run ruff check server/thinker/main.py background-process/run_thinker.py tests/unit/test_phase94_thinker_loop.py tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase94_thinker_loop.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run python background-process/run_thinker.py --help`
- `make -n thinker thinker-once`
- `make thinker-once`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

## 2026-05-24 セッション49

### やること（開始時に書く）
- M3 Phase 9.3: arrival precompute を実装する
- `ArrivalPrecomputer` と arrival context / prompt / fallback 境界を追加する
- fresh arrival candidate 保存、LLM 失敗時 fallback、DTO round-trip、fake 構成 perf をテストで固定する
- `pytest -m unit` で確認する

### やったこと
- `server/thinker/arrival.py` を追加した
  - `ArrivalPrecomputer.precompute_once(now, device_id)` で 3 分 TTL の arrival candidate を保存する
  - active な urgent utterance candidate から `urgent_candidate_count` / `top_urgent_seeds` を組み立てる
  - optional な `ArrivalStatsReader` で session 統計と persona hint を注入できるようにした
- `ArrivalContextSnapshot` を Phase 9.3 schema へ更新した
  - `computed_at` / `local_time` / `time_since_last_session_sec` / `session_count_today` / `urgent_candidate_count` / `top_urgent_seeds` / `persona_hint`
  - 古い `observed_at` JSON は読み取り fallback として残した
- arrival prompt の出力 schema を `behavior` / `utterance_text` / `reason` に固定した
- LLM 失敗や invalid response は `wait_silent` fallback として保存するようにした
- `tests/unit/test_phase93_arrival_precompute.py` と `tests/perf/test_phase93_arrival_precompute_latency.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- Phase 9.0 の `ArrivalContextSnapshot` は arrival precompute 前の仮 schema だった
  → Phase 9.3 の必須項目へ更新しつつ、既存 JSON を読めるよう `observed_at` fallback を残した
- 入室前 context に session 統計をどう入れるかは DB 実装を増やすと Phase 9.3 の範囲を越える
  → 初段は `ArrivalStatsReader` protocol にして、Phase 9.4 以降の background loop / DB reader から差し込める形にした

### 次のセッションでやること
- Phase 9.4: thinker process loop で candidate generation と arrival precompute を定期実行する
- `ArrivalStatsReader` の実 DB reader が必要なら Phase 9.4 で追加する

### 検証
- `mise exec -- uv run ruff check server/shared/candidate.py server/thinker/arrival.py tests/unit/test_phase93_arrival_precompute.py tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase93_arrival_precompute.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase93_arrival_precompute.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`
- `git diff --check`

## 2026-05-24 セッション48

### やること（開始時に書く）
- M3 Phase 9.2: LLM evaluator を実装する
- `ThinkerEvaluationContext` / `EvaluatedUtterance` と `UtteranceEvaluator` 抽象を追加する
- `LLMUtteranceEvaluator` を追加し、privacy task / JSON schema / failure fallback を unit test で固定する
- `pytest -m unit` で確認する

### やったこと
- `ThinkerEvaluationContext` / `EvaluatedUtterance` を追加した
- `UtteranceEvaluator` 抽象と `LLMUtteranceEvaluator` を追加した
  - `InferenceRouter.select("candidate_gen", "privacy")` を使う
  - JSON schema は `should_keep` / `generated_text` / `priority` / `urgent` / `reason`
  - malformed JSON や backend failure は `None` として破棄する
- `InferenceRouter` と config に `candidate_gen` backend / fallback を追加した
- `CandidateStore.insert_evaluated_utterance_once()` を追加した
  - `should_keep=false` と evaluator failure は保存しない
  - `should_keep=true` は `maturity=1` candidate として保存する
- `tests/unit/test_phase92_llm_evaluator.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 既存の Phase 9.1 seed 保存と text-ready 保存を同じ dedupe key で扱うと、maturity 0 の seed が maturity 1 保存を塞ぐ可能性がある
  → Phase 9.2 の `insert_evaluated_utterance_once()` では maturity 1 以上の active candidate だけを dedupe 対象にした
- LLM evaluator に会話原文や DB row を渡すと境界が重くなる
  → `ThinkerEvaluationContext` の要約・用語・人格 subset だけを渡す DTO 境界にした

### 次のセッションでやること
- Phase 9.3 に進む場合は arrival precompute を実装する
- Phase 9.4 の thinker process loop で source → evaluator → store を常駐処理として接続する

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_router.py tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run ruff check server/shared/candidate.py server/shared/config.py server/shared/inference/router.py server/thinker tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-24 セッション47

### やること（開始時に書く）
- M3 Phase 9.1: deterministic source / selection を実装する
- `CandidateSeed` / `ThinkerSourceContext` と `InformationSource` を追加する
- deterministic `TimeBasedSource` と `HighestPriority` selection を追加し、dedupe 方針を unit test で固定する
- `pytest -m unit` で確認する

### やったこと
- `server/shared/candidate.py` に `CandidateSeed` / `ThinkerSourceContext` を追加した
- `server/thinker/sources/base.py` と `server/thinker/sources/time_based.py` を追加した
  - `TimeBasedSource` は時刻 bucket だけから deterministic seed を返す
  - 外部 API / LLM / DB read は呼ばない
- `server/thinker/selection/base.py` と `server/thinker/selection/highest.py` を追加した
  - priority 降順、urgent 優先、expires_at 昇順、created_at 昇順で選ぶ
- dedupe は `context_tags` の `dedupe:<dedupe_key>` で固定した
  - active candidate に同じ dedupe tag があれば insert しない
  - spoken / dismissed 済みは再生成可能にした
- `tests/unit/test_phase91_deterministic_sources.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- dedupe_key の保存先は専用カラムも考えられるが、Phase 9.1 では schema を増やす圧がまだない
  → `context_tags` に `dedupe:<dedupe_key>` を保存し、後続で検索圧や DB 一意性が必要になったら専用列 / index を検討する方針にした

### 次のセッションでやること
- Phase 9.2 に進む場合は `UtteranceEvaluator` / `ThinkerEvaluationContext` / `EvaluatedUtterance` から実装する
- LLM evaluator failure は online 会話を止めず、失敗 seed を捨てるか log に残すだけにする

### 検証
- `mise exec -- uv run ruff check server/shared/candidate.py server/thinker tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

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

## 2026-05-25 セッション28

### やること（開始時に書く）
- Perplexity / Codex Computer Use による外部情報収集を、Tomoko 本体から切り離した Phase として PLAN.md に追記する
- 生 Markdown を filesystem に残し、Tomoko の解釈を PostgreSQL に残す二層構造を明文化する
- 不安定な Markdown / Computer Use / LLM normalize を前提に、validate / failed / archived の取り込み手順へ分解する

### やったこと
- `PLAN.md` に Phase 18「外部観測 Markdown と Tomoko 解釈パイプライン」を追記した
- `informations/work` / `archived` / `failed` / `prompts` の directory contract を定義した
- raw Markdown artifact、normalizer、DB schema、ingest Makefile、Tomoko persona interpretation、thinker / journalist 連携、Perplexity / Codex Computer Use recipe の順に小 Phase へ分解した

### 詰まったこと・解決したこと
- Perplexity の Markdown 出力や Codex Computer Use 操作は安定しない前提にした
  → Tomoko 本体へ直接接続せず、raw artifact と schema validation で隔離する方針にした
- ルールベースでニュース内容を理解する方針は否定した
  → ルールは外枠 validation と file movement に限定し、内容理解は LLM normalize / interpretation worker に寄せる方針にした

### 検証
- `git diff --check -- PLAN.md LOG.md`

### 次のセッションでやること
- Phase 18.0 を実装する場合は、`informations/` directory、`.gitignore`、sample artifact、frontmatter schema validator の unit test から始める

## 2026-05-25 セッション37

### やること（開始時に書く）
- `vad_silence_ms` を 800ms に戻し、1000ms との体感差を比較できるようにする
- 新しい `audio_start` が来た時、古い turn の再生キューと `nextPlaybackTime` が残って次の返答を遅らせないようにする
- 短い相槌で Supertonic CoreML TTS が遅く見える件をログ観測ベースで整理する

### やったこと
- `config/central_realtime.toml` の `vad_silence_ms` を 800ms に戻した
- `audio_start` で turn が切り替わる時、クライアントが古い再生キューを停止し `nextPlaybackTime` を現在時刻へ戻すようにした
- config 契約テストの期待値を 800ms に更新した

### 詰まったこと・解決したこと
- 1000ms は発話の食い込み対策としては効くが、発話終了後の体感待ちが増えるため比較用に否定して 800ms へ戻した
- `ruff` を JS ファイルに直接当てると Python として解釈されるため不適切だった
  → JS は `node --check`、Python は `ruff check .` で確認した

### 検証
- `node --check client/main.js`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_streaming_tts_pipeline.py`
  - 7 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 297 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` の実ブラウザ体感で、800ms が発話被りと返答遅れのバランスとして良いか確認する
- Supertonic F1 の短文相槌で TTS 外れ値が再現するなら、固定相槌音声キャッシュまたは短文用バックエンドを検討する

## 2026-05-25 セッション38

### やること（開始時に書く）
- WhisperKit serve の STT backend を large 系モデルへ切り替え、small 由来の誤認識を減らせるか比較する
- 既存 small serve と混ざらないよう、large 用 backend は別 port で起動できる設定にする
- `prompts/base_persona.md` を音声会話向けに調整し、聞き取り不確実時の確認・短い自然な応答・メタ発話への受け答えを明示する

### やったこと
- `config/central_realtime.toml` の active STT backend を `local_whisperkit_serve_large` に変更した
- `local_whisperkit_serve_large` を追加し、`large-v3-v20240930_626MB` を `127.0.0.1:50061` で serve する設定にした
- `prompts/base_persona.md` に音声会話向けのルールを追加した
  - 聞き取りが怪しい時は断定せず確認する
  - Tomoko の動作や遅延についての発話には、開発中のTomokoとして一緒に確認する
  - 相槌だけで終わらせず、必要なら短い確認質問を一つだけ添える
- config と persona prompt の契約テストを更新した

### 詰まったこと・解決したこと
- WhisperKit serve は起動時の `--model` が重要なので、small と同じ port だと既存 small server を健康と見なしてしまう可能性がある
  → large backend は `50061` に分け、small server と混ざらないようにした
- large モデルの初回 download / compile は重くなり得るため、このセッションでは実モデル perf までは走らせず、次回 `make server-debug` の startup warm-up ログで確認する

### 検証
- `whisperkit-cli help serve`
  - `--model` / `--host` / `--port` が利用可能であることを確認
- `mise exec -- uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_phase4_thinking.py tests/unit/test_stt_backends.py`
  - 24 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 298 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` を再起動し、WhisperKit large の startup warm-up / 実 transcript / 体感遅延を確認する
- large が遅すぎる場合は `distil large-v3` または `medium` を比較候補にする

## 2026-05-25 セッション39

### やること（開始時に書く）
- 応答推論 LLM に渡す system prompt と messages をログへ出す
- 会話が噛み合わない時に、STT 結果・persona prompt・会話履歴がどう渡ったかを `logs/server-debug.log` で追えるようにする

### やったこと
- `ThinkFastMode` が `backend.chat_stream()` を呼ぶ直前に、応答推論へ渡す prompt payload を INFO ログへ出すようにした
- ログ payload には `system_prompt` / `messages` / `device_id` / `speaker` を JSON として含める
- `tests/unit/test_phase4_thinking.py` に、LLM prompt payload がログ対象になることを固定する unit test を追加した

### 詰まったこと・解決したこと
- 最初のテストは `caplog` で捕まえようとしたが、プロジェクトの logging 設定では stderr へは出ても `caplog.text` に乗らなかった
  → `server.gateway.thinking.fast.logger.info` を monkeypatch して、呼び出し引数そのものを検証する形にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase4_thinking.py`
  - 9 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 299 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` の実会話で `ThinkFastMode llm_prompt` 行を確認し、STT transcript と実際の LLM messages のズレを見る

## 2026-05-25 セッション43

### やること（開始時に書く）
- `work/audio-recordings/` に保存された実録音 WAV を使い、gate / spectral filter の CPU コストと STT への影響を測る
- 実験結果を `work/noise-filter-experiments/` に保存し、今後の STT 前処理の判断材料にする

### やったこと
- `_tools/bench_audio_filters.py` を追加し、noise WAV と input WAV から segment gate / frame gate / spectral gate の比較 WAV と summary JSON を生成できるようにした
- `tests/unit/test_audio_filter_bench.py` を追加し、短い synthetic audio で gate / spectral filter の基本挙動を固定した
- `20260525T122451Z-noise.wav` と `20260525T122454Z-read_aloud.wav` を使い、filter ごとの CPU コストと MLX Whisper large-v3-turbo-q4 の transcription を測定した

### 詰まったこと・解決したこと
- noise 録音は `rms_db=-120.0` / `peak_db=-120.0` のほぼデジタル無音で、spectral profile としては弱かった
  → この素材では spectral gate は有効な除去対象を持たず、Whisper では `反反反...` の反復幻聴を誘発した
- read-aloud 録音は `rms_db=-47.4`、active frame ratio 20.4% とかなり低信号で、raw / frame gate では `ご視聴ありがとうございました` の定型幻聴が残った
- CPU コストは十分軽く、frame gate は約 0.014ms/audio sec、spectral gate は約 2.06ms/audio sec だった
- このケースでは「加工して聞かせる」より、segment-level の品質判定で STT 投入前に reject する方が効きそうだと判断した

### 検証
- `mise exec -- uv run python _tools/bench_audio_filters.py --noise work/audio-recordings/20260525T122451Z-noise.wav --input work/audio-recordings/20260525T122454Z-read_aloud.wav --repeat 100`
  - summary: `work/noise-filter-experiments/20260525T122454Z-read_aloud/summary.json`
- `mise exec -- uv run python _tools/bench_stt_backends.py --backends local_whisper_mlx_large_turbo_q4 --runs 1 --audio-file ...`
  - raw / frame_gate_-45db: `ご視聴ありがとうございました`
  - spectral_gate: `反反反...`
- `mise exec -- uv run pytest -m unit`
  - 305 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- STT hot path に入れるなら、まず `SpeechSegment` の `rms_db` / active frame ratio / duration を使った reject 判定を追加し、今回の低信号 read-aloud artifact が STT に入らないことを unit test で固定する
- spectral filter を続けるなら、空調やMacファン音など実ノイズが入った startup noise profile を取り直してから再評価する

## 2026-05-25 セッション44

### やること（開始時に書く）
- STT 投入直前に軽量な audio signal gate を入れ、低品質 segment で Whisper を無駄に叩かないようにする
- central session と edge remote の両方で、低信号 segment が transcriber へ届かないことを unit test で固定する

### やったこと
- `server/edge/pipeline/stt_gate.py` を追加し、duration / rms_db / peak_db / active_frame_ratio で STT 前 reject を判断できるようにした
- `TomoroSession._handle_finished_speech()` に STT signal gate を入れ、reject 時は transcriber を呼ばず VAD / streaming transcriber を reset して idle に戻すようにした
- `EdgeRemoteAudioSession` にも同じ gate を入れ、edge node 側でも gateway へ transcript を送る前に低品質音声を落とすようにした
- streaming partial STT についても、明らかに弱い chunk では `process_stream_chunk()` を呼ばないようにした
- `tests/unit/test_stt_signal_gate.py` / `tests/unit/test_edge_remote_stt_gate.py` を追加し、`tests/unit/test_phase3_stt.py` に central session の reject test を追加した

### 詰まったこと・解決したこと
- 既存の一部 unit test は `vad_processor=object()` や 100ms の synthetic segment を直接 `_handle_finished_speech()` に渡していた
  → runtime fallback として sample_rate は `getattr(..., 16000)` にし、短すぎ判定は実運用に影響しにくい 80ms へ緩めた
- 今回の主目的は「実録音のような低信号・低 active ratio segment を Whisper に入れない」ことなので、`rms_db < -45` かつ `active_frame_ratio < 0.25` を sparse low signal として reject する方針にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_signal_gate.py tests/unit/test_phase3_stt.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_phase8_memory.py tests/unit/test_reply_speech_normalizer.py tests/unit/test_edge_remote_stt_gate.py`
  - 25 passed
- `mise exec -- uv run pytest -m unit`
  - 312 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザ録音で `TomoroSession latency speech_end ... stt_gate_action=reject/accept` を確認し、reject が強すぎる場合は `low_signal_rms_db` / `low_signal_max_active_ratio` を調整する

## 2026-05-25 セッション45

### やること（開始時に書く）
- STT 前処理を単一 gate ではなく filter chain として扱える構造へ広げる
- `signal_gate` / `short_segment_merge` / `spectral_subtraction` を programmatic に ON/OFF できるようにする
- startup noise profile 相当の profile をいつでも capture できる API を用意する

### やったこと
- `SttAudioFrontend` を追加し、`enabled_filters=()` なら素通り、`("signal_gate",)` なら既存 gate、`("short_segment_merge", "signal_gate")` なら短い segment の pending / merge、`("spectral_subtraction", ...)` なら noise profile がある場合のみ spectral subtraction を通す構造にした
- `NoiseProfile` / `build_noise_profile()` / `spectral_subtract()` を追加し、startup noise profile を runtime から capture して後段 filter に渡せるようにした
- `TomoroSession` と `EdgeRemoteAudioSession` は `SttSignalGate` 直接ではなく `SttAudioFrontend` を呼ぶようにした
- 既存 `TranscriptFilter` は STT 後段の hallucination filter として残し、audio frontend とは別レイヤにした
- `tests/unit/test_stt_signal_gate.py` に filter OFF、short pending merge、spectral subtraction profile capture の unit test を追加した

### 詰まったこと・解決したこと
- short segment pending は timer を持たないため、単独の短い segment は pending のまま次 segment が来るまで STT に投げない
  → 次 segment が merge window 内なら結合して STT、window 外なら古い pending を捨てて新しい segment を評価する方針にした
- spectral subtraction は profile がない場合に何もしない
  → `enabled_filters` に入れても比較対象がない時は素通りし、profile がある時だけ処理する構造にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_signal_gate.py tests/unit/test_phase3_stt.py tests/unit/test_edge_remote_stt_gate.py`
  - 15 passed
- `mise exec -- uv run pytest -m unit`
  - 316 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実録音比較では `SttAudioFrontend(enabled_filters=...)` の組み合わせを変えて、raw / signal_gate / short_merge / spectral_subtraction の STT 結果と latency を比較する

## 2026-05-25 セッション46

### やること（開始時に書く）
- audio frontend に speech bandpass filter を追加し、低域床揺れとナイキスト近辺の高域ノイズを軽く削る
- 100Hz high-pass / 2kHz low-pass 案を実録音で比較し、Whisper への影響を見る
- 常時ONにできる filter と実験用 filter を分ける

### やったこと
- `speech_bandpass()` を追加し、100Hz high-pass と 7.2kHz low-pass を `SttAudioFrontend` の filter として使えるようにした
- central / edge runtime の default frontend を `("speech_bandpass", "signal_gate")` にした
- `SttAudioFrontend` 単体の default は既存 unit test 互換のため `("signal_gate",)` のままにし、runtime から明示的に bandpass をONにする形にした
- 比較用に `bandpass_100_2000hz.wav` を作成し、2kHz low-pass の STT 影響を測った

### 詰まったこと・解決したこと
- 多くの既存 unit test は `np.ones()` の DC 信号を synthetic speech として使っており、100Hz high-pass が正しく削ると STT 前 gate が reject して落ちた
  → runtime では bandpass を常時ONにしつつ、`TomoroSession` を直接作る unit test では従来互換の frontend default を使う構造にした
- 2kHz low-pass は `捨てずに` が `すけずに` 寄りに崩れ、Whisper の子音認識を悪化させる可能性が見えた
  → 常用 low-pass は 2kHz ではなく 7.2kHz とする

### 検証
- `work/noise-filter-experiments/20260525T125851Z-read_aloud/all_filters_bandpass.wav`
  - `short_segment_merge` / `speech_bandpass` / `spectral_subtraction` / `signal_gate`
  - frontend: 約 9.2ms / 5秒音声、約 1.84ms/audio sec
  - Whisper: `うんそうだよ ともこ 短い声をすてずに 続きの`
- `work/noise-filter-experiments/20260525T125851Z-read_aloud/bandpass_100_2000hz.wav`
  - Whisper: `うんそうだよともこ短い声をすけずに続きの`
- `mise exec -- uv run pytest -m unit`
  - 318 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで `filters=speech_bandpass,signal_gate` の accept/reject ログを見て、通常発話を削りすぎていないか確認する

## 2026-05-25 セッション47

### やること（開始時に書く）
- RNNoise 系 denoise を実験用 filter として追加し、実録音に対する処理時間と Whisper 結果を確認する
- 常時ONにするかどうかを、少なくとも良い録音と低信号録音の2ケースで判断する

### やったこと
- `ffmpeg` の `arnndn` filter が利用可能であることを確認した
- `work/rnnoise-models/std.rnnn` に arnndn model を保存した
- `_tools/bench_rnnoise_filter.py` を追加し、WAV に RNNoise 系 denoise をかけて処理時間と metrics を JSON に保存できるようにした
- `SttAudioFrontend` に実験用 `rnnoise` filter を追加した
  - default OFF
  - model file がない場合は素通り
  - enabled の時だけ `ffmpeg arnndn` を呼ぶ
- `tests/unit/test_rnnoise_bench_tool.py` と `tests/unit/test_stt_signal_gate.py` の RNNoise 関連 test を追加した

### 詰まったこと・解決したこと
- Python の RNNoise binding は現環境に入っていなかった
  → 既に利用可能な `ffmpeg arnndn` を使い、追加 Python dependency なしで検証する形にした
- RNNoise は前処理としては動くが、5秒音声で約60-65ms、約12-13ms/audio sec と、現行 frontend より一桁重い
  → 常時ONではなく実験用 filter とする

### 検証
- 良い録音 `20260525T125851Z-read_aloud.wav`
  - RNNoise: 約65.1ms / 5秒音声、約13.0ms/audio sec
  - Whisper: `うんそうだよ ともこ 短い声をすてずに 続きの`
- 低信号録音 `20260525T122454Z-read_aloud.wav`
  - RNNoise: 約60.1ms / 5秒音声、約12.0ms/audio sec
  - Whisper: `おやすみなさい`
- `mise exec -- uv run pytest -m unit`
  - 321 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- RNNoise は低信号 hallucination を完全には止めないため、常時ON候補にせず、signal gate reject を優先する
- 実ノイズが強い録音素材を取ったら、`rnnoise` と `spectral_subtraction` を改めて比較する

## 2026-05-27 セッション12

### やること（開始時に書く）
- Apple Speech STT の CPU / ANE 使用を人間が `mactop` で観測できるよう、central realtime の active STT を `local_apple_speech_ja` に切り替える
- 既存 `/ws` / `TomoroSession` には触れず、config と契約テストだけを更新する
- unit test と lint を通し、観測用に `make server-debug` で起動できる状態にする

### やったこと
- `config/central_realtime.toml` の `stt_backend` を `local_apple_speech_ja` に変更した
- `tests/unit/test_phase0_config.py` の active STT 契約を Apple Speech に更新した
- MLX Whisper large turbo q4 は比較/rollback 用 backend として残した
- MEMORY.md に、Apple Speech active は CPU / ANE 観測用の一時判断であることを追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_stt_backends.py`
  - 18 passed
- `.venv/bin/python -m pytest`
  - 343 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- `.venv/bin/python _tools/bench_stt_backends.py --backends local_apple_speech_ja --runs 1 --output logs/stt-apple-active-smoke.json`
  - warm 189.6ms / measured 196.3ms
  - transcript `智子3 +3 =いくつですか`

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を動かし、人間側で `mactop` を見て Apple Speech STT 実行中の CPU / ANE / GPU 使用傾向を確認する

### 起動失敗修正
- `AppleSpeechSTT.warm_up()` が 1 秒の無音を実 transcribe しており、Apple Speech が正常に `No speech detected` を返した結果、FastAPI startup が失敗していた
- Apple Speech の warm-up は実 STT ではなく sidecar build / existence check だけに変更した
- `test_apple_speech_warm_up_only_ensures_sidecar` を追加し、warm-up で無音 audio を transcribe しないことを固定した

### 検証追記
- `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_startup_warmup.py tests/unit/test_phase0_config.py`
  - 22 passed
- `TOMOKO_CONFIG=config/central_realtime.toml .venv/bin/python - <<'PY' ... _warm_up_app()`
  - STT warm-up `local_apple_speech_ja` completed in 0.8ms
  - TTS / conversation / embedding warm-up まで完了
- `make server-debug`
  - port 8000 が既存プロセス使用中で起動不可だったため、上の `_warm_up_app()` 直接実行で startup path を確認した
- `.venv/bin/python -m pytest`
  - 344 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-27 セッション12

### やること（開始時に書く）
- go/cancel 判定用の local small LLM worker 方針を Phase として PLAN.md に追記する
- LM Studio の会話生成キューとは分離し、Tomoko 管理下の Makefile entry で起動する前提を明文化する

### やったこと
- `PLAN.md` に Phase 10.11 local turn-taking judge worker を追記した
- `make turn-taking-worker` / `make turn-taking-worker-once` を完了条件に入れ、会話 26B の LM Studio queue と制御 worker queue を分離する方針を明文化した
- rule-first judge、local small LLM worker、TomoroSession 接続、実ブラウザ評価の subphase に分けた

### 詰まったこと・解決したこと
- 計画追記のみなので実装・テスト実行はまだ行っていない

### 次のセッションでやること
- Phase 10.11 の契約に沿って `TurnTakingJudge` / local worker / Makefile entry / unit test を実装する

## 2026-05-27 セッション11

### やること（開始時に書く）
- Apple Speech STT 追加後に unit test が長くなっていないか確認する
- `tests/unit/test_stt_backends.py` と関連 factory test に、実 Swift build / 実 subprocess / 実 STT が混ざっていないか調べる
- 遅い原因が test 側にあれば mock 境界を直し、unit test を再実行する

### やったこと
- `tests/unit/test_stt_backends.py --durations=0` を確認し、Apple Speech unit は mock されており実 Swift build / 実 STT は走っていないことを確認した
- full unit の `--durations=20` を確認し、最遅 unit は 0.07s で、Apple Speech 周辺は遅延原因ではなかった
- 裸の `pytest` が perf / integration も対象にし得る設定だったため、`pyproject.toml` に `addopts = "-m unit"` を追加した
- `tests/unit/test_phase0_config.py` に pytest default が unit である契約を追加した

### 詰まったこと・解決したこと
- 長く見えた原因は unit test の中身ではなく、裸の `pytest` が `tests/perf/test_stt_latency.py` など実モデル系 perf を拾える設定だったこと
  - 解決: default は unit のみにし、perf / integration は従来どおり `pytest -m perf` / `pytest -m integration` で明示実行する

### 検証
- `.venv/bin/python -m pytest --collect-only -q`
  - 343/360 tests collected、17 deselected
- `.venv/bin/python -m pytest -m perf --collect-only -q tests/perf/test_stt_latency.py`
  - 5 perf tests collected
- `.venv/bin/python -m pytest -m integration --collect-only -q tests/integration/test_phase90_candidates_db.py`
  - 1 integration test collected
- `.venv/bin/python -m pytest --durations=20`
  - 343 passed, 17 deselected in 0.74s
- `make test-unit`
  - 343 passed, 17 deselected in 0.72s
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- perf STT を実行したい時だけ、`pytest -m perf --tb=short tests/perf/test_stt_latency.py -s` または `_tools/bench_stt_backends.py` を明示する

## 2026-05-27 セッション11

### やること（開始時に書く）
- 実会話ログで、LLM 推論中に `stale reply cancelled reason=resumed_user_speech_before_output` が出て返答が捨てられる原因を切り分ける
- VAD が `listening` に入っただけの低音量/空 STT 区間で、未出力 reply をキャンセルしないようにする

### やったこと
- 09:10:57 付近の実ログを確認し、LLM reply 開始直後に VAD が `listening` へ入っただけで未出力 reply がキャンセルされていることを確認した
- その直後の STT は `text=''` / `reason=empty` であり、実際には意味のある follow-up ではなかった
- `TomoroSession._transition("listening")` では未出力 reply をキャンセルしないようにした
- 空 transcript では既存 reply が生き残る regression test を追加し、意味のある follow-up は従来通り次の reply task 起動時に差し替える方針にした

### 詰まったこと・解決したこと
- `resumed_user_speech_before_output` の意図は、ユーザーが推論中に話し足した時に古い reply を捨てることだった
  - ただし VAD の listening は「発話候補」であって「確定発話」ではないため、Apple Speech の空認識と組み合わさると返答だけ消えて会話が終わったように見えた
  - 解決: キャンセル基準を listening 遷移から確定 transcript 側へ寄せ、空 STT ではキャンセルしない

### 検証
- `.venv/bin/python -m pytest tests/unit/test_streaming_tts_pipeline.py tests/unit/test_attention_mode.py tests/unit/test_barge_in.py -q`
  - 19 passed
- `.venv/bin/python -m pytest -q`
  - 346 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で、LLM 推論直後の低音量/空 STT 区間により Tomoko の返答が消えないことを確認する
- 実発話で推論中に追い発話した場合、古い reply がどの程度話し始めるかを観察し、必要なら「listening 中は出力を短時間 defer」する

## 2026-05-27 セッション10

### やること（開始時に書く）
- Apple 純正 Speech framework を Swift sidecar CLI として Python から呼べるようにする
- 同じ録音 WAV で Apple Speech と `local_whisper_mlx_large_turbo_q4` の transcript / latency を比較できるベンチを追加する
- 既存 `/ws` や `TomoroSession` には触れず、STT backend 境界と `_tools` の比較経路に閉じる

### やったこと
- `_tools/apple_speech_stt/AppleSpeechSTT.swift` と `Info.plist` を追加し、Apple Speech framework を使う Swift sidecar CLI を作った
- `server/edge/pipeline/stt_apple.py` と `apple_speech` backend factory を追加した
- `config/central_realtime.toml` に `local_apple_speech_ja` を比較 lane として追加した
- 既存 `_tools/bench_stt_backends.py` で Apple Speech と `local_whisper_mlx_large_turbo_q4` を同じ WAV で比較できるようにした

### 詰まったこと・解決したこと
- CLI から `SFSpeechRecognizer.requestAuthorization` を呼ぶと、TCC が usage description を認識せず `SIGABRT` した
  - 解決: sidecar を `.app` bundle + embedded Info.plist + ad-hoc codesign でビルドし、authorization request は明示実行しない
- semaphore で main thread を塞ぐと `recognitionTask` の callback が進まず timeout した
  - 解決: RunLoop を短い間隔で回しながら final callback を待つ

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py tests/unit/test_stt_bench_tool.py`
  - 23 passed
- synthetic `say` 音声 3 runs:
  - Apple Speech avg 183.3ms / text `智子3 +3 =いくつですか`
  - MLX Whisper large turbo q4 avg 240.6ms / text `ともこ3たす3はいくつですか`
- 実録音 `work/audio-recordings/20260525T125851Z-read_aloud.wav` 3 runs:
  - Apple Speech avg 242.7ms / text `うんそうだよ智子短いです声を捨てずに続き`
  - MLX Whisper large turbo q4 avg 248.7ms / text `うんそうだよ ともこ 短い声を捨てずに 続きの`
- JSON:
  - `logs/stt-apple-speech-vs-mlx-large-turbo-q4.json`
  - `logs/stt-apple-speech-vs-mlx-large-turbo-q4-read-aloud.json`

### 次のセッションでやること
- Apple Speech は active STT へ切り替えず比較 lane として残し、実会話録音が増えたら読み・表記ゆれをさらに比較する

## 2026-05-25 セッション48

### やること（開始時に書く）
- RNNoise は効果に対するコストが厳しいため、実ランタイムではOFFのままにする
- central realtime の会話モデルを `local_gemma4_e2b_mlx` に変更する
- TTS backend を Kokoro に変更する

### やったこと
- `config/central_realtime.toml` の `conversation_backend` を `local_gemma4_e2b_mlx` に変更した
- `config/central_realtime.toml` の `conversation_fallback` を `local_lfm25_12b_jp_mlx` に変更し、前メインモデルを fallback として残した
- `config/central_realtime.toml` と `config/edge_kitchen.toml` の `tts_backend` を `kokoro_mlx` に変更した
- `kokoro_mlx` backend に `sample_rate = 24000` を明示し、`KokoroMLXBackend.from_spec()` が config の `sample_rate` を反映するようにした
- 音声 stack soak tool の default TTS / conversation backend を `kokoro_mlx` / `local_gemma4_e2b_mlx` に更新した

### 詰まったこと・解決したこと
- 素の `pytest` は Python 3.14 環境で起動し、`psycopg` / `ollama` が見えず collection error になった
  → repo の `.venv/bin/python -m pytest` で検証した
- Kokoro MLX backend は実装上 24kHz default だったが、config には `sample_rate` がなく `from_spec()` でも読んでいなかった
  → config と factory の両方を揃え、設定で保証できる形にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_voice_stack_soak_tool.py tests/unit/test_stt_signal_gate.py tests/unit/test_kokoro_mlx_tts.py`
  - 34 passed

### 次のセッションでやること
- full unit / lint を回し、config 切り替えが既存の router / TTS 周辺に波及していないか確認する

### 検証追記
- `.venv/bin/python -m pytest -m unit`
  - 321 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-27 セッション5

### やること（開始時に書く）
- 最新 `logs/server-debug.log` / `logs/backend-trace.jsonl` を見て、26B A4B 採用後の実会話品質を確認する
- E2B / E4B より会話として楽しい領域に入ったか、内容とレイテンシーの両面で判断する
- メイン会話モデル探索を続けるか、自発的発話調整へ移るかを決める

### やったこと
- 03:55 台の実ブラウザ会話ログを確認した
- 会話 backend が `lmstudio_gemma4_26b_a4b` / `gemma-4-26b-a4b-it-mlx` で fallback していないことを確認した
- 「人生の生き方を考える上で大事にすること」への返答で、一般論ではなく相手の曖昧さを受け止め、次の内省質問へつなげられていることを確認した
- ユーザー体感として、E2B 時より明らかに楽しく、Romi よりも楽しいと判断された

### 詰まったこと・解決したこと
- first audio は 1.1〜2.2 秒程度で、即応性は犠牲になる
  → ただし意味のある会話の楽しさがレイテンシーを上回るため、メイン会話は 26B A4B で一旦FIXする
- 次に掘るべき対象はモデルサイズ探索ではなく、Tomoko からの自発的発話・話しかける間合い・候補選択へ移る

### 次のセッションでやること
- 自発的発話の runtime log と candidate / policy trace を確認し、Tomoko から話しかける段階の調整に入る

## 2026-05-27 セッション6

### やること（開始時に書く）
- 自発発話の初回実ログを踏まえ、PLAN.md に「話しかけ方の自然さ」を調整する Phase を追記する
- 候補文の橋渡し、会話開始後の文脈回復、候補品質評価、ログ確認手順を実装者が迷わない粒度に分解する

### やったこと
- PLAN.md に `2026-05-27 追記: Phase 10.10 自発発話の会話開始品質調整` を追加した
- 04:09 台の実ログで確認した、自発候補発話から follow-up conversation session へ入った流れを Phase の前提として記録した
- 課題を candidate 文の唐突さ、聞き返し時の話題保持、主語欠け文、境界 score の LLM judge 発話に分解した
- Phase 10.10.0〜10.10.4 として、ログ評価、generated_text 契約、会話文脈への載せ方、間合い調整、実ブラウザ smoke を追加した

### 検証
- `git diff --check -- PLAN.md LOG.md`
  - pass

### 次のセッションでやること
- Phase 10.10.0 から着手し、自発発話ログを同じ手順で評価できる inspection 手順を文書化する

## 2026-05-27 セッション7

### やること（開始時に書く）
- Phase 10.10 全体をまとめて進める
- 自発発話ログ評価手順、candidate generated_text 契約、follow-up 時の直前自発話題文脈、候補 policy tuning を実装する
- 迷った箇所は仮判断で前へ進め、実ブラウザ smoke で残る判断は起床後に確認できる形で LOG / MEMORY に残す

### やったこと
- `_docs/evaluation.md` に Phase 10.10 用の自発発話 inspection 手順を追記した
  - `logs/server-debug.log` から candidate fetched / policy / start reply / followup session / prompt / reply を見る
  - `utterance_candidates` / `conversation_sessions` の確認 SQL を固定した
  - `starts_conversation` / `not_abrupt` / `self_contained` / `recoverable` / `low_intrusion` の評価語彙を固定した
- `LLMUtteranceEvaluator` の prompt を、単なる興味文ではなく会話開始用 `generated_text` を返す契約に強めた
- candidate 生成後の正規化で、主語欠け断片、長すぎる文、最新情報の断定を落とすようにした
- world observation 由来 candidate に `topic_shift_bridge_required` tag を付け、必要なら `さっきの話とは別で、` を補うようにした
- `TomoroSession.start_precomputed_reply()` が直前の initiative / arrival 発話本文、source、candidate id を保持するようにした
- 人間の follow-up で通常会話に入る最初の LLM prompt に、直前の Tomoko 自発発話を assistant turn として入れるようにした
- 自発発話だけでは `conversation_session` を開始しない既存判断を regression test で固定した
- `CandidateSpeakPolicy` で `recent_heavy_conversation` 直後の bridge なし topic shift に小さな penalty を付けた
- 現行 `config/central_realtime.toml` の active STT が `local_whisper_mlx_large_turbo_q4` へ戻っていたため、config 契約テストを実設定に合わせた

### 詰まったこと・解決したこと
- `make thinker-once` は成功したが、既存 candidate の dedupe により新規 text-ready candidate は増えなかった
  - 結果: `candidate_generated=6 candidate_inserted=0 candidate_kept=0 pregenerated=0 arrival_behavior=wait_silent`
  - DB には active candidate が 11 件あり、既存の spoken world observation candidate も確認できた
- `make server-debug` は起動し、startup warm-up と root HTML 取得まで確認した
  - STT warm-up: `local_whisper_mlx_large_turbo_q4` 1218.0ms
  - TTS warm-up: `voicevox_tsumugi` 138.7ms
  - conversation warm-up: `lmstudio_gemma4_26b_a4b` 366.8ms
  - embedding warm-up: `local_bge_m3` 7811.0ms
  - `GET /` は 200 で HTML を返した
- 無人では実マイクから ambient idle -> initiative 発話 -> 人間 follow-up -> 2 turn 継続の自然さを評価できないため、Phase 10.10.4 の実ブラウザ会話評価は積み残した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase18_world_observation_source.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase4_thinking.py`
  - 58 passed
- `.venv/bin/python -m pytest -m unit`
  - 339 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- `make thinker-once`
  - pass, but inserted/kept/pregenerated are all 0 due to existing active candidates / dedupe
- `make server-debug`
  - startup complete, `GET /` 200, then stopped manually after smoke

### 次のセッションでやること
- `make server-debug` の実ブラウザで、ambient idle から 1 回以上 initiative を発話させる
- その場で人間が返答し、直後 3 turn の `ThinkFastMode llm_prompt` と reply を評価する
- 成功例と要改善例を最低 1 件ずつ LOG に残し、必要なら active 既存 candidate を dismiss / expire して新しい bridge 付き candidate を試す

## 2026-05-27 セッション10

### やること（開始時に書く）
- Apple Speech STT が `No speech detected` で例外を投げ、WebSocket / server task まで落ちる経路を修正する
- Apple Speech backend が現状は比較用の薄い実験レーンであり、本番品質の失敗分類までは未完成であることを確認する

### やったこと
- `AppleSpeechSTT` で sidecar の `No speech detected` を空 transcript として扱うようにした
- Apple Speech の未知の sidecar error は従来通り `RuntimeError` として残し、権限や実行失敗を隠さないようにした
- Apple Speech backend はまだ比較用の薄い実験レーンであり、失敗分類の詰めが必要だと MEMORY に追記した

### 詰まったこと・解決したこと
- VAD が拾った短い/弱い区間で Apple Speech が発話なし判定を返すと、STT の通常の空認識ではなく例外として websocket task を落としていた
  - 解決: `No speech detected` だけを非 fatal に分類し、以後の既存 transcript empty/drop 経路へ流す

### 検証
- `.venv/bin/python -m pytest tests/unit/test_stt_backends.py -q`
  - 17 passed
- `.venv/bin/python -m pytest -q`
  - 346 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で Apple Speech の空 transcript が WebSocket を落とさず drop されることを確認する
- engaged 滞在時間が短い問題は STT とは別に、attention timeout / VAD accepted segment / transcript empty の時系列で切り分ける

## 2026-05-27 セッション8

### やること（開始時に書く）
- ユーザー回答を反映する
  - active STT は `local_whisper_mlx_large_turbo_q4` が正
  - bridge 文は候補生成 prompt 側へ寄せ、自動補完は弱める
  - 断片 candidate reject は厳しめのまま
  - recent heavy conversation penalty は軽く入れたまま
  - initiative / arrival 両方を follow-up context に入れる実装は維持
- 既存候補で試すため、候補が来たが発話しなかった理由をブラウザ上で見える UI にする

### やったこと
- `LLMUtteranceEvaluator` の world observation bridge 自動補完を外し、bridge は prompt 側が生成する契約に戻した
- `CandidateCommandRunner` が `TomoroSession` の transition emission を WebSocket へ流すようにした
- ブラウザ UI に `Candidate` 表示欄を追加し、fetch / skip / reply / arrival / command failure の最新イベントを表示するようにした
- policy wait 時に `initiative_skipped` と policy score / threshold / reason がブラウザへ届く regression test を追加した
- 接続開始時の arrival emission が混ざっても、Phase1 の state / debug 契約を検証できるようにテストを更新した

### 詰まったこと・解決したこと
- 全体 unit で Phase1 の `sent_json` 完全一致テストが、接続開始時の `arrival_fetch_requested` emission 追加により失敗した
  - 解決: Phase1 テストは目的の state / debug event だけを抽出して検証し、candidate visibility event との共存を許容した
- Python ruff に `client/main.js` を直接渡すと JavaScript を Python として解析して失敗する
  - 解決: JS は `node --check client/main.js`、Python は `ruff check .` で検証した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase18_world_observation_source.py tests/unit/test_phase106_initiative_policy.py`
  - 28 passed
- `node --check client/main.js`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 340 passed, 17 deselected
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` のブラウザで、Candidate 欄に `initiative_fetch_requested` / `initiative_skipped` / `initiative_reply_requested` が見えることを実マイク込みで確認する
- 既存 active candidate を使い、発話した例と policy / gate で止まった例を LOG に残す

## 2026-05-27 セッション9

### やること（開始時に書く）
- cooldown に入るのが早すぎる疑いについて、最新 `logs/server-debug.log` を確認する
- `TomoroSession` の state / attention_mode 遷移、VAD/STT/transcript/playback の時系列を見て原因候補を切り分ける

### やったこと
- `logs/server-debug.log` の 04:51 台を確認し、Tomoko の音声再生中に `engaged -> cooldown` が発火していることを確認した
  - 04:51:23.330 chunk 18 playback_started
  - 04:51:32.129 `engaged -> cooldown`
  - 04:51:34.417 chunk 18 playback_ended
  - 04:51:39.638 chunk 20 playback_ended
  - 04:51:40.128 `cooldown -> ambient`
- `TomoroSession.process_audio_chunk()` が VAD state `idle` の無音 chunk だけで attention idle を進めており、playback state を見ていないことを確認した
- playback state が `idle` の時だけ `_advance_attention_idle()` を進めるように修正した
- 再生中に attention idle が進まない regression test を追加した

### 詰まったこと・解決したこと
- cooldown が早い原因は timeout 値そのものより、Tomoko の長い再生時間も idle として数えていたことだった
  - 解決: `AudioTurnController.playback_state` が `client_playing` / `speaking` / `echo_grace` の間は attention timeout を止める

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_attention_mode.py tests/unit/test_barge_in.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_candidate_command_runner.py`
  - 35 passed
- `.venv/bin/python -m ruff check server/session.py tests/unit/test_attention_mode.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 341 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` の実ブラウザで、長い Tomoko 返答の再生中に `engaged -> cooldown` が出ないことを確認する
- 最終 chunk の playback_ended から、意図した 8 秒程度の無音後に cooldown / ambient へ進むかを確認する
