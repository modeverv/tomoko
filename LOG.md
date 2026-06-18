# LOG.md

## 2026-06-18 セッション21

### やること（開始時に書く）
- partial STT / E2B / LLM / TTS を WebSocket audio receive loop から非同期 lane に逃がす。
- audio receive loop が partial 処理待ちで詰まらず、VAD final 検出と音声受信を継続できるようにする。
- 実 `/ws` smoke で first audio latency と final transcript 順序を再測定し、`_docs/latency.md` に残す。

### やったこと
- `/ws` direct audio conversation path に `AudioPartialLane` / `AudioFinalLane` を追加した。
- receive loop は VAD と queue 投入だけを行い、partial STT / E2B / LLM / TTS は background task で処理するようにした。
- partial lane は queue に溜まった audio chunks を coalesce して、Apple Speech pseudo partial の再実行回数を減らした。
- final lane は partial lane が idle になるまで短く待ってから final STT を始めるようにした。
- `SpeechOrderExecutor(protect_inflight_replace=True)` を追加し、partial TTS 合成中に final replace が来ても partial audio を discard しないようにした。
- partial observation から作る prompt は `短く一文で返す` instruction を付けるようにした。
- smoke script に `--post-first-audio-ms` を追加し、first audio 後も観測を続けられるようにした。

### 詰まったこと・解決したこと
- 最初の async 化だけでは final replace が partial TTS の generation を潰し、partial audio が `discarded=1` になった。
  - 解決: in-flight replace protection を入れた。
- 次に final result の model_delta 送信が partial audio より先に queue を占有した。
  - 解決: 音声 chunk が無い queued/deferred speech-order の prompt result は WebSocket に流さないようにした。
- それでも high partial が final STT に負けるケースがあった。
  - 解決: final STT 自体を background lane に逃がし、partial lane の idle を短く待ってから final Apple Speech を始めるようにした。
- partial 返答が長いと VOICEVOX full WAV 待ちが重かった。
  - 解決: partial prompt を concise にした。

### 検証
- `uv run pytest -m unit -q`
  - 77 passed, 1 deselected
- `uv run ruff check server scripts tests`
  - passed
- `git diff --check`
  - passed
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:62235/ws --voice Kyoko --text 'トモコ今日の予定を教えてそれだけで大丈夫です' --continue-after-first-audio --timeout-sec 90`
  - artifact `logs/say-latency-20260618-160201.json`
  - voice-end to first audio 860.5ms
  - partial prompt は concise、TTS text `今は特に決まってないけど、のんびり過ごそうかな。`
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:62235/ws --voice Kyoko --text 'トモコ今日の予定を教えてそれだけで大丈夫です' --continue-after-first-audio --post-first-audio-ms 5000 --timeout-sec 90`
  - artifact `logs/say-latency-20260618-160314.json`
  - voice-end to first audio 1515.6ms
  - final transcript 5123.1ms after voice end

### 次のセッションでやること
- 同一 utterance の partial speech-order と final speech-order を reconcile し、重複 append / duplicate reply を止める。
- Apple Speech pseudo partial のタイミングばらつきをさらに下げる。必要なら low-saturation partial の E2B 判定頻度を減らす。

## 2026-06-18 セッション20

### やること（開始時に書く）
- v1 の Apple Speech streaming partial 実装（`streaming`, `stream_interval_ms`, `stream_min_audio_ms`, `_last_stream_text` 抑制）を v2 に移植する。
- `process_audio_samples()` が VAD segment 完了前に partial observation を返し、E2B semantic 判定から speech-order 開始判断まで進めるようにする。
- 実 `/ws` audio path で final transcript 前に partial 由来の scheduler decision / speech-order が出るか smoke する。

### やったこと
- `AppleSpeechStreamingBackend` に v1 と同じ pseudo streaming partial を移植した。
  - `streaming` / `stream_interval_ms` / `stream_min_audio_ms`
  - accumulated stream buffer
  - `_last_stream_text` による同一 partial 抑制
  - `reset_stream()` による VAD final segment 境界での partial state reset
- `HotPathAudioConversation.process_audio_samples()` で、VAD final segment が出る前にも
  speech probability が閾値以上なら `process_stream_chunk()` を呼び、partial observation を
  conversation core / scheduler / speech executor に流すようにした。
- partial の semantic saturation が低い場合に speech-order を作らない gate を scheduler に追加した。
- `scripts/v2_say_latency_smoke.py` に `--continue-after-first-audio` を追加し、
  first audio 後も silence を送り続けて final transcript との順序を artifact で確認できるようにした。

### 詰まったこと・解決したこと
- v1 の Apple Speech partial は Swift sidecar の true partial ではなく、Python 側の accumulated audio を
  定期的に Apple Speech final transcription へ通す pseudo streaming だった。
  - 解決: v2 もこの方式として移植した。
- 短い partial `今日の予定は` / `今日の予定で` は E2B saturation 0.3 相当になり、
  scheduler は `partial semantic saturation is below start threshold` で suppress した。
- 実 `/ws` smoke では発話 `トモコ今日の予定を教えてそれだけで大丈夫です` で
  partial `その今日の予定を教えて` が E2B saturation 0.8 相当になり、final transcript 前に
  scheduler `replace_current` / speech-order が出た。
- ただし first audio は voice-end から 4492.5ms 後だった。partial STT / E2B / LLM / TTS を
  audio receive loop 内で await しているため、音声受信と final VAD 検出が詰まっている。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py::test_apple_speech_backend_streams_partial_and_suppresses_duplicates tests/unit/test_v2_audio_tomoko_prompt.py::test_hot_path_can_emit_partial_speech_order_before_vad_final tests/unit/test_v2_semantic_scheduler.py::test_speech_scheduler_suppresses_low_saturation_partial_start -q`
  - 3 passed
- `uv run ruff check server/audio/stt.py server/hot_path/audio_conversation.py server/tomoko/scheduler.py server/shared/models.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_semantic_scheduler.py`
  - passed
- `mlx_lm.server --model mlx-community/gemma-4-e2b-it-OptiQ-4bit --port 8083`
  - E2B saturation endpoint として起動した。
- `TOMOKO_V2_SEMANTIC_LLM=1 TOMOKO_V2_SEMANTIC_LLM_URL=http://127.0.0.1:8083 TOMOKO_V2_SEMANTIC_LLM_MODEL=mlx-community/gemma-4-e2b-it-OptiQ-4bit uv run uvicorn server.hot_path.app:app --host 127.0.0.1 --port 62234`
  - 実 `/ws` hot-path smoke 用に起動した。
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:62234/ws --voice Kyoko --text 'トモコ今日の予定を教えてそれだけで大丈夫です' --continue-after-first-audio --timeout-sec 90`
  - artifact `logs/say-latency-20260618-152817.json`
  - partial `その今日の予定を教えて` at elapsed 9168.0ms
  - scheduler `replace_current` / semantic saturation 0.8 相当
  - final transcript at elapsed 14276.5ms
  - partial speech-order は final より 5108.4ms 早い
  - voice-end to first audio は 4492.5ms

### 次のセッションでやること
- partial STT / E2B / LLM / TTS を WebSocket audio receive loop から非同期に逃がし、
  発話受信と VAD final 検出を止めない。
- partial 由来 speech-order 後に final STT が来た時、同一 utterance の重複発話を抑制する。

## 2026-06-18 セッション19

### やること（開始時に書く）
- 意味飽和判定 LLM として Gemma 4 E2B MLX を導入し、実測できる smoke を追加する。
- 現行 Apple Speech sidecar は final のみ返すため、同じ say 音声を時間窓で切った疑似 partial を作り、full STT final より前に `LLM開始判定OK` が出るか観測する。
- E2B saturation latency、partial offset、full final STT latency、early OK lead time を JSON artifact と `_docs/latency.md` に残す。

### やったこと
- semantic saturation 専用の `OpenAICompatibleSaturationBackend` を追加し、`TOMOKO_V2_SEMANTIC_LLM_URL` / `TOMOKO_V2_SEMANTIC_LLM_MODEL` で E2B 系 OpenAI 互換 server を指定できるようにした。
- E2B では従来 prompt が明らかな依頼文にも `SATURATION=0.1` を返したため、`saturation_prompt()` を compact few-shot 形式に変更した。
- `scripts/v2_semantic_early_smoke.py` / `make v2-semantic-early-smoke` を追加し、`say` 音声の prefix window を疑似 partial として Apple Speech に通し、Gemma E2B saturation 判定が full final STT より前に OK を出せるか推定できるようにした。
- `mlx_lm.server --model mlx-community/gemma-4-e2b-it-OptiQ-4bit --port 8083` で E2B を一時起動して smoke を実行した。

### 詰まったこと・解決したこと
- dflash は Gemma E2B 用 draft が無く、`mlx-community/gemma-4-e2b-it-OptiQ-4bit` を直接 serve できなかった。
  - 解決: 今回の観測では `mlx_lm.server` を使った。
- 既存 8081/8082 の dflash server は request の `model` 指定を受けても起動中の 31B/26B で返したため、E2B 観測には別 port が必要だった。
- 現行 Apple Speech sidecar は streaming partial を返さないため、今回の smoke は「prefix window replay による推定」であり、実運用には streaming partial source の追加が必要。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py::test_openai_saturation_backend_builds_small_non_stream_payload tests/unit/test_v2_semantic_scheduler.py::test_saturation_prompt_uses_compact_examples_for_e2b tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 3 passed
- `uv run ruff check server/tomoko/semantic.py scripts/v2_semantic_early_smoke.py tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- E2B probe
  - old prompt: `SATURATION=0.1` for `トモコ、今日の予定を教えて`
  - compact few-shot prompt: `SATURATION=0.95`, 290〜440ms
- `TOMOKO_V2_SEMANTIC_LLM_URL=http://127.0.0.1:8083 TOMOKO_V2_SEMANTIC_LLM_MODEL=mlx-community/gemma-4-e2b-it-OptiQ-4bit uv run python -m scripts.v2_semantic_early_smoke --voice Kyoko --text 'トモコ、今日の予定を一言で教えて。' --offset-ms 800 --offset-ms 1200 --offset-ms 1600 --offset-ms 2000 --offset-ms 2400 --threshold 0.75`
  - artifact `logs/semantic-early-smoke-20260618-151302.json`
  - 2400ms までの partial は saturation 0.3 で early OK なし
- 同条件で `--offset-ms 2400 --offset-ms 2800 --offset-ms 3000 --offset-ms 3200`
  - artifact `logs/semantic-early-smoke-20260618-151319.json`
  - 3000ms partial `智子今日の予定を一言で教え` で saturation 0.8、E2B 判定 281.3ms
  - full final STT available 3634.0ms from speech start に対し、estimated decision 3281.3ms、lead 352.7ms

### 次のセッションでやること
- Apple Speech sidecar か別 STT backend で実 streaming partial を出し、prefix-window 推定ではなく実 event 時刻で early OK を測る。
- 早期 OK 後に final STT が diverge した場合の cancel / replace を Phase S11 の残タスクとして実装する。

## 2026-06-18 セッション18

### やること（開始時に書く）
- dflash prefix cache が効きやすいかを確認するため、main reply prompt を `SYSTEM` / `SESSION_TRANSCRIPT` / `INSTRUCTION` 形式に変更する。
- `SESSION_TRANSCRIPT` には同一 session の過去発話と現在 user 発話を speaker 付きで並べる。
- 5ターン smoke で prompt artifact と dflash prefix-cache-stats を確認する。

### やったこと
- main reply prompt を `SYSTEM` / `INSTRUCTION` / `SESSION_TRANSCRIPT` 形式に変更した。
- `SESSION_TRANSCRIPT` は `user:` / `tomoko:` の speaker 付きで同一 session の履歴を append-only に積み、最後に現在 user 発話を置くようにした。
- `PromptRequest.prompt_text` は smoke artifact にそのまま残しつつ、OpenAI compatible chat completion へ送る直前に `SESSION_TRANSCRIPT` を `user` / `assistant` role の message list に分解するようにした。
- hot-path 側 executor と tomoko-process 側 chat backend の両方で同じ prompt role 分解を行うようにした。

### 詰まったこと・解決したこと
- 最初に `SYSTEM` / `SESSION_TRANSCRIPT` / `INSTRUCTION` の文字列順で試したが、dflash 側では 2ターン目以降も prefix cache が hit しなかった。
- 次に `INSTRUCTION` を transcript の前へ移動して `prompt_text` 自体は前 turn の完全 prefix になるようにしたが、単一 user message として送る限り dflash はまだ hit しなかった。
- 原因は、chat template 後の token 列では previous request の assistant 生成位置と next request の user message 継続位置が揃わないためと判断した。
- `SESSION_TRANSCRIPT` を実際の chat roles に分解したところ、2ターン目以降で dflash の `prefix cache hit` が出るようになった。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py::test_prompt_builder_next_turn_keeps_previous_prompt_as_prefix tests/unit/test_v2_audio_tomoko_prompt.py::test_session_transcript_prompt_is_sent_as_chat_roles -q`
  - 2 passed
- `uv run pytest -m unit -q`
  - 69 passed, 1 deselected
- `uv run ruff check server/tomoko/prompt.py server/llm/chat.py server/hot_path/model_executor.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_speech_order_flow.py`
  - passed
- temp server `ws://127.0.0.1:62231/ws` で exact order smoke
  - artifact `logs/five-turn-smoke-20260618-145017.json`
  - dflash `hits=16+0` のまま、misses が増え、`prefill restored 0.0 tok/s`
- temp server `ws://127.0.0.1:62232/ws` で append-only prompt text smoke
  - artifact `logs/five-turn-smoke-20260618-145232.json`
  - prompt text は turn N+1 が turn N を prefix に持つが、dflash は `hits=16+0` のまま
- temp server `ws://127.0.0.1:62233/ws` で chat role 分解 smoke
  - artifact `logs/five-turn-smoke-20260618-145708.json`
  - avg first audio 2354.5ms / p95 3073.2ms / max 3073.2ms
  - dflash は 2ターン目から `prefix cache hit 40/63`, `59/86`, `82/112`, `108/132` tokens
  - `prefill_tokens_saved` は 1822 -> 2111 まで増え、`prefill restored` も 72.9 / 69.8 / 128.9 / 136.8 tok/s と出た

### 次のセッションでやること
- DB split path でも同じ chat role 分解が効くか、tomoko-db worker 経由の 5ターン smoke を必要に応じて回す。
- dflash の hit が出る条件は「prompt_text 文字列 prefix」ではなく「chat template 後 token prefix」なので、今後 prompt 形式を変える時は role 境界込みで確認する。

## 2026-06-18 セッション17

### やること（開始時に書く）
- DB split 版で、tomoko-process が無音 gap を元に conversation session を DB に明示発番する。
- `v2_utterances` に同一 session の user / tomoko 発話を積み、prompt は同一 session の過去発話から作る。
- 5ターン smoke の prompt で、現在発話の重複と LAST n による片側欠落をなくす。

### やったこと
- `tomoko-db` worker が final STT ごとに open session を DB から読み、open session が無い場合は新規発番、idle gap 超過時は旧 session を `idle_gap` close して新規発番するようにした。
- DB split path で user durable utterance と Tomoko speech order text を同じ `v2_utterances.session_id` に保存するようにした。
- `TomokoConversationCore` は DB worker から渡された `session_id_override` と `prior_session_history` を使って prompt を作れるようにした。
- prompt は現在発話を履歴へ append する前に作るよう変更し、`STABLE_CONTEXT` は過去発話、`CURRENT_USER_UTTERANCE` は現在発話だけに分離した。
- hot-path / DB split の VAD audio clock 初期値を wall-clock epoch ms にし、1970 年 timestamp で DB session gap が壊れないようにした。

### 詰まったこと・解決したこと
- SQL の `NULL < 現在` は true ではなく unknown になるため、session 発番条件は `open session が存在しない` を明示条件にした。
- fake DB split smoke の VAD timestamp が 1970 年になっており、既存 open session との gap が負になる問題を見つけた。audio clock を現在時刻初期化にして解決した。
- 5ターン smoke の 1ターン目で current user が stable context に重複した原因は、`recent_history` ではなく `recent_utterances` fallback に current を先に入れていたことだった。prompt 作成後 append に統一した。

### 検証
- `uv run pytest -m unit -q`
  - 67 passed, 1 deselected
- `uv run ruff check server/hot_path/audio_conversation.py server/hot_path/db_conversation.py server/tomoko/db_worker.py server/tomoko/db_bridge.py server/tomoko/conversation.py server/tomoko/main.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_semantic_scheduler.py`
  - passed
- `git diff --check`
  - passed
- `make v2-db-split-smoke`
  - artifact `logs/db-split-smoke-20260618-144044.json`
  - total 58.7ms / transcript->order 0.1ms / order->first audio 0.2ms
  - latest DB session `d2d16008-5b82-4475-a15a-09ae8d8ec34f` に user / tomoko の 2 utterance が残ることを SQL で確認した。
- temp server `ws://127.0.0.1:62191/ws` で `uv run python -m scripts.v2_five_turn_smoke --url ws://127.0.0.1:62191/ws --voice Kyoko`
  - artifact `logs/five-turn-smoke-20260618-143934.json`
  - 1ターン目の `STABLE_CONTEXT` は空、5ターン目は同一 session の過去4ターンが入り、現在発話は `CURRENT_USER_UTTERANCE` のみに出る。

### 次のセッションでやること
- 既存 8000 番の reload process は古い app state を握ることがあるため、確認時は process restart または別 port temp server を使う。
- DB に過去 smoke 由来の 1970 年 open session が残っているため、必要なら dev DB cleanup 用の運用メモか maintenance SQL を追加する。

## 2026-06-18 セッション16

### やること（開始時に書く）
- v1 にあった multi-turn 実 runtime smoke に相当する v2 版を追加する。
- 実 Apple Speech / dflash / VOICEVOX を使い、同一 `/ws` セッションで約5ターンの say 音声を流して first audio latency と応答を artifact に残す。

### やったこと
- `scripts/v2_five_turn_smoke.py` を追加し、macOS `say` で生成した5発話を同一 `/ws` セッションへ順番に流す実 runtime smoke を作った。
- `make v2-five-turn-smoke` を追加し、既存の `WS_LATENCY_URL` / `WS_LATENCY_VOICE` で実行できるようにした。
- artifact には turn ごとの input WAV、final transcript、model/TTS text、event counts、voice-end to transcript / TTS result / first audio latency、全体平均 / p95 / max を保存する。

### 検証
- `uv run ruff check scripts/v2_five_turn_smoke.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `python -m py_compile scripts/v2_five_turn_smoke.py`
  - passed
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - passed
- `make v2-five-turn-smoke`
  - passed on existing `ws://127.0.0.1:8000/ws`
  - artifact `logs/five-turn-smoke-20260618-140934.json`
  - avg first audio 3491.2ms / p95 4387.7ms / max 4387.7ms
  - turn first audio: 2505.4 / 2869.6 / 3511.1 / 4182.1 / 4387.7ms
- 追試: `llm_prompt` event と artifact field を追加して `make v2-five-turn-smoke` を再実行
  - artifact `logs/five-turn-smoke-20260618-141915.json`
  - 各 turn に会話 LLM へ渡した `llm_prompt` を保存
  - prompt chars: 136 / 199 / 260 / 341 / 379
  - avg first audio 2717.7ms / p95 3642.5ms

### 次のセッションでやること
- 5ターン smoke を DB split URL でも回し、通常 hot-path と DB split の multi-turn 劣化傾向を比較する。
- turn が進むほど first audio が伸びる理由を、prompt/history増加、dflash cache hit、VOICEVOX長文化に分けて見る。

## 2026-06-18 セッション15

### やること（開始時に書く）
- DB split path の発話ごとの `psycopg.AsyncConnection.connect()` をなくし、hot-path / tomoko-db worker が process lifetime の DB connection を持つようにする。
- 同じ分離版 real say latency smoke を再実行し、ユーザー発話終わりから VOICEVOX first audio chunk までを測り直す。

### やったこと
- hot-path DB split conversation は `/ws` ready 前に `v2_speech_order` LISTEN connection と write/read connection を warm し、STT insert / order load / recovery polling / audio event 保存で再接続しないようにした。
- tomoko-db worker は `v2_stt_observation` LISTEN connection と work connection を process lifetime で開き、通知ごとの `AsyncConnection.connect()` をやめた。
- unit test で DB split runtime の connection open が初期化関数だけに閉じていることを固定した。

### 詰まったこと・解決したこと
- 最初の修正では hot-path connection が lazy open で、初回発話 latency に初期接続が混ざった。
  - 解決: `/ws` accepted 後、`ready` event を返す前に `warm_connections()` を呼んで、発話開始前に LISTEN/write connection を作るようにした。

### 検証
- `make check`
  - ruff passed
  - unit 64 passed / 1 deselected
- `TEST_DATABASE_URL=postgresql://tomoko:tomoko@localhost:5432/tomoko uv run pytest -m integration -q`
  - 1 passed / 64 deselected
- `make v2-db-split-smoke`
  - process-lifetime connection + ready-before-warm path passed
  - fake total 49.4ms / server internal DB split total 15.8ms
  - artifact `logs/db-split-smoke-20260618-140130.json`
- split real say latency smoke on `ws://127.0.0.1:60620/ws`
  - voice-end to first audio 2153.8ms
  - server STT-start to audio-ready 1733.9ms
  - notify->order 826.3ms
  - order->VOICEVOX ready 607.4ms
  - artifact `logs/say-latency-20260618-140145.json`

### 次のセッションでやること
- 同条件で 3-5 回連続測定し、dflash / VOICEVOX の揺れを平均と p95 で見る。
- `order->VOICEVOX ready` 約600msを短縮するため、VOICEVOX streaming chunk 設定と hot-path送出タイミングを再点検する。

## 2026-06-18 セッション14

### やること（開始時に書く）
- hot-path と tomoko-process を DB `LISTEN/NOTIFY` で完全分離する。
- hot-path は STT observation を DB に insert して `v2_stt_observation` を id-only NOTIFY し、`v2_speech_order` を LISTEN して TTS/audio 実行する。
- tomoko-process は `v2_stt_observation` を LISTEN し、scheduler/LLM を実行して scheduler decision / speech-order を DB に保存し、`v2_speech_order` を id-only NOTIFY する。
- fake と real runtime の split smoke で latency を artifact / `_docs/latency.md` に記録する。

### やったこと
- `server/tomoko/db_worker.py` を追加し、tomoko-process が `v2_stt_observation` を LISTEN して `TomokoConversationCore` を実行し、semantic saturation / scheduler decision / speech-order を DB に保存して `v2_speech_order` を NOTIFY するようにした。
- `server/hot_path/db_conversation.py` を追加し、hot-path が STT observation を DB insert + NOTIFY し、`v2_speech_order` を LISTEN / recovery polling で受けて `SpeechOrderExecutor` と TTS/audio event 保存を実行するようにした。
- `TOMOKO_V2_DB_SPLIT=1` で `/ws` audio conversation が DB split path を使い、`python -m server.runtime process tomoko-db` で tomoko DB worker が起動するようにした。
- `make v2-db-split-smoke` / `scripts/v2_db_split_smoke.py` を追加し、別 process の hot-path + tomoko-db worker を起動して DB `LISTEN/NOTIFY` latency smoke を実行できるようにした。
- `README.md` / `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に DB split smoke の使い方、完了範囲、実測値を記録した。

### 詰まったこと・解決したこと
- 初回 smoke は tomoko-process が `PromptRequest.context_snapshot_id` を `v2_prompt_requests` に保存しようとして、未永続 `v2_context_snapshots` FK に当たり停止した。
  - 解決: DB split smoke では prompt request に未永続 context/utterance/candidate FK を持たせず、音声出力用 request row は hot-path が speech-order id で作る形にした。
- `make v2-db-split-smoke` は fake runtime で process/DB bridge の latency を測る。real Apple Speech / dflash / VOICEVOX の split latency は別途 live runtime 起動状態で測る必要がある。

### 検証
- `uv run ruff check server scripts background-process tests`
  - passed
- `uv run pytest -m unit -q`
  - 62 passed / 1 deselected
- `make v2-db-split-smoke`
  - passed
  - total 67.6ms / transcript->order 0.1ms / order->first audio 0.2ms
  - artifact: `logs/db-split-smoke-20260618-133937.json`

### 次のセッションでやること
- live dflash / VOICEVOX / Apple Speech 起動状態で DB split の real say latency smoke を追加または実行し、fake bridge latency と real perceived latency を分けて記録する。

## 2026-06-18 セッション13

### やること（開始時に書く）
- `PLAN.md` の Phase S1-S12 を上から最後まで対応する。
- テスト先行で speech-order DTO、semantic saturation、scheduler、tomoko-process 側 LLM 発話生成、hot-path speech-order executor、縦切り smoke、DB/NOTIFY bridge、runtime smoke/report まで実装する。
- 実機依存の real runtime / live overlap / calendar smoke は、可能な限り自動 smoke と readiness で確認し、未実行条件は `LOG.md` / `_docs/latency.md` に明記する。

### やったこと
- `SpeechOrder` / `SpeechSchedulerInput` / `SpeechSchedulerOutput` / `SemanticSaturationResult` などの DTO を追加し、round-trip / slots / enum test で固定した。
- `SemanticSaturationJudge` と固定行 `SATURATION=...` parser、deterministic fallback、stable prefix helper を追加した。
- `SpeechScheduler` を pressure model + threshold selection として実装し、replace / append / stop / suppress と `score_breakdown` logging を追加した。
- `server/llm/chat.py` を追加し、tomoko-process 側 `TomokoConversationCore` が LLMで発話本文だけを生成して `SpeechOrder` を返す経路を作った。
- `SpeechOrderExecutor` を追加し、replace / append queue / stop / generation guard を hot-path 側で実行できるようにした。
- `/ws` audio path は新しい scheduler 経路で `scheduler_decision` / `speech_order` event と binary WAV を返すようにした。旧 prompt event 互換 path は残した。
- `v2_speech_orders` / `v2_speech_scheduler_decisions` / `v2_semantic_saturation_observations` と `v2_speech_order` NOTIFY channel を追加した。
- `make v2-scheduler-conversation-smoke` / `make v2-scheduler-say-latency-smoke` / `make v2-scheduler-report` を追加した。
- client timeline は scheduler decision と speech order を表示するだけにし、判断ロジックは持たせていない。
- `PLAN.md` のチェックボックスを実施済み範囲だけ更新し、未実行の常駐LISTEN / live overlap / calendar smoke / final divergence は未チェックで残した。

### 詰まったこと・解決したこと
- S7 の DB 分離は schema / SQL bridge / integration DDL test までは実装したが、常駐 LISTEN worker と hot-path の DB 書き込み接続はまだ本線に入れていない。
- S8 は tmux runtime が既に `tomoko-v2-runtime` として起動済みだったため二重起動せず、`make v2-runtime-ready` と real scheduler say smoke で確認した。
- 現在の `/ws` scheduler audio path は `TomokoConversationCore` が LLM stream を内部で集約してから返すため、client 側の `model_delta` は first content の真の時刻ではなく batch 表示になる。first audio は artifact に記録した。

### 検証
- `make check`
  - ruff passed
  - unit 62 passed / 1 deselected
- `uv run pytest -m integration -q`
  - 1 skipped / 62 deselected (`TEST_DATABASE_URL` 未設定)
- `node --check client/main.js`
  - passed
- `git diff --check`
  - passed
- `make v2-scheduler-conversation-smoke`
  - fake vertical slice passed
  - artifact `logs/scheduler-conversation-smoke-20260618-131947.json`
- `make v2-conversation-smoke`
  - fake `/ws` scheduler path passed
  - event sequence includes `scheduler_decision` and `speech_order`
- `make v2-runtime-ready`
  - dflash `8081` / `8082` ready
  - VOICEVOX `50122` ready
  - STT/OCR sidecar source availability printed
- `make v2-scheduler-say-latency-smoke`
  - STT `智子短く返事して`
  - reply `了解。短く話すね。`
  - voice-end to first audio 2862.5ms
  - artifact `logs/scheduler-say-latency-20260618-132107.json`
- `make v2-scheduler-report`
  - generated `reports/v2-scheduler-report.html`

### 次のセッションでやること
- DB 常駐分離を本線化する: hot-path STT insert + `v2_stt_observation` NOTIFY、tomoko-process LISTEN、speech-order insert + NOTIFY、hot-path speech-order LISTEN、recovery polling。
- live overlap / stop / calendar append をブラウザ実操作または専用 replay で確認する。
- partial -> final divergence 時の replace / suppress を fake replay で固定する。

## 2026-06-18 セッション12

### やること（開始時に書く）
- 旧 `PLAN.md` を `PLAN.old.md` に退避する。
- `ARCHITECTURE.md` の新方針に合わせ、まず計算モデルを持って会話する Tomoko を実現するための新しい `PLAN.md` を作る。
- 既に完了した bootstrapping / runtime / DTO / smoke の成果は否定せず、新計画の前提として扱う。

### やったこと
- 旧 `PLAN.md` を `PLAN.old.md` に退避した。
- 新 `PLAN.md` を、`SpeechScheduler -> tomoko-process LLM -> speech-order -> hot-path TTS/audio` の縦切りを最初の主目標にして作り直した。
- Phase S1-S12 として、speech-order DTO、意味飽和度、SpeechScheduler、tomoko 側 LLM、hot-path speech executor、DB/NOTIFY bridge、real runtime smoke、overlap/append/partial/tuning の順に編纂した。
- 完了済みの root v2 bootstrap / runtime launcher / STT / OCR / VOICEVOX / dflash / smoke は、新 PLAN の前提として残した。

### 検証
- `PLAN.old.md` が存在することを確認した。
- 新 `PLAN.md` の Phase 見出しが S0-S12 まで揃っていることを確認した。
- `git diff --check -- PLAN.md PLAN.old.md LOG.md`
  - passed

### 次のセッションでやること
- Phase S1: `SpeechOrder` / `SpeechSchedulerInput` / `SpeechSchedulerOutput` などの DTO contract を実装し、unit test で固定する。

## 2026-06-18 セッション11

### やること（開始時に書く）
- Tomoko の VOICEVOX 発話速度は 2.0 では速すぎるため、1.5 倍を正式な既定にする。
- Makefile / runtime default / tests / README / MEMORY の速度期待値を一致させる。

### やったこと
- `VoicevoxChunkedTtsBackend` の既定 `speed` を `1.5` にした。
- `create_default_real_prompt_executor()` の `TOMOKO_V2_VOICEVOX_SPEED` 未指定時 fallback を `1.5` にした。
- `Makefile` の `TOMOKO_V2_VOICEVOX_SPEED ?= 1.5` と unit test の期待値を一致させた。
- README の VOICEVOX speed 既定値を `1.5` にした。

### 検証
- `make check`
  - ruff passed
  - unit 44 passed / 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること
- 実 runtime の live conversation で 1.5 倍の聴感と first audio latency を再確認する。

## 2026-06-18 セッション10

### やること（開始時に書く）
- LLM prompt の stable context に前回までの Tomoko 発話（LLM 推論結果）も載せる。
- user-only の `recent_user_raw` だけでなく、speaker が分かる会話履歴として prompt snapshot を作る。

### やったこと
- `ConversationHistoryItem(speaker, text)` を `server/shared/models.py` に追加した。
- `ContextSnapshot.recent_history` を追加し、従来の `recent_utterances` は互換用に残した。
- `PromptBuilderV2` は speaker 付き履歴がある場合、user を `recent_user_raw=...`、Tomoko を `recent_tomoko_raw=...` として prompt に出すようにした。
- `HotPathAudioConversation` は user durable utterance と LLM complete text を `_recent_history` に積み、次 turn の prompt に Tomoko 発話を載せるようにした。

### 詰まったこと・解決したこと
- `make check` はこの時点では、既存の未コミット `Makefile` 差分 `TOMOKO_V2_VOICEVOX_SPEED ?= 1.5` と unit test の `2.0` 固定期待がズレて失敗した。
- セッション11で 1.5 を正式採用し、Makefile / runtime default / tests を一致させた。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_models.py tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 24 passed
- `uv run python -m py_compile server/shared/models.py server/tomoko/context.py server/tomoko/prompt.py server/hot_path/audio_conversation.py`
  - passed
- `uv run ruff check server tests`
  - passed
- `make check`
  - ruff passed
  - unit 43 passed / 1 failed / 1 deselected
  - failure at the time: `tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order` が `TOMOKO_V2_VOICEVOX_SPEED ?= 2.0` を期待していたが、worktree の `Makefile` は `1.5`

### 次のセッションでやること
- live console で次 turn prompt に `recent_tomoko_raw=...` が出ることを確認する。

## 2026-06-18 セッション9

### やること（開始時に書く）
- UI timeline で blank final STT を表示しない。
- console に STT 結果、NOTIFY 送信、LLM prompt 全文を出す。
- 現在の実 server log から拾える STT hallucination を辞書で block し、block した事実も console に出す。
- hot-path / tomoko / LLM / VOICEVOX 実 runtime を通し、macOS `say` 音声の終話から Tomoko の最初の音声 binary 到着までを測る simulator を追加して実測する。

### やったこと
- `client/main.js` の timeline は final STT が blank の場合に item を追加しないようにした。
- hot-path / audio / LLM / DB NOTIFY の console-visible log を強化した。
  - STT final text: `stt_done final_text=...`
  - STT block: `stt_rule_blocked` / `stt_hallucination_blocked`
  - LLM prompt: `prompt_send` と `TOMOKO LLM PROMPT BEGIN/END` に挟んだ全文
  - NOTIFY: `notify_send channel=... payload=...`
- `TomokoProcessCore` に final STT block 辞書を追加し、実 log で繰り返していた `はい` / `い` と blank を durable utterance / prompt request に昇格しないようにした。
- `scripts/v2_say_latency_smoke.py` と `make v2-say-latency-smoke` を追加し、macOS `say` 生成音声を 16kHz mono float32 chunk として実 `/ws` に流す latency smoke を作った。
- Makefile の `TOMOKO_V2_VOICEVOX_SPEED` 既定値も実 runtime 起動時に効くよう `2.0` に揃えた。

### 詰まったこと・解決したこと
- hot-path の `audio_bytes` log は 128 sample ごとに出て STT/PROMPT log を埋めるため削除した。代わりに VAD segment 以降の意味のある境界を console に残す。
- `/ws` の transcript event は現状 `process_segment()` が LLM/TTS 実行まで完了してから client に返るため、tool 側では transcript / TTS / first audio の受信時刻がほぼ同時になった。server console では STT 完了と prompt 送信の順序は追える。
- `PLAN.md` の live acceptance P50/P95 や 10 分 smoke は今回の 1 回実測では未達なのでチェックは付けていない。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_runtime_foundation.py -q`
  - 28 passed
- `uv run python -m py_compile scripts/v2_say_latency_smoke.py && node --check client/main.js`
  - passed
- `make check`
  - ruff passed
  - unit 42 passed / 1 deselected
- `uv run python -m server.runtime readiness`
  - LLM `8081` / `8082`: true
  - VOICEVOX `50122`: true
  - Apple Speech / OCR binaries: true
  - database: false
- `make v2-say-latency-smoke`
  - input `トモコ、短く返事して。` / voice `Kyoko`
  - STT `智子短く返事して`
  - reply `了解。短く話すね。`
  - voice-end to first binary audio 2875.8ms
  - artifact `logs/say-latency-20260618-120425.json`

### 次のセッションでやること
- transcript event を LLM/TTS 完了前に client へ出すかを検討する。UI timeline の STT 表示を「STT完了時刻」として使いたいなら、現在の batch 返却では遅すぎる。
- live conversation を 10 分以上走らせ、first audio P50/P95 と block 辞書の過不足を `_docs/latency.md` に追記する。

## 2026-06-18 セッション8

### やること（開始時に書く）
- VOICEVOX の発話速度を 1.5 倍にし、Tomoko を早口にする。

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
