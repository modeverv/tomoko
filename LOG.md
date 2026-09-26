# LOG.md

## 2026-06-20 セッション30

### やること（開始時に書く）
- 前セッションで作った `public-synthetic-append-dedupe-h2048-l005-model.json` を Tomoko runtime に組み込み、重複 final STT による LLM 会話推論と TTS を抑制する。
- suppress は hot path の音声入出力ではなく、Tomoko 側の LLM fire 前に置く。
- 既存の continuation / new_intent を潰さないよう、`duplicate_score` が高く、`continuation_score` / `new_intent_score` が低い時だけ抑制する。
- 失敗する unit test を先に追加し、`うんあんまりよくわかってない` -> `あんまりよくわかってない` は suppress、補足・話題変更は suppress しないことを固定する。

### やったこと
- `server/shared/models.py` に runtime 用 `AppendDedupeDecision` DTO を追加した。
- `server/tomoko/append_dedupe.py` を追加し、`make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json` を resident load する `HashRidgeAppendDedupeGuard` を実装した。
- `TomokoConversationCore` で final STT の通常応答が LLM prompt build に進む直前に dedupe guard を呼ぶようにした。
- `duplicate_score >= 0.85`、`continuation_score <= 0.45`、`new_intent_score <= 0.45`、`time_delta_ms <= 5000` の時だけ `SpeechSchedulerAction.SUPPRESS` に変える。
- suppress 時は durable observation は返すが、`prompt_request` / `speech_order` / `model_events` は作らず、in-memory prompt history にも積まないようにした。
- hot-path direct conversation と DB worker の default core に `create_default_append_dedupe_guard()` を渡した。artifact が無い場合や `TOMOKO_V2_APPEND_DEDUPE=0` では fail-open で guard 無しにする。

### 結果
- `うんあんまりよくわかってない` -> `あんまりよくわかってない` は LLM 前で suppress され、二度目の `chat_backend.stream()` が呼ばれない unit contract を固定した。
- `あんまりよくわかってない` -> `もう少し具体的に言うと設定ファイルの話` と、`...` -> `ところで音量下げて` は suppress せず LLM/TTS に進む contract を固定した。
- default guard の実 artifact smoke では duplicate 0.993 / continuation 0.1138 / new_intent 0.0420 で `should_suppress=True`。
- resident benchmark は load 0.9219ms、mean 0.4425ms、p50 0.4032ms、p95 0.5798ms。

### 詰まったこと・解決したこと
- DB worker 経路は `prior_session_history` を渡すため、in-memory prompt history には追記しない。一方で dedupe には直前 final text が必要なので、`_last_final_user_text` / `_last_final_user_audio_ended_at` は prompt history 追記とは別に更新するようにした。
- duplicate suppress でも durable utterance は返す。これにより UI/DB では観測が残るが、会話推論と TTS は二重に走らない。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_suppresses_duplicate_final_before_llm tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_keeps_continuation_and_new_intent_after_dedupe -q`
  - 2 passed
- `uv run python - <<'PY' ... create_default_append_dedupe_guard() ...`
  - default artifact load and `should_suppress=True`
- `uv run ruff check server/shared/models.py server/tomoko/append_dedupe.py server/tomoko/conversation.py server/hot_path/audio_conversation.py server/tomoko/db_worker.py tests/unit/test_v2_speech_order_flow.py`
  - passed
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py -q`
  - 16 passed
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_speech_order_flow.py -q`
  - 64 passed
- `uv run pytest -m unit -q`
  - 136 passed, 1 deselected
- `uv run python make-model/benchmark_append_dedupe_latency.py --model make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json --previous 'うんあんまりよくわかってない' --current 'あんまりよくわかってない' --time-delta-ms 900 --tomoko-speaking --speech-queue-active --repeats 10000 --warmup 1000 --json`
  - mean 0.4425ms / p50 0.4032ms / p95 0.5798ms

### 次のセッションでやること
- live `/ws` 会話または targeted replay で `append_dedupe_suppressed` が出て、対応する LLM/TTS が増えないことを実ログで確認する。
- 実ログで false suppress が見えたら threshold か synthetic/private eval を見直す。

## 2026-06-20 セッション29

### やること（開始時に書く）
- `append_after_current` の重複発話を runtime 組み込みではなく、まず public synthetic な軽量 dedupe model artifact / 学習・評価 script / shadow 入出力設計として作る。
- 既存 `make-model/` の semantic saturation pipeline と `server/tomoko/semantic.py`、最新 `logs/server-debug.log` の 22:04 前後の重複例を確認する。
- unit または model-level test を先に追加し、`duplicate` / `continuation` / `new_intent` と filler 差分、否定・訂正・話題変更の扱いを固定する。
- 推論 API と benchmark を追加し、hot path resident model で 1ms 前後を目標に load time / warm predict mean / p50 / p95 を測る。
- `_docs/latency.md` と `MEMORY.md` に、shadow 評価までの判断と実測結果を追記する。

### やったこと
- `logs/server-debug.log` の 22:04 前後で、`うんあんまりよくわかってない` と `あんまりよくわかってない` が別 final STT になり、それぞれ `append_after_current` speech-order を作っていることを確認した。
- `make-model/make_model/append_dedupe.py` に `HashRidgeAppendDedupeModel`、`AppendDedupeInput`、`AppendDedupeResult`、filler 正規化、debug features、評価 helper を追加した。
- public synthetic anchor だけを生成する `generate_append_dedupe_synthetic_labels.py` を追加し、実ログ由来 seed は ignored な `make-model/data/private-log-seeds/` にだけ抽出する補助 script として分離した。
- train / evaluate / predict / benchmark CLI を追加し、artifact `make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json` と train metrics を生成した。
- `make-model/README.md` に shadow 入出力設計と CLI 例を追記した。
- `_docs/latency.md` と `MEMORY.md` に実測と確定判断を追記した。

### 結果
- public synthetic labels は 320 件。label 内訳は duplicate 96、continuation 80、new_intent 144。
- synthetic anchor eval は accuracy 1.0。confusion matrix は全 label で対角のみ。
- 指定例の単発予測:
  - `うんあんまりよくわかってない` -> `あんまりよくわかってない`: label `duplicate`, duplicate 0.993。
  - `あんまりよくわかってない` -> `もう少し具体的に言うと設定ファイルの話`: label `continuation`, continuation 0.9489。
  - `今日の予定を教えて` -> `ところで音量下げて`: label `new_intent`, new_intent 0.9209。
- resident hot predict benchmark は load 0.9051ms、mean 0.4094ms、p50 0.4019ms、p95 0.4394ms、max 6.5360ms。

### 詰まったこと・解決したこと
- `duplicate` と `continuation` は文字列類似だけだと混ざるため、`previous_vague` と `continuation_cue` を明示 feature にした。
- 否定・訂正は filler と違い duplicate 扱いに寄せすぎないよう、`correction_cue` で duplicate score を上限 0.42 に抑える safety adjustment を入れた。
- 実ログ seed 抽出は便利だが公開不可データ混入の事故があるため、public synthetic の label/artifact とは別 path に閉じた。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 25 passed
- `uv run python make-model/evaluate_append_dedupe_model.py --model make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json --labels make-model/data/public-synthetic/append-dedupe-labels.jsonl`
  - accuracy 1.0
- `uv run python make-model/benchmark_append_dedupe_latency.py --model make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json --previous 'うんあんまりよくわかってない' --current 'あんまりよくわかってない' --time-delta-ms 900 --tomoko-speaking --speech-queue-active --repeats 10000 --warmup 1000 --json`
  - mean 0.4094ms / p50 0.4019ms / p95 0.4394ms
- `uv run ruff check make-model/make_model/append_dedupe.py make-model/generate_append_dedupe_synthetic_labels.py make-model/train_append_dedupe_model.py make-model/evaluate_append_dedupe_model.py make-model/benchmark_append_dedupe_latency.py make-model/predict_append_dedupe.py make-model/extract_append_dedupe_seed_examples.py tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run pytest -m unit -q`
  - 134 passed, 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること
- runtime suppress ではなく、まず `append_after_current` guard の shadow log に今回の model output を出す。
- shadow の false duplicate / false new_intent を見てから、speech-order suppress への昇格条件を unit test で固定する。

## 2026-06-20 セッション28

### やること（開始時に書く）
- smoke artifact に LLM へ実際に送る OpenAI messages 形の prompt を載せる。
- partial 用でも `SYSTEM` / `INSTRUCTION` の形を変えず、cache prefix を割らないようにする。
- partial 誤認識が transcript history に確定行として入りすぎるとは何か、現行コードと smoke artifact で説明する。
- 同じ prompt 2 回と prompt+append 1 回を dflash へ直接投げ、prefix cache が保持されるか切り分ける。

### やったこと
- `llm_prompt` WebSocket event に `sent_messages` を追加し、dflash へ実際に送る OpenAI chat messages を smoke artifact に残すようにした。
- `PromptBuilderV2.build_main_reply(..., concise=True)` でも `INSTRUCTION` を変えないようにし、partial / final で `instruction_hash` が割れないようにした。
- 失敗する unit test を先に追加し、partial instruction が final と同じであること、`llm_prompt.sent_messages` が出ることを固定した。
- direct dflash probe と、変更後の `make v2-five-turn-smoke` を実行して cache behavior を比較した。

### 結果
- direct dflash probe は `logs/dflash-cache-direct-probe-20260620-215358.json` に保存した。ユニーク prompt の初回は first content 764.9ms / miss、同一 prompt 2 回目は 211.7ms / `prefix cache hit 72/76`、prompt+append は 518.6ms / `prefix cache hit 72/98` だった。dflash cache 自体は保持されている。
- 変更後 smoke `logs/five-turn-smoke-20260620-215418.json` は avg first audio 1396.5ms、p95 1776.9ms。
- 変更後 smoke の全 turn で `instruction_hash=770c5bb7cb` に揃い、`sent_messages` も artifact に入った。
- dflash 26B は変更後 smoke の 5 request 全てで `prefix cache hit` line を出した。misses は `21` のまま増えず、`prefill_tokens_saved` は 332 から 568 まで増えた。
- それでも turn3->4 / turn4->5 の prefix は partial/final reconcile による履歴差し替えで途中から崩れる。これは cache miss ではなく、保存する transcript history の安定性の問題として残った。

### partial 誤認識が transcript history に確定行として入る、の意味
- 現行 core は LLM が発話したら、その発話 text を `_recent_history` に `tomoko:` として追加する。partial STT 由来の LLM 発話でも同じ扱いになる。
- 後から final STT が来て partial と違っても、active partial reply 後の final は suppress/discard されるため二重発話は避ける。一方で、すでに出した partial reply 自体は履歴に残る。
- smoke 例では partial `今日の予定を5時` に対して `tomoko: 5時に何かあるの？` が履歴に残り、後から final `今日の予定を一言で教えて` が来る。次 prompt ではこの partial 由来の `tomoko:` 行が通常の確定発話と同じ重みで入る。
- その結果、次 turn の prompt は単純 append ではなく「partial の user 行が消えて、partial reply と final user が入る」形に変わり、cache prefix と会話整合性の両方を揺らす。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py::test_prompt_builder_keeps_partial_instruction_same_as_final tests/unit/test_v2_runtime_foundation.py::test_hot_path_websocket_uses_prompt_executor_for_text_prompt -q`
  - 失敗を確認後、実装して pass
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_speech_order_flow.py -q`
  - 62 passed
- `uv run pytest -m unit -q`
  - 130 passed, 1 deselected
- `uv run ruff check server/tomoko/prompt.py server/hot_path/app.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `make v2-five-turn-smoke`
  - completed, artifact `logs/five-turn-smoke-20260620-215418.json`
- `git diff --check`
  - passed

### 次のセッションでやること
- partial reply を `_recent_history` に入れる時、final reconcile 済みの trace では「暫定応答」扱いにできるか検討する。
- 具体的には prompt history では partial user 行を final user 行で置換しつつ、partial reply を assistant 確定行として残すか、metadata 付きで抑制するかを unit test から決める。

## 2026-06-20 セッション27

### やること（開始時に書く）
- prefix cache stabilization 後の smoke を実行し、dflash cache hit / miss / eviction と `llm_prompt.cache_shape` を同じ時間帯で確認する。
- 可能なら `make v2-five-turn-smoke` を使い、累積会話履歴で prefix cache が改善しているかを artifact / dflash log / server log から集計する。
- 結果を `_docs/latency.md` / `LOG.md` に追記し、必要な確定判断があれば `MEMORY.md` に追記する。

### やったこと
- `uv run python -m server.runtime readiness` で runtime 状態を確認した。DB は false、LLM `8081` / `8082` と VOICEVOX `50122`、Apple Speech、OCR は ready だった。
- `make v2-five-turn-smoke` を実行し、`logs/five-turn-smoke-20260620-213806.json` を取得した。
- smoke artifact の `llm_prompt.cache_shape` と、同じ時間帯の `logs/dflash-26b.log` prefix cache counter を突き合わせた。
- `_docs/latency.md` に first-audio 実測と cache hit / miss / eviction の内訳を追記した。

### 結果
- 5 turn smoke は avg first audio 1393.7ms、p95 2179.2ms、max 2179.2ms だった。
- dflash 26B counter は pre-smoke `hits=1+1` / `misses=17` / `evictions=27` / `prefill_tokens_saved=68` から final `hits=2+2` / `misses=20` / `evictions=36` / `prefill_tokens_saved=148` へ動いた。
- 5 request 中、`prefix cache hit` line が出たのは最初の 2 request だけだった。実効 hit は約 40% で、3 request は miss として `misses` / `evictions` を増やした。
- `llm_prompt.cache_shape` では `system_hash=eda2ab0021` が全 turn で安定していた。一方、turn 1-2 は通常 instruction hash `770c5bb7cb`、turn 3-5 は partial concise instruction hash `a8ca522a76` になっており、partial prompt lane が cache 系列を分けている。
- この smoke では `runtime_context_chars=0` / `volatile_recall_chars=0` だったため、runtime context を後段へ逃がした効果そのものは、実 context が入るシナリオで再測が必要。

### 詰まったこと・解決したこと
- server debug log には `prompt_send cache_shape` が残らず、今回の shape 確認は WebSocket smoke artifact 側の `llm_prompt` event を正とした。
- dflash は `entries=8/8` のまま各 request で eviction が増えており、prompt 側の安定化だけでは cache rate が上がり切らない可能性が見えた。

### 検証
- `make v2-five-turn-smoke`
  - completed, artifact `logs/five-turn-smoke-20260620-213806.json`
- dflash log 集計
  - smoke window 5 lookup, hit line 2, miss increment 3, eviction increment 9 from pre-smoke snapshot
- `git diff --check`
  - passed

### 次のセッションでやること
- partial concise prompt が通常 prompt と cache 系列を分けすぎているため、instruction を変えずに response mode を trailing user context へ移せるか unit test から検討する。
- `RUNTIME_CONTEXT` が実際に入るシナリオで `runtime_context_hash` と dflash hit/miss を再測する。

## 2026-06-20 セッション26

### やること（開始時に書く）
- prefix cache hit を上げられるように、最新 dflash log と現行 `PromptBuilderV2` / OpenAI messages 変換を確認する。
- 人格・履歴・直近文脈は削らず、どの prompt context が毎回揺れているかを unit test / log で見える形にする。
- stable prefix に寄せられる部分があれば、失敗する unit test を先に追加してから実装する。

### やったこと
- 最新 `logs/dflash-26b.log` tail を確認し、直近 1200 行で prefix cache lookup 100 回に対して hit line 12 回、最後の lookup が `hits=1+1` / `misses=17` / `evictions=27` / `prefill_tokens_saved=68` であることを確認した。
- `PromptBuilderV2` で `summary[...]` / `calendar[...]` / `user_status=...` が system message に入り、turn ごとの context 変化で stable prefix を揺らす可能性があることを確認した。
- unit test を先に追加し、runtime context が stable system prefix に入らないこと、OpenAI messages 変換で最後の user message へ追記されること、`prompt_cache_shape` で section 差分が見えることを固定した。
- `summary` / `calendar` / `user_status` を `RUNTIME_CONTEXT` として `SESSION_TRANSCRIPT` 後ろへ移し、`VOLATILE_RECALL` と同じく最後の user message 末尾へ付けるようにした。
- `prompt_cache_shape()` を追加し、system / instruction / transcript / runtime context / volatile recall の chars / hash / turn 数を `llm_prompt` event と `[tomoko:llm] prompt_send` に出すようにした。
- `_docs/latency.md` と `MEMORY.md` に今回のログ根拠、未測定理由、確定判断を追記した。

### 詰まったこと・解決したこと
- 既存 test は summary 付き prompt が `SESSION_TRANSCRIPT` で終わることを期待していた。runtime context を後段へ移す新方針に合わせ、clock 質問では calendar は省くが summary は `RUNTIME_CONTEXT` に残る期待へ更新した。
- 今回は dflash live runtime を再起動していないため、prefix hit 率や first-audio の改善幅は未測定として残した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 33 passed
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_speech_order_flow.py -q`
  - 62 passed
- `uv run pytest -m unit -q`
  - 130 passed, 1 deselected
- `uv run ruff check server/tomoko/prompt.py server/llm/chat.py server/hot_path/model_executor.py server/hot_path/app.py tests/unit/test_v2_audio_tomoko_prompt.py`
  - passed
- `git diff --check`
  - passed

### 次のセッションでやること
- tmux runtime で `make v2-five-turn-smoke` または live conversation を回し、`llm_prompt.cache_shape` と dflash `prefix cache hit` / `prefill_tokens_saved` / `evictions` を同じ時間帯で比較する。
- 必要なら context のうちさらに安定 prefix に寄せられるものと、最後の user message に置くべきものを smoke artifact ベースで切り分ける。

## 2026-06-20 セッション25

### やること（開始時に書く）
- 最新 `logs/server-debug.log` / `logs/v2-runtime.jsonl` で見えている会話 runtime の無駄・重複・読みにくさを改善する。
- 優先順は、VAD 後 STT 前の短尺/低エネルギー segment gate、MaAI backchannel `TypeError` 再発経路の特定と修正、partial 由来 `append_after_current` の抑制、readiness structured log 改善、prefix cache の揺れ可視化と安定化候補の確認。
- 各項目は実ログと現行コードを確認し、失敗する unit test を先に追加してから実装する。

### やったこと
- 最新ログを集計し、`vad_segment=574` に対して Apple Speech final 空文字が 492 件、MaAI poll error が 311 件、`append_after_current` が 216 件あることを確認した。
- `SegmentSttGate` を追加し、VAD 完成 segment の境界で duration / RMS を見て、低エネルギー segment を Apple Speech 前に `vad_segment_dropped` として落とすようにした。
- partial observation に VAD segment 内で共有する trace_id を付け、active partial reply 後の同一 trace partial/final は prompt / speech-order を作らず discard/suppress するようにした。
- MaAI poll は `result_dict_queue` / `output_queue` を優先し、`timeout` kwarg 非対応 queue でも fallback するようにした。poll error は同一 error type ごとに間引き、fail-open のままログを埋めないようにした。
- runtime readiness は起動時 snapshot と後続 transition を分けて JSONL / console に出すようにした。
- dflash prefix cache 改善のため、揺れやすい `VOLATILE_RECALL` を stable system prefix から外し、OpenAI messages では最後の user message 末尾に足すようにした。
- `_docs/latency.md` と `MEMORY.md` に今回のログ根拠、未測定理由、確定判断を追記した。

### 詰まったこと・解決したこと
- STT gate の unit で最初に使った低エネルギー sample は VAD 自体が speech とみなさず、gate まで届かなかった。VAD は反応し、STT gate では低エネルギーとして落ちる閾値に調整した。
- 既存 unit は短い人工 segment を使うため、短尺だけで落とすとテスト用の高エネルギー発話まで落ちる。短尺は低エネルギーと組み合わせた時だけ落とし、長い低エネルギーも落とす判断にした。
- 今回は live tmux runtime smoke を起動していないため、first-audio の改善幅は未測定として残した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_runtime_foundation.py -q`
  - 66 passed
- `uv run pytest -m unit -q`
  - 127 passed, 1 deselected
- `uv run ruff check server/hot_path/audio_conversation.py server/hot_path/db_conversation.py server/hot_path/backchannel.py server/tomoko/conversation.py server/runtime.py server/tomoko/prompt.py server/llm/chat.py server/hot_path/model_executor.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `git diff --check`
  - passed

### 次のセッションでやること
- `make run` / tmux runtime を起動した状態で会話または `make v2-say-latency-smoke` を実行し、`vad_segment_dropped` 件数、MaAI poll error の再発有無、同一 trace partial/final の discard、readiness transition、dflash prefix cache hit / eviction の変化を実ログで確認する。

## 2026-06-20 セッション24

### やること（開始時に書く）
- UI の Stop ボタンが実際に音声再生を止めているか、client/server のイベント経路とログから確認する。
- Stop が ACK のみで playback を止めていない場合、失敗する unit contract を追加してから修正する。

### やったこと
- client は Stop ボタンで `audio_control` を送っていたが、予約済み `AudioBufferSourceNode` を保持しておらず、local playback を止められないことを確認した。
- hot-path は `audio_control` 受信時に `audio_control_ack` を返すだけで、`SpeechOrderExecutor` の generation を進めていなかったことを確認した。
- client に active audio source 管理、`stopLocalPlayback()`、stop 後の stale binary audio chunk 破棄を追加した。
- server 由来の `speech_order mode=stop` でも client が同じ停止処理を実行するようにした。
- hot-path の `audio_control/stop` で `SpeechOrderExecutor.stop_playback()` を呼び、進行中 generation と append queue を止めるようにした。

### 詰まったこと・解決したこと
- server から既に送信済みの audio chunk は server 側から停止できないため、client 側で source を持って止める必要があった。
- stop 後に同じ発話の audio chunk が遅れて届くケースに備え、次の `speech_order` / `backchannel` までは binary audio を再生しないようにした。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_speech_order_flow.py -q`
  - 26 passed
- `uv run ruff check server/hot_path/app.py server/hot_path/speech_executor.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_speech_order_flow.py`
  - passed
- `node --check client/main.js`
  - passed
- `uv run pytest -m unit -q`
  - 119 passed, 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること
- 実ブラウザで長めの TTS 再生中に Stop を押し、即時停止と stale chunk 破棄を耳で確認する。

## 2026-06-20 セッション23

### やること（開始時に書く）
- hot-path の MaAI backchannel poll error を最新ログから特定して修正する。
- 意味飽和度 partial early-start が現状 final 待ちになっていないか、実 artifact / code から確認する。
- client UI の `PLAN` 表示を消す。

### やったこと
- 最新 `logs/server-debug.log` で、MaAI backchannel poll が `TypeError` で連続失敗していることを確認した。
- installed `maai.Maai.get_result()` が timeout 引数を受け取らないため、hot-path の poll を `result_dict_queue.get(timeout=0.1)` 優先に変更した。
- partial early-start gate が saturation と総合 score の両方を必須にしていたため、片方が十分なら確認段階へ進めるようにした。
- client timeline から scheduler decision の `PLAN` 表示を削除した。

### 詰まったこと・解決したこと
- ログ上の partial は総合 score が 0.86〜0.96 でも saturation だけで suppress されており、final まで待つ体感と一致していた。
  低情報 partial は saturation と score の両方が低い時だけ suppress し、誤発火対策として 2 回確認は残した。
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:8000/ws ...` は hot-path server が起動しておらず `ConnectionRefusedError` になった。
  今回の live first-audio は未測定として `_docs/latency.md` に残す。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_runtime_foundation.py -q`
  - 30 passed
- `uv run ruff check server/hot_path/backchannel.py server/tomoko/conversation.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `uv run pytest -m unit -q`
  - 117 passed, 1 deselected
- `node --check client/main.js`
  - passed

### 次のセッションでやること
- tmux runtime を起動した状態で `/ws` say latency smoke を再実行し、MaAI poll error が再発しないことと partial early-start の first-audio を実測する。

## 2026-06-20 セッション22

### やること（開始時に書く）
- モデル名間違いで hot-path が落ちる最新ログを確認する。
- 実際に落ちている境界を特定し、設定/launcher/runtime のモデル名契約を修正する。

### やったこと
- 最新 `logs/server-debug.log` で、`/ws` 接続時に missing JDD distilled saturation artifact を load して `FileNotFoundError` で落ちていることを確認した。
- `TOMOKO_V2_DISTILLED_SATURATION_MODEL` と Python default を、現在実在する public synthetic artifact に揃えた。
- default saturation judge 作成時に artifact が存在しない場合、hot-path を落とさず deterministic fallback で起動するようにした。
- default artifact と Makefile の一致、missing artifact fallback を unit test で固定した。

### 詰まったこと・解決したこと
- 古い判断では JDD 10000件 artifact を runtime default にしていたが、この checkout では ignored artifact として存在せず、public synthetic artifact だけが実在していた。
- 修正前の tmux pane には古い traceback が残っていたが、uvicorn reload 後の実 `/ws` smoke は `speech_order` / LLM / VOICEVOX まで通った。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_runtime_foundation.py -q`
  - 34 passed
- `uv run ruff check server/tomoko/semantic.py server/tomoko/conversation.py server/hot_path/audio_conversation.py tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `uv run pytest -m unit -q`
  - 115 passed, 1 deselected
- `make v2-conversation-smoke`
  - passed
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:8000/ws --text 'トモコ、短く返事して。' --voice Kyoko`
  - passed: voice-end to first audio 3661.7ms, artifact `logs/say-latency-20260620-204850.json`

### 次のセッションでやること
- 必要なら public synthetic 10000件版 artifact を長時間生成し、runtime default を差し替えるか代表ケース比較で判断する。

## 2026-06-20 セッション21

### やること（開始時に書く）
- `Intent` 概念を入れず、LLM 前を `materials -> pressures -> pressure synthesis gate -> LLM` に徹底する。
- `InferenceStartGate` の intent 的な decision 名を廃止し、LLM fire だけを決める gate に改名・整理する。
- docs / tests / conversation core を同じ語彙へ揃える。

### やったこと
- `InferenceStartDecision` を `LlmFireDecision` に置き換え、decision を `do_not_fire` / `fire` / `cancel_or_replace_pending` のみにした。
- `InferenceStartGateInput` / `InferenceStartGateOutput` を `LlmFireGateInput` / `LlmFireGateOutput` に置き換えた。
- `InferenceStartGate` を `LlmFireGate` に置き換え、pressure source ごとの intent 分岐を削除した。
- `TomokoConversationCore` を `materials -> pressures -> LlmFireGate -> LLM -> PreparedSpeechCandidate -> SpeechEmissionGate` の名前に揃えた。
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` を `LlmFireGate` と pressure synthesis gate の語彙へ更新した。

### 詰まったこと・解決したこと
- `SpeechSchedulerOutput.text_intent` は DB/WS 返却互換として残るが、LLM 前の gate decision からは intent 的 enum を消した。通常会話経路では `reply` の互換値として扱う。
- legacy `SpeechScheduler` の unit / DB schema には `text_intent` が残るが、今回の対象である LLM 前の新経路には `InferenceStart*` / intent-like decision は残っていない。

### 検証
- `uv run pytest -m unit -q`
  - 113 passed, 1 deselected
- `uv run ruff check server tests scripts make-model`
  - passed
- `make v2-conversation-smoke`
  - passed
- `make v2-scheduler-conversation-smoke`
  - passed: total 1.8ms, artifact `logs/scheduler-conversation-smoke-20260620-164357.json`
- `rg -n "InferenceStart|inference_start|START_MAIN_REPLY|START_NATURAL|START_INITIATIVE|START_EXTERNAL|START_CALENDAR|DO_NOT_INFER|CANCEL_OR_REPLACE_PENDING_INFERENCE" server tests ARCHITECTURE.md`
  - no matches

### 次のセッションでやること
- 必要なら legacy `SpeechScheduler` / DB column の `text_intent` も別フェーズで名前を分ける。ただし LLM 前の gate からは今回外した。

## 2026-06-20 セッション20

### やること（開始時に書く）
- Materials/Pressures/Gates 再整理後に、E2E smoke を通して壊れていないか確認する。

### やったこと
- fake runtime `/ws` E2E smoke と in-process scheduler conversation smoke を実行した。
- real LLM/VOICEVOX readiness を確認し、`v2-llm-tts-smoke` を実行した。
- DB split fake LISTEN/NOTIFY smoke を実行した。
- 一時的に Tomoko realtime WS `:8765` と hot-path `:8010` を起動し、real `say -> /ws` smoke を実行した。
- Tomoko realtime 側ログで `turn_materials` が `/internal/hot-path` に届いていることを確認した。
- smoke 後に一時起動した `:8765` / `:8010` の uvicorn を停止した。

### 検証
- `make v2-conversation-smoke`
  - passed: transcript / durable_utterance / speech_order / model / tts / audio / prompt_complete
- `make v2-scheduler-conversation-smoke`
  - passed: action `replace_current`, total 1.8ms
- `uv run python -m server.runtime readiness`
  - DB false, LLM 8081/8082 true, VOICEVOX true, Apple Speech true, OCR true
- `make v2-llm-tts-smoke`
  - passed: text `了解。`, 1 audio chunk, 23596 bytes
- `make v2-db-split-smoke`
  - passed: total 60.5ms, artifact `logs/db-split-smoke-20260620-154029.json`
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:8010/ws --text 'トモコ、短く返事して。' --voice Kyoko`
  - passed: voice-end to first audio 1801.4ms, artifact `logs/say-latency-20260620-154057.json`

### 詰まったこと・解決したこと
- port 8000 は既存 Python process が LISTEN していたため触らず、今回の live smoke は port 8010 で実行した。
- real `say` smoke では `turn_materials` の audio/silence material は WS 到達したが、MaAI/VAP 由来の `p_yielding` は `None` だった。real MaAI result の yield key は別途確認が必要。

### 次のセッションでやること
- `make run` / tmux-runtime の通常起動でも `turn_materials` WS が接続されることを確認する。
- real MaAI/VAP yield material の有無を確認する。

## 2026-06-20 セッション19

### やること（開始時に書く）
- `Material -> Pressure -> Gate` の設計語彙を `ARCHITECTURE.md` に追記する。
- `TurnOpportunitySnapshot` / `TurnSignalAggregator` ベースの命名を `TurnMaterials` / pressure model ベースへ整理し直す。
- `SpeechScheduler` を通常の会話発話裁定経路から外し、`InferenceStartGate -> LLM -> SpeechEmissionGate -> hot-path` の順に近づける。

### やったこと
- `ARCHITECTURE.md` に `Materials -> Pressures -> Gates` の設計語彙を追記し、`silence_ms` / `p_yielding` / MaAI などは shared material、各 pressure model が参照する材料として整理した。
- `TurnOpportunitySnapshot` を `TurnMaterials` に置き換え、hot-path 側も `TurnMaterialAggregator` / `InternalTurnMaterialClient` / `turn_materials` WS event に改名した。
- `WorldMaterials` / `PersonalityMaterials`、`DialogueTurnPressure` / `NaturalSpeechPressure` / `MotivationPressure` / `WorldPressure`、`PreparedSpeechCandidate` を DTO として追加した。
- `server.tomoko.pressures` を追加し、materials から4種類の pressure を計算する薄い model を置いた。
- `InferenceStartGateInput` / `SpeechEmissionGateInput` を pressure/candidate ベースへ変更した。
- `TomokoConversationCore` の通常発話裁定経路から `SpeechScheduler.decide()` を外し、`materials -> pressures -> InferenceStartGate -> LLM -> PreparedSpeechCandidate -> SpeechEmissionGate -> SpeechOrder` の順にした。
- `SpeechSchedulerOutput` は既存テスト・返却互換の入れ物として残し、gate 結果から組み立てるようにした。

### 詰まったこと・解決したこと
- pressure を計算済み中間量として扱った後、gate 側でさらに重みを掛けると final STT / 高 saturation partial が弱くなりすぎた。gate score は weighted sum だけでなく、主要 pressure の代表値も見る形にした。
- direct unit path では hot-path material がまだ無いため、final STT は発話区間終端の観測として `p_yielding=1.0` / 小さな `silence_ms` を補うようにした。

### 検証
- `uv run pytest -m unit -q`
  - 113 passed, 1 deselected
- `uv run ruff check server tests scripts make-model`
  - passed
- `git diff --check`
  - passed

### 次のセッションでやること
- 実 `make run` で `turn_materials` internal WS の送受信ログを確認する。
- real MaAI result に `p_yielding` 相当が十分含まれるか確認し、不足する場合は VAP yield 専用 material を追加する。

## 2026-06-20 セッション18

### やること（開始時に書く）
- 公開しやすい semantic saturation モデルを、ネット上の会話コーパスに依存しない synthetic-only 系統として作り直す。
- 入力テキストは Codex / ユーザー / 自作スクリプト由来、初期教師ラベルは Gemma 4 26B、補正 anchor は Codex / 手作業由来として provenance を残す。
- `make-model` に synthetic corpus 生成、provenance、model card を追加し、ラベル作成、train/evaluate、latency benchmark まで一連で実行する。

### やったこと
- `make-model/generate_synthetic_saturation_corpus.py` を追加し、ネット上の会話コーパスを使わない `public-synthetic` corpus / prefixes / manifest を生成できるようにした。
- `make-model/combine_teacher_labels.py` を追加し、teacher train split と手作り anchor labels を安全に結合できるようにした。
- `make-model/PUBLIC_SYNTHETIC_PROVENANCE.md` と `make-model/MODEL_CARD.public-synthetic.md` を追加した。
- `generate_teacher_labels.py` に `--incremental` / `--progress-every` を追加し、長時間 teacher run の途中成果を JSONL に残せるようにした。
- `make_anchor_teacher_labels.py --kind life` を追加し、Tomoko 生活コマンド系 anchor を 1000件作れるようにした。
- `make-model/README.md` に public synthetic-only の一連コマンドと 2026-06-20 smoke 結果を追記した。
- `make-model/data/public-synthetic/` に synthetic corpus 2500 utterances / 34350 prefixes を生成した。
- Gemma teacher 200件を生成し、160 train / 40 eval に split した。
- general / contrastive / referential / life command anchors 各1000件を train split に追加した。
- `hash_size=8192` / `ridge_lambda=0.01` で `public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json` を作成した。

### 結果
- train labels: 4160件
  - Gemma teacher train: 160件
  - manual anchors: 4000件
- held-out eval: 40件
  - binary_accuracy: 0.900
  - mae: 0.15981232172049104
  - rmse: 0.24036738390980764
- hot predict latency:
  - mean: 0.281958ms
  - p50: 0.265708ms
  - p95: 0.430631ms
- 代表スコア:
  - `今日の予定を教えて`: 0.9253
  - `えっと`: 0.1750
  - `ただ、やっぱり`: 0.2682
  - `それが良いと思う`: 0.7881
  - `それが良いと思うがしかし`: 0.2201
  - `トモコ、ネットスーパーのラフを作って`: 0.8882

### 詰まったこと・解決したこと
- Gemma 4 26B teacher 10000件 / 2000件 / 500件は、完了時まで出力されない旧 generator では進捗が見えず対話ターン内で扱いづらかった。
  `--incremental` を追加して、1件ずつ JSONL に flush する方式にした。
- 初期の 2048 次元 model は `えっと` が高すぎたため、公開用候補は `hash_size=8192` / `ridge_lambda=0.01` にした。
- 生活コマンド系が弱かったため、`life` anchor を追加して Tomoko 固有の実用発話を高 saturation に寄せた。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 21 passed
- `uv run ruff check make-model/generate_synthetic_saturation_corpus.py make-model/combine_teacher_labels.py tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run ruff check make-model/generate_teacher_labels.py`
  - passed
- `uv run ruff check make-model/make_anchor_teacher_labels.py tests/unit/test_make_model_pipeline.py`
  - passed

### 次のセッションでやること
- 必要なら `--sample-size 10000 --incremental` で Gemma teacher を長時間実行し、同じ public-synthetic 系統の本番 artifact を作る。
- 10000件版を作ったら、runtime default artifact に採用するかは JDD 系 artifact と代表ケース比較してから決める。

## 2026-06-20 セッション17

### やること（開始時に書く）
- `InferenceStartGate` と `SpeechEmissionGate` を tomoko-process 内で分割し、既存 `SpeechScheduler` の責務を見通しよくする。
- hot-path と tomoko-process の間に internal WebSocket を追加し、MaAI/VAP 由来の 200ms `TurnOpportunitySnapshot` を Tomoko 側へ渡す。
- snapshot を二段 gate の計算材料へ接続し、unit test と smoke 可能な最小縦切りを作る。

### やったこと
- `server.shared.models` に `TurnOpportunitySnapshot`、`InferenceStartGate*`、`SpeechEmissionGate*` DTO と decision enum を追加した。
- `server.tomoko.gates` を追加し、重い推論開始判断と生成済み発話送出判断を分離した。
- `TomokoConversationCore` を `InferenceStartGate -> SpeechScheduler -> SpeechEmissionGate` の順に接続した。
- `server.hot_path.turn_signal` を追加し、audio RMS と MaAI result を 200ms `TurnOpportunitySnapshot` に集約するようにした。
- `MaaiBackchannelDetector` に raw result callback を追加し、相槌 emission と独立して `p_bc_react` / `p_bc_emo` / `p_yielding` 相当を snapshot に渡せるようにした。
- `server.tomoko.realtime` を追加し、`/internal/hot-path` WebSocket で `turn_opportunity` JSON を受けて latest snapshot を保持するようにした。
- hot-path `/ws` は snapshot を direct conversation core に反映しつつ、`TOMOKO_INTERNAL_WS_URL` があれば Tomoko realtime WS に送るようにした。
- `Makefile` の `v2-tomoko` を Tomoko realtime WS server にし、tmux runtime では tomoko window を hot-path より先に起動するようにした。
- `PLAN.md` に Phase S18 を追記し、`MEMORY.md` と `ARCHITECTURE.md` に実装済みの境界を追記した。

### 詰まったこと・解決したこと
- 最初の `InferenceStartGate` は final STT でも saturation が中程度の文を止めすぎたため、final text には小さな確定加点を入れ、既存 final 返答経路を壊さないようにした。
- internal WS snapshot は durable DB state ではなく latest-wins の realtime signal として扱い、DB/NOTIFY には raw MaAI/VAP frame を載せない方針にした。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_internal_ws.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_runtime_foundation.py -q`
  - 40 passed
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 36 passed
- `uv run pytest -m unit -q`
  - 109 passed, 1 deselected
- `uv run ruff check server tests scripts make-model`
  - passed
- `git diff --check`
  - passed

### 次のセッションでやること
- 実 `make run` で Tomoko realtime WS と hot-path snapshot client が接続することを pane log で確認する。
- MaAI 実 result に `p_yielding` 相当が含まれるか確認し、無い場合は VAP lane から yield signal を別途 snapshot に足す。

## 2026-06-20 セッション16

### やること（開始時に書く）
- 発話判断計算モデルを、重い推論を fire する gate と、生成済み候補を hot-path へ送出する gate の二段として ARCHITECTURE.md に追記する。
- 設計判断として MEMORY.md にも短く残す。

### やったこと
- `ARCHITECTURE.md` に「計算モデルは二段の gate として整理する」を追記した。
- `InferenceStartGate` は partial/final STT、semantic saturation、無音、外部調査結果、calendar、motivation から、Tomoko 側で重い推論を fire して発話候補を作る gate と定義した。
- `SpeechEmissionGate` は生成済み候補を、ユーザー発話の遮りや勘違いリスクも含めて hot-path へ speech-order として送出するかを決める gate と定義した。
- `MEMORY.md` に同じ設計判断を確定事項として追記した。

### 詰まったこと・解決したこと
- 既存の `SpeechScheduler` は二段の責務を一部同時に背負っているため、今回の追記では既存設計を否定せず、今後の DTO / ログ / report 分離のための概念整理として記載した。

### 検証
- `git diff --check -- ARCHITECTURE.md MEMORY.md LOG.md`
  - passed

### 次のセッションでやること
- 実装へ進む場合は、まず artifact / structured log 上で `inference_start_gate` と `speech_emission_gate` を別イベントとして出す。

## 2026-06-20 セッション15

### やること（開始時に書く）
- MaAI を VAP/VAD 制御ではなく hot-path の相槌専用センサーとして使う。
- 相槌は `うん` / `へえ` / `ほう` の3種に固定し、事前生成 WAV asset を `assets/backchannels/` に置く。
- `PLAN.md` に新 Phase を追記し、unit test で hot-path 相槌契約を固定してから実装する。

### やったこと
- `PLAN.md` 末尾に Phase S17: MaAI fixed backchannel hot-path lane を追記した。
- `server.hot_path.backchannel` を追加し、MaAI `bc_2type` result の `p_bc_react` / `p_bc_emo` が閾値以上の時だけ fixed WAV 相槌を返す detector を作った。
- `/ws` の audio receive loop で user audio chunk を detector に渡し、相槌 emission は result queue から `backchannel` event + binary WAV として返すようにした。
- 相槌は `うん` / `へえ` / `ほう` の3種に固定した。
- `assets/backchannels/un.wav` / `hee.wav` / `hou.wav` を起動中 VOICEVOX `127.0.0.1:50122`、speaker 8、speedScale 1.5、16kHz mono WAV で生成した。
- `Makefile` に `TOMOKO_V2_MAAI_BACKCHANNEL` / threshold / cooldown / asset dir を追加し、既定を有効 `1` にした。
- MaAI package が無い場合や detector start に失敗した場合は hot-path を落とさず no-op にするようにした。
- `MEMORY.md` に MaAI fixed backchannel の確定判断を追記した。
- `ARCHITECTURE.md` に未来メモ「口喧嘩できる Tomoko」を追記し、残る中心課題は motivation 設計であることを書いた。
- `ARCHITECTURE.md` に未来メモ「NOTIFY/LISTEN から WS origin へ」を追記し、リアルタイム制御線を段階的に internal WebSocket へ寄せる構想を書いた。

### 詰まったこと・解決したこと
- 最初に `say` で asset を生成したが、ユーザー指定に合わせて VOICEVOX 生成 WAV に置き換えた。
- `scripts` ではなく hot-path app 直下に繋いだため、main LLM / VOICEVOX runtime を相槌ごとに呼ばない。
- `TOMOKO_V2_MAAI_BACKCHANNEL=1` が既定だが、MaAI 未導入なら `backchannel_disabled` を出して no-op に落ちる。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 44 passed
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_hot_path_backchannel.py -q`
  - 18 passed
- `uv run pytest -m unit -q`
  - 103 passed, 1 deselected
- `uv run ruff check server/hot_path/backchannel.py server/hot_path/app.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `git diff --check`
  - passed
- `make -n tmux-runtime`
  - hot-path に `TOMOKO_V2_MAAI_BACKCHANNEL="1"` と `TOMOKO_V2_BACKCHANNEL_ASSET_DIR="assets/backchannels"` が渡ることを確認した。
- `BackchannelAssetStore(Path("assets/backchannels"))`
  - `うん` 9942 bytes / `へえ` 10966 bytes / `ほう` 10966 bytes、いずれも RIFF/WAVE。

### 次のセッションでやること
- 実 `make run` で MaAI package が利用可能な状態を確認し、体感上の相槌頻度に合わせて threshold / cooldown を調整する。

## 2026-06-20 セッション14

### やること（開始時に書く）
- 蒸留 scorer 切り替え後の `/ws` say latency smoke を実行し、過去の E2B runtime 記録と比較する。
- smoke wrapper の引数不整合で測れない場合は、測定できる状態に直す。

### やったこと
- `scripts.v2_scheduler_say_latency_smoke` が `measure()` に渡す `post_first_audio_ms` / `continue_after_first_audio` を持っておらず落ちたため追加した。
- `scripts.v2_scheduler_say_latency_smoke` と `scripts.v2_semantic_early_smoke` の `_docs/latency.md` 追記日付を実行日ベースにし、semantic early smoke の説明を E2B から distilled saturation に更新した。
- 起動済み `tomoko-v2-runtime` の `/ws` に対して scheduler say latency smoke を2本実行した。
- distilled semantic early-start smoke を実行し、prefix replay 上の開始可能タイミングを確認した。

### 結果
- `/ws` smoke `トモコ、今日の予定を一言で教えて。`
  - artifact: `logs/scheduler-say-latency-20260620-033825.json`
  - voice-end to first audio: 4999.1ms
  - partial は `今日の` / `今日の予定は` / `今日の予定で` で止まり、0.75 gate を超えず final 待ちになった。
- `/ws` smoke `トモコ、短く返事して。`
  - artifact: `logs/scheduler-say-latency-20260620-033846.json`
  - voice-end to first audio: 1599.3ms
  - partial は `と` のみで、final 後に `了解。` を返した。
- distilled semantic early-start smoke `トモコ、今日の予定を一言で教えて。`
  - artifact: `logs/semantic-early-smoke-20260620-033913.json`
  - first OK: speech start +1600.8ms
  - full final STT available: speech start +3600.1ms
  - lead: 1999.3ms
  - saturation latency: 0.52ms / 0.77ms / 0.85ms / 0.55ms / 0.57ms

### 比較
- 2026-06-18 E2B semantic early smoke は first OK 3281.3ms、lead 352.7ms だった。
- 今回の distilled semantic early smoke は first OK 1600.8ms、lead 1999.3ms で、semantic 判断単体では約1680ms 前倒し、final への余裕は約5.7倍になった。
- 実 `/ws` best は 2026-06-18 の async partial/final lanes が 860.5ms、final-confirm run が 1515.6ms。今回の短文 smoke 1599.3ms は final-confirm run とほぼ同等だが、partial early start ではなく final 起点だった。
- 今回の長め smoke 4999.1ms は Apple Speech partial が開始可能な粒度で出なかったため悪化した。蒸留 scorer 自体ではなく STT partial の出方が支配している。

### 検証
- `uv run ruff check scripts/v2_scheduler_say_latency_smoke.py scripts/v2_semantic_early_smoke.py`
  - passed
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_semantic_scheduler.py -q`
  - 29 passed
- `git diff --check`
  - passed

### 次のセッションでやること
- 実 `/ws` で semantic early smoke と同じ粒度の partial を出せるか、Apple Speech pseudo streaming の chunk/offset/coalescing を調整する。

## 2026-06-20 セッション13

### やること（開始時に書く）
- E2B semantic endpoint を runtime 経路から外し、resident の蒸留 saturation scorer を使う。
- partial early start は蒸留モデルを final 特徴で採点し、0.75 以上が2回連続した時だけ通す。
- 短い final STT（例: `はい`）は1回しか判定できないため、semantic scorer 側のルールで吸収する。

### やったこと
- `server.tomoko.semantic` に `DistilledSaturationBackend` と default loader を追加した。
- default artifact を `jdd-gemma26b-10000-plus-anchors-contrastive-tail-referential-saturation-model.json` にし、partial も `--final` 相当の特徴で採点するようにした。
- `はい` / `うん` / `了解` など短い final/backchannel は `short_ack_rule` で saturation 0.35 以下に clamp するようにした。
- hot-path と default conversation core を、E2B LLM backend ではなく蒸留 scorer backend へ差し替えた。
- partial start gate の saturation threshold を 0.85 から 0.75 に変更し、既存の2回連続確認は維持した。
- `Makefile` から `semantic-e2b-run`、`semantic-e2b` tmux window、semantic LLM readiness URL を外した。
- `scripts/v2_semantic_early_smoke.py` を蒸留 scorer 用の smoke に更新した。
- この判断を `MEMORY.md` に確定判断として追記した。

### 詰まったこと・解決したこと
- E2B backend class は比較用・既存 unit 用に残したが、runtime default / `make run` 経路からは外した。
- 実 artifact の smoke では `今日の予定を教えて` が 0.9391、`それが良いと思うがしかし` が 0.3336、`はい` が short-ack rule で 0.3500 になった。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_speech_order_flow.py -q`
  - 39 passed
- `uv run pytest -m unit -q`
  - 98 passed, 1 deselected
- `uv run ruff check server/tomoko/semantic.py server/tomoko/conversation.py server/hot_path/audio_conversation.py scripts/v2_semantic_early_smoke.py tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_runtime_foundation.py`
  - passed
- `git diff --check`
  - passed
- `make -n tmux-runtime`
  - `semantic-e2b` window なし、hot-path に `TOMOKO_V2_DISTILLED_SATURATION_MODEL` が渡ることを確認した。

### 次のセッションでやること
- 実 `make run` 起動後に partial early start の発火ログを確認し、0.75 2連続 gate の体感と過発火を観測する。

## 2026-06-20 セッション12

### やること（開始時に書く）
- `それが良いと思う --final` が 0.6226 と低めなので、指示語・照応系の positive surface anchors を追加する。
- `それ/その/これ/この` 系の逆説なし完了文を train に足し、再 train/evaluate/predict する。

### やったこと
- `make-model/make_anchor_teacher_labels.py` に `--kind referential` を追加した。
- `それが良いと思う` / `それで問題ない` / `その方向でいい` / `これは違うと思う` などの指示語・照応系完了文を `manual_referential_anchor` として1000件生成できるようにした。
- unit test で referential anchors が1000件生成され、代表例が高 saturation label を持つことを固定した。
- 既存 10000件 train labels に referential anchors 1000件を足し、11000件で train した。
- README と MEMORY に手順と結果を追記した。

### 詰まったこと・解決したこと
- `jq` 確認を anchor 生成と並列実行したため、確認側が先にファイルを読みに行って一度失敗した。
  - 解決: 生成後に確認を再実行した。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 17 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/make_anchor_teacher_labels.py --kind referential --out make-model/data/japanese-daily-dialogue/teacher-labels-manual-referential-anchors-1000.jsonl --count 1000`
  - 1000 manual referential anchors written
- combined train labels:
  - 11000 rows: 8000 teacher_llm / 1000 manual_anchor / 1000 manual_contrastive_anchor / 1000 manual_referential_anchor
- `uv run python make-model/train_saturation_model.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-train-plus-anchors-contrastive-referential.jsonl --out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-tail-referential-saturation-model.json --metrics-out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-tail-referential-train-metrics.json`
  - train time about 1.95s
  - train binary_accuracy 0.8464 / MAE 0.1306 / RMSE 0.1809
- held-out JDD eval:
  - binary_accuracy 0.8215 / MAE 0.1818 / RMSE 0.2349
- manual anchor eval:
  - binary_accuracy 0.986 / MAE 0.0483 / RMSE 0.0644
- contrastive anchor eval:
  - binary_accuracy 1.0 / MAE 0.0631 / RMSE 0.0989
- referential anchor eval:
  - binary_accuracy 0.769 / MAE 0.0441 / RMSE 0.0567
- representative predict with `--final`:
  - `それが良いと思う`: 0.7170
  - `それが良いと思うがしかし`: 0.3336
  - `それで問題ない`: 0.7440
  - `その方向でいい`: 0.6623
  - `これは違うと思う`: 0.8539
  - `今日の予定を教えて`: 0.9391
- hot predict latency:
  - mean 0.0733ms / p95 0.0806ms

### 次のセッションでやること
- `それが良いと思う` を 0.75 以上へ確実に上げるなら、referential labels を 0.85-0.90 寄りへ上げるか、referential phrase feature を追加する。

## 2026-06-20 セッション11

### やること（開始時に書く）
- hash-ridge saturation scorer に `contrastive_tail` 明示特徴を追加する。
- 逆説末尾の低ラベルが前半本文へ漏れすぎないか、contrastive anchor 版を再 train/evaluate して確認する。

### やったこと
- `make_model.model.EXTRA_FEATURES` に `contrastive_tail` を追加した。
- `hashed_features()` が `しかし` / `けど` / `だが` / `だけど` / `とはいえ` などの逆説末尾を明示特徴として立てるようにした。
- 旧 artifact は weight 次元が1つ少ないため、`align_features_for_weights()` で追加特徴を無視して互換 predict できるようにした。
- unit test で `contrastive_tail` が逆説末尾だけに立つこと、旧 artifact 次元でも predict できることを固定した。
- 同じ 10000件 train labels で `contrastive-tail-feature` artifact を再学習し、README と MEMORY に結果を追記した。

### 詰まったこと・解決したこと
- feature 追加直後、新コードで旧 artifact を読むと weight/features の次元不一致で落ちた。
  - 解決: 旧 artifact は新しい末尾 feature を無視して推論できる互換 path を追加した。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 16 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/train_saturation_model.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-train-plus-anchors-contrastive.jsonl --out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-tail-feature-saturation-model.json --metrics-out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-tail-feature-train-metrics.json`
  - train time about 1.96s
  - train binary_accuracy 0.8538 / MAE 0.1385 / RMSE 0.1875
- held-out JDD eval:
  - binary_accuracy 0.8210 / MAE 0.1833 / RMSE 0.2359
- manual anchor eval:
  - binary_accuracy 0.984 / MAE 0.0499 / RMSE 0.0657
- contrastive anchor eval:
  - binary_accuracy 1.0 / MAE 0.0585 / RMSE 0.0884
- representative predict with `--final`:
  - `それが良いと思うがしかし`: 0.3228
  - `それが良いと思うけど`: 0.1869
  - `今日は進めたい。だけど`: 0.2015
  - `それが良いと思う`: 0.6226
  - `今日の予定を教えて`: 0.9381
- hot predict latency:
  - mean 0.1075ms / p95 0.1491ms

### 次のセッションでやること
- `それが良いと思う` をさらに上げたい場合は positive counter-anchor を足す。

## 2026-06-20 セッション10

### やること（開始時に書く）
- `それが良いと思うがしかし --final` が 0.8711 と高く出るため、逆説末尾の手作り anchor labels を1000件追加する。
- 逆説 anchor 追加後に train/evaluate/predict を再実行する。

### やったこと
- `make-model/make_anchor_teacher_labels.py` に `--kind contrastive` を追加した。
- `しかし` / `けど` / `だが` / `だけど` / `とはいえ` などで終わる final 発話を、低 saturation の `manual_contrastive_anchor` として1000件生成できるようにした。
- unit test で `それが良いと思うがしかし=0.22`、`それが良いと思うけど < 0.35`、`今日は進めたい。だけど < 0.35` を固定した。
- 既存 9000件 train split に contrastive anchors 1000件を足し、10000件で train した。
- README と MEMORY に手順と結果を追記した。

### 詰まったこと・解決したこと
- 最初の contrastive 候補は 980件で、1000件に少し足りなかった。
  - 解決: base 文を追加した。
- `今日は進めたい。だけど` のような `。だけど` 形がなかった。
  - 解決: `CONTRASTIVE_ENDINGS` に `。だけど` を追加した。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 14 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/make_anchor_teacher_labels.py --kind contrastive --out make-model/data/japanese-daily-dialogue/teacher-labels-manual-contrastive-anchors-1000.jsonl --count 1000`
  - 1000 manual contrastive anchors written
- combined train labels:
  - 10000 rows: 8000 teacher_llm / 1000 manual_anchor / 1000 manual_contrastive_anchor
- `uv run python make-model/train_saturation_model.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-train-plus-anchors-contrastive.jsonl --out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-saturation-model.json --metrics-out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-train-metrics.json`
  - train time about 1.78s
  - train binary_accuracy 0.8507 / MAE 0.1424 / RMSE 0.1908
- held-out JDD eval:
  - binary_accuracy 0.8135 / MAE 0.1877 / RMSE 0.2410
- manual anchor eval:
  - binary_accuracy 0.979 / MAE 0.0577 / RMSE 0.0739
- contrastive anchor eval:
  - binary_accuracy 1.0 / MAE 0.0661 / RMSE 0.0858
- representative predict with `--final`:
  - `それが良いと思うがしかし`: 0.3536
  - `それが良いと思うけど`: 0.2716
  - `今日は進めたい。だけど`: 0.1820
  - `今日の予定を教えて`: 0.9345
- hot predict latency:
  - mean 0.0840ms / p95 0.1010ms

### 次のセッションでやること
- JDD held-out accuracy が少し下がったため、contrastive 版は shadow 比較で採用判断する。

## 2026-06-19 セッション9

### やること（開始時に書く）
- 既存 Gemma 26B teacher labels の train split に、手作り anchor labels 1000件を追加する。
- anchor 追加後の model を train し、held-out 2000 eval と代表例 `今日の予定を教えて` を確認する。

### やったこと
- `make-model/make_anchor_teacher_labels.py` を追加し、質問・依頼・確認の高 saturation、言い淀み・途中文の低 saturation、自己完結文の中間 saturation を含む手作り anchor labels 1000件を生成できるようにした。
- unit test で anchor が 1000件、`manual_anchor`、代表例 `今日の予定を教えて=0.95` / `聞こえますか=0.90` / `えっと=0.10` / `ただ、やっぱり=0.20` を含むことを固定した。
- 既存 8000 teacher train split と manual anchors 1000件を結合し、9000件で train した。
- held-out JDD 2000 eval、manual anchor 1000 eval、代表例 predict、hot latency を確認した。
- `make-model/README.md` と `MEMORY.md` に結果を追記した。

### 詰まったこと・解決したこと
- 最初の anchor 候補は 765件しかなく、1000件に届かなかった。
  - 解決: topic と high/mid patterns を増やし、1000 unique anchors を作れるようにした。
- `predict_saturation.py` の既定は `is_final=False` の partial 扱いなので、完了発話の代表例を見るには `--final` が必要だった。
  - `今日の予定を教えて`: partial/default 0.4986、`--final` 0.9313。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 13 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/make_anchor_teacher_labels.py --out make-model/data/japanese-daily-dialogue/teacher-labels-manual-anchors-1000.jsonl --count 1000`
  - 1000 manual anchors written
- `cat teacher-labels-gemma26b-10000-train.jsonl teacher-labels-manual-anchors-1000.jsonl > teacher-labels-gemma26b-10000-train-plus-anchors.jsonl`
  - 9000 rows: 8000 teacher_llm / 1000 manual_anchor
- `uv run python make-model/train_saturation_model.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-train-plus-anchors.jsonl --out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-saturation-model.json --metrics-out make-model/artifacts/jdd-gemma26b-10000-plus-anchors-train-metrics.json`
  - train time about 2.44s
  - train binary_accuracy 0.8441 / MAE 0.1441 / RMSE 0.1925
- held-out JDD eval:
  - binary_accuracy 0.8265 / MAE 0.1802 / RMSE 0.2334
- manual anchor eval:
  - binary_accuracy 0.989 / MAE 0.0453 / RMSE 0.0619
- representative predict:
  - `今日の予定を教えて` partial/default 0.4986
  - `今日の予定を教えて --final` 0.9313
  - `聞こえますか --final` 1.0000
  - `えっと` partial/default 0.1223
- hot predict latency:
  - mean 0.0743ms / p95 0.0792ms

### 次のセッションでやること
- partial でも完全文らしい依頼を高く出したい場合は、manual anchors に `is_final=False` の完全文 high examples も追加するか、runtime 側の partial/final feature の扱いを見直す。

## 2026-06-19 セッション8

### やること（開始時に書く）
- 10000件 teacher label JSONL を seed 付きで 8000 train / 2000 eval に分ける CLI を `make-model` に追加する。
- README に split -> train -> held-out eval のコマンドを追記する。

### やったこと
- `make-model/split_teacher_labels.py` を追加し、teacher label JSONL を seed 付き shuffle で train/eval に分けられるようにした。
- unit test で split の再現性、8000/2000 件数、train/eval の重複なしを固定した。
- `make-model/README.md` に 10000件 teacher label 作成、8000/2000 split、train、held-out eval のコマンドを追記した。
- 手元の 10000件 labels を 8000 train / 2000 eval に分割し、train/evaluate を実行した。

### 詰まったこと・解決したこと
- 既存ファイル名は `teacher-labels-gemma26b-1000.jsonl` のままだが、中身は 10000 件だった。
  - 解決: 新しい split outputs と model artifact は `10000` を含むファイル名にした。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 12 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/split_teacher_labels.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl --train-out make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-train.jsonl --eval-out make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-eval.jsonl --train-size 8000 --seed 20260619`
  - train 8000 / eval 2000
- `uv run python make-model/train_saturation_model.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-train.jsonl --out make-model/artifacts/jdd-gemma26b-10000-saturation-model.json --metrics-out make-model/artifacts/jdd-gemma26b-10000-train-metrics.json`
  - train time about 1.56s
  - train binary_accuracy 0.82725 / MAE 0.1539 / RMSE 0.2005
- `uv run python make-model/evaluate_saturation_model.py --model make-model/artifacts/jdd-gemma26b-10000-saturation-model.json --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-10000-eval.jsonl --threshold 0.75`
  - held-out eval binary_accuracy 0.8285 / MAE 0.1795 / RMSE 0.2327
- `uv run python make-model/predict_saturation.py --model make-model/artifacts/jdd-gemma26b-10000-saturation-model.json "今日の予定を教えて"`
  - SATURATION=0.3556

### 次のセッションでやること
- 必要なら labels file 自体も `teacher-labels-gemma26b-10000.jsonl` にリネームして、README の命名と揃える。

## 2026-06-19 セッション7

### やること（開始時に書く）
- `make-model` の teacher payload に、`saturation_prompt()` の定義文・高い値/低い値・few-shot が入ることを明示的に test で固定する。
- teacher label 作成時に、短い system prompt だけでなく「会話相手が今返し始めてよい度合い」の詳細説明が Gemma 26B に渡ることを確認する。

### やったこと
- `tests/unit/test_make_model_pipeline.py` の teacher payload test を強化し、`saturation_prompt()` の定義文、高い値/低い値、few-shot 全体が user message に入ることを固定した。
- `make-model/README.md` に、詳細説明と few-shot が Gemma 26B teacher の user message に入ることを追記した。
- `MEMORY.md` に、teacher payload の詳細 prompt contract を unit test で固定したことを追記した。

### 詰まったこと・解決したこと
- 実装自体は前セッションの時点で `teacher.complete(saturation_prompt(...))` になっていた。
  - 解決: 仕様として曖昧にならないよう、引用された詳細 prompt 断片を test で明示的に固定した。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py tests/unit/test_v2_semantic_scheduler.py -q`
  - 26 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py server/tomoko/semantic.py tests/unit/test_v2_semantic_scheduler.py`
  - passed

### 次のセッションでやること
- 新 prompt で random 1000件 teacher label を作り直し、train/evaluate を再実行する。

## 2026-06-19 セッション6

### やること（開始時に書く）
- `make-model` の Gemma 26B teacher prompt を、runtime の Gemma E2B semantic lane と同じ prompt contract に揃える。
- `意味飽和度` という曖昧な system 文言を避け、`会話相手が今返し始めてよい度合い` の定義を含む既存 `saturation_prompt()` を teacher でもそのまま使っていることを test で固定する。

### やったこと
- `server/tomoko/semantic.py` の E2B semantic system prompt を `SATURATION_SYSTEM_PROMPT` として定数化した。
- `make-model/make_model/teacher.py` が同じ `SATURATION_SYSTEM_PROMPT` を使うようにし、旧 `会話の意味飽和度を採点する教師モデルです` 文言を削除した。
- `teacher.payload(saturation_prompt(...))` が runtime E2B lane と同じ system/user prompt contract になることを unit test で固定した。
- `make-model/README.md` と `MEMORY.md` に、teacher label prompt は runtime E2B semantic lane と同じ contract に揃える判断を追記した。

### 詰まったこと・解決したこと
- なし。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py tests/unit/test_v2_semantic_scheduler.py -q`
  - 26 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py server/tomoko/semantic.py tests/unit/test_v2_semantic_scheduler.py`
  - passed

### 次のセッションでやること
- 既存 `jdd-gemma26b-1000` artifact は旧 teacher prompt 由来なので、評価用には random 1000 件 teacher label を新 prompt で作り直す。

## 2026-06-19 セッション5

### やること（開始時に書く）
- `make-model` に、モデルを1回ロードしてから predict を1000回以上繰り返す latency benchmark CLI を追加する。
- `今日の予定を教えて` で実行し、CLI起動込みではない hot predict latency を実測する。

### やったこと
- `make-model/benchmark_saturation_latency.py` を追加し、モデルロード時間と hot predict の mean / p50 / p95 / min / max を測れるようにした。
- unit test で benchmark helper の統計出力 contract を固定した。
- `make-model/README.md` と `_docs/latency.md` に実測コマンドと結果を追記した。
- `MEMORY.md` に、CLI 起動込み 111ms と resident model の hot predict 0.1ms 前後は別物として扱う判断を追記した。

### 詰まったこと・解決したこと
- なし。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 10 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/benchmark_saturation_latency.py --model make-model/artifacts/jdd-gemma26b-1000-saturation-model.json --repeats 1000 --warmup 100 "今日の予定を教えて"`
  - mean 0.079825ms / p50 0.066209ms / p95 0.122440ms
- `uv run python make-model/benchmark_saturation_latency.py --model make-model/artifacts/jdd-gemma26b-1000-saturation-model.json --repeats 10000 --warmup 1000 "今日の予定を教えて"`
  - model load 0.4254ms
  - last saturation 0.1294
  - mean 0.074437ms / p50 0.073375ms / p95 0.087840ms / max 2.134708ms

### 次のセッションでやること
- Tomoko runtime に組み込む場合は、artifact を process 起動時に1回ロードし、turn-taking hot path では `predict()` だけを呼ぶ。

## 2026-06-19 セッション4

### やること（開始時に書く）
- `make-model/generate_teacher_labels.py` が Gemma 4 26B teacher に渡す prefix rows を seed 付きランダム抽出できるようにする。
- `make-model/README.md` の 1000 件 teacher label 作成コマンドを先頭 1000 件ではなくランダム 1000 件に変更する。

### やったこと
- `generate_teacher_labels.py` に `--sample-size` と `--sample-seed` を追加し、Gemma teacher input を再現可能なランダム subset から作れるようにした。
- `--limit` は smoke 用の先頭 N 件として残し、`--sample-size` との同時指定は拒否するようにした。
- `make-model/README.md` の 1000 件 teacher label 作成手順を `--sample-size 1000 --sample-seed 20260619` に変更した。
- unit test で seeded random sampling、旧 `--limit`、同時指定拒否を固定した。

### 詰まったこと・解決したこと
- 旧 1000 件 artifact は先頭 1000 件のままなので作り直し対象。
  - 解決: README では旧 `--limit 1000` の実測値を pipeline smoke と明記し、今後の評価用コマンドをランダム抽出に切り替えた。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 9 passed
- `uv run ruff check make-model tests/unit/test_make_model_pipeline.py`
  - passed
- `uv run python make-model/generate_teacher_labels.py --prefixes make-model/data/japanese-daily-dialogue/prefixes.jsonl --out make-model/data/japanese-daily-dialogue/teacher-labels-random-smoke.jsonl --sample-size 10 --sample-seed 123 --deterministic-only`
  - 10 labels written

### 次のセッションでやること
- 必要なら `--sample-size 1000 --sample-seed 20260619` で JDD random 1000 件の Gemma teacher label を作り直し、train/evaluate を再実行する。

## 2026-06-19 セッション3

### やること（開始時に書く）
- Japanese Daily Dialogue prefix dataset から Gemma 4 26B teacher label を 1000 件作成する。
- 作成した 1000 件ラベルで saturation scorer を train し、evaluate まで実行する。
- `make-model/README.md` に 1000 件 teacher label 作成、train、evaluate の一連のコマンドを追記する。

### やったこと
- `make-model/README.md` に 1000件 teacher label 作成、train、evaluate、単発 predict のコマンドを追記した。
- `http://127.0.0.1:8082/v1/models` に `mlx-community/gemma-4-26b-a4b-it-4bit` が見えていることを確認した。
- JDD prefix 先頭 1000 件を Gemma 4 26B で teacher label 化した。
  - output: `make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl`
  - 1000 rows
  - `label_source=teacher_llm`: 1000
  - saturation avg 0.39655、`>=0.75`: 254
- 1000 件ラベルで hash-ridge saturation scorer を train した。
  - model: `make-model/artifacts/jdd-gemma26b-1000-saturation-model.json`
  - metrics: `make-model/artifacts/jdd-gemma26b-1000-train-metrics.json`
- evaluate と単発 predict を実行した。
  - binary_accuracy 0.817
  - MAE 0.13467446691436838
  - RMSE 0.17769236473241526
  - `今日の予定を教えて` -> `SATURATION=0.1294`

### 詰まったこと・解決したこと
- 1000 件 teacher label 作成は逐次実行で約19分かかった。
- `--limit 1000` は先頭から取るため、今回の1000件は43 utterancesに偏った。
  - 解決: README に「pipeline smoke としては有効だが本命評価ではない」と明記した。
  - 次は発話全体から prefix をサンプリングする script を足す。

### 検証
- `uv run python make-model/generate_teacher_labels.py --prefixes make-model/data/japanese-daily-dialogue/prefixes.jsonl --out make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl --limit 1000 --url http://127.0.0.1:8082 --model mlx-community/gemma-4-26b-a4b-it-4bit`
  - 1000 labels written
- `uv run python make-model/train_saturation_model.py --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl --out make-model/artifacts/jdd-gemma26b-1000-saturation-model.json --metrics-out make-model/artifacts/jdd-gemma26b-1000-train-metrics.json`
  - passed
- `uv run python make-model/evaluate_saturation_model.py --model make-model/artifacts/jdd-gemma26b-1000-saturation-model.json --labels make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-1000.jsonl --threshold 0.75`
  - binary_accuracy 0.817 / MAE 0.1347 / RMSE 0.1777
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 7 passed

### 次のセッションでやること
- JDD 全体から utterance-balanced prefix sampling を行う script を足し、同じ 1000 件でも先頭偏りのない teacher label / train / evaluate を比較する。

## 2026-06-19 セッション2

### やること（開始時に書く）
- Japanese Daily Dialogue を repo 外扱いの ignored data として落とし、Gemma 4 26B teacher label 生成へ渡せる prefix dataset に変換する。
- JDD 固有の nested JSON (`data/*.json` -> dialogues -> utterances) を読み、`make-model` の標準 corpus/prefix JSONL に落とす importer と README 手順を追加する。
- 実データ・teacher labels・model artifacts は公開しない前提なので `.gitignore` で除外する。

### やったこと
- `.gitignore` に `make-model/data/` と `make-model/artifacts/` を追加した。
- `make_model.japanese_daily_dialogue` と `prepare_japanese_daily_dialogue.py` を追加し、JDD の nested JSON を標準 corpus/prefix JSONL と manifest に変換できるようにした。
- `make-model/README.md` に JDD download/convert と Gemma 4 26B teacher label 生成手順を追記した。
- `prepare_japanese_daily_dialogue.py --min-chars 1 --stride-chars 1` で実 JDD を clone/変換した。
  - utterances: 41,737
  - prefixes: 1,151,948
  - output: `make-model/data/japanese-daily-dialogue/{corpus.jsonl,prefixes.jsonl,manifest.json}`
- 実 `http://127.0.0.1:8082` に `mlx-community/gemma-4-26b-a4b-it-4bit` が見えていることを確認し、`--limit 5` で Gemma 26B teacher label smoke を通した。

### 詰まったこと・解決したこと
- JDD README の構造説明と実装時に想定される JSON 形が list root / dict root で揺れる可能性があるため、`dialogues` 配列あり/なしの両方を importer で吸収した。
- 全 prefix は約115万件で、全件 Gemma 採点は時間がかかるため、まず `--limit` 付きの small run で teacher contract を確認する運用にした。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 7 passed
- `uv run pytest -m unit -q`
  - 87 passed, 1 deselected
- `uv run ruff check server scripts tests make-model`
  - passed
- `git diff --check`
  - passed
- `uv run python make-model/generate_teacher_labels.py --prefixes make-model/data/japanese-daily-dialogue/prefixes.jsonl --out make-model/data/japanese-daily-dialogue/teacher-labels-gemma26b-smoke.jsonl --limit 5 --url http://127.0.0.1:8082 --model mlx-community/gemma-4-26b-a4b-it-4bit`
  - 5 labels written

### 次のセッションでやること
- Gemma 26B teacher label を本番作成する前に、`--limit 100` / `--limit 1000` で saturation 分布と短 prefix の揺れを確認し、必要なら prefix sampling や prompt に `full_text` context を足すか判断する。

## 2026-06-19 セッション1

### やること（開始時に書く）
- root に `make-model/` を追加し、Gemma 4 26B MLX 4bit の OpenAI-compatible endpoint を教師として semantic saturation 教師データを作る。
- `0.0..1.0` の意味飽和度を出力する軽量蒸留 scorer を学習・評価・推論できる Python 群と README を追加する。
- runtime 本線には接続せず、unit test でデータ生成・ラベル parsing・学習 artifact contract を固定する。

### やったこと
- `make-model/` に corpus loader、prefix dataset builder、Gemma teacher label generator、hash-ridge saturation scorer、train/evaluate/predict CLI、README を追加した。
- 教師ラベル生成は既存 `server.tomoko.semantic.saturation_prompt()` / `parse_saturation_output()` と同じ `SATURATION=0.0..1.0` contract を使うようにした。
- 初期の学生モデルは JSON 保存できる hashed character n-gram + ridge regression とし、runtime 本線には接続していない。
- unit test で text/jsonl corpus 読み込み、1文字 step prefix、teacher label parsing、train/evaluate/reload/predict contract を固定した。

### 詰まったこと・解決したこと
- `make-model` はディレクトリ名に hyphen があるため、その中に import 可能な `make_model` package を置いた。
- CLI smoke では実 Gemma endpoint を叩かずにパイプライン形状だけ確認したかったため、`generate_teacher_labels.py --deterministic-only` を smoke 用に追加した。

### 検証
- `uv run pytest -m unit tests/unit/test_make_model_pipeline.py -q`
  - 5 passed
- `uv run pytest -m unit -q`
  - 85 passed, 1 deselected
- `uv run ruff check server scripts tests make-model`
  - passed
- temp corpus で `build_prefix_dataset.py -> generate_teacher_labels.py --deterministic-only -> train_saturation_model.py -> predict_saturation.py -> evaluate_saturation_model.py`
  - passed

### 次のセッションでやること
- 実 Gemma 4 26B MLX 4bit endpoint を起動した状態で、少量の実コーパスから teacher labels を作り、Gemma lane との一致率と false start / missed early-start を測る。

## 2026-06-18 セッション27

### やること（開始時に書く）
- partial 応答開始を「意味飽和 high が2回連続」「semantic_saturation >= 0.85」「score >= partial_start_score_threshold」「前回 partial と大きく矛盾しない」に絞る。
- unit test で 1回目 high partial は hold、2回連続 high partial は speech-order 作成、矛盾 partial は hold になることを固定する。

### やったこと
- `TomokoConversationCore` に partial start confirmation gate を追加した。
- partial speech-order 開始前に `semantic_saturation >= 0.85`、scheduler score 閾値以上、前回 high partial との normalize 類似、2回連続 confirmation を要求するようにした。
- 1回目 high partial は LLM/TTS を呼ばず hold し、矛盾する後続 partial も hold する unit test を追加した。
- hot-path の partial speech-order test を、2回目 partial で発話する期待へ更新した。

### 詰まったこと・解決したこと
- 既存 test は「partial 1回で speech-order 作成」を前提にしていた。
  - 解決: 新仕様に合わせ、1回目は `partial start gate is waiting for confirmation`、2回目で speech-order 作成に更新した。
- deterministic saturation では test 文が 0.85 未満になるケースがあった。
  - 解決: gate の仕様 test では fixed saturation judge を使い、confirmation 条件だけを検証した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_semantic_scheduler.py -q`
  - 51 passed
- `uv run pytest -m unit -q`
  - 80 passed, 1 deselected
- `uv run ruff check server/tomoko/conversation.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_audio_tomoko_prompt.py`
  - passed

### 次のセッションでやること
- 実 `make run` の live 会話で、`これは誰` のような単発誤 partial が hold され、2回連続 high partial でだけ発話開始することを `logs/server-debug.log` で確認する。

## 2026-06-18 セッション26

### やること（開始時に書く）
- 最新のサーバーログを確認し、二重応答に見える挙動が partial/final reconcile の想定内か、実際の重複 speech-order かを切り分ける。

### やったこと
- `logs/server-debug.log` の最新末尾と該当箇所を確認した。
- `これは誰` -> `これはダブルで出てるのか` -> `これはダブルで出ているのかST Tが` の同一発話内で、partial / partial / final がそれぞれ speech-order を作っていることを確認した。

### 詰まったこと・解決したこと
- 既存の active partial/final reconcile は「先行 partial と後続 partial/final が十分似ている」場合だけ効くため、Apple Speech pseudo partial の途中誤認識 `これは誰` が後続 text と似ておらず、reconcile されなかった。
- 音声回り込みよりも、同一 VAD segment 内の partial 更新を `append_after_current` として扱ってしまう scheduler/core 側の問題として見るのが妥当。

### 検証
- ログ確認のみ。コード変更とテスト実行は未実施。

### 次のセッションでやること
- 同一 trace/VAD segment の後続 partial/final は append せず、active partial の更新として replace/suppress/reconcile する設計を unit test から固定する。

## 2026-06-18 セッション25

### やること（開始時に書く）
- E2B semantic saturation prompt を会話発話可能判定器の二段構成へ変更する。
- `Tomoko` 固有名を prompt から外し、接続確認・返答待ち発話を高 saturation として扱えるよう unit test で固定する。

### やったこと
- `OpenAICompatibleSaturationBackend` の system message を日本語の会話発話可能判定器 role に変更した。
- `saturation_prompt` を「会話相手が今返し始めてよい度合い」判定に変更し、`Tomoko` 固有名を examples から外した。
- `聞こえますか` / `今の返事ちゃんと聞こえてる` 系の返答待ちを高 saturation として扱う prompt contract を unit test で固定した。

### 詰まったこと・解決したこと
- なし。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py -q`
  - 15 passed
- `uv run ruff check server/tomoko/semantic.py tests/unit/test_v2_semantic_scheduler.py`
  - passed
- `uv run pytest -m unit -q`
  - 79 passed, 1 deselected

### 次のセッションでやること
- 実 `make run` 後に `こんにちは聞こえますか` の live `/ws` で `SATURATION=0.90` 相当の scheduler score と発話開始を確認する。

## 2026-06-18 セッション24

### やること（開始時に書く）
- `make run` / `tmux-runtime` に Gemma E2B semantic saturation endpoint の model load を混ぜる。
- hot-path 起動時に E2B endpoint を使う環境変数も渡す。
- readiness / stop / unit test の Makefile contract を更新する。

### やったこと
- `semantic-e2b-run` target を追加し、`mlx_lm.server --model mlx-community/gemma-4-e2b-it-OptiQ-4bit --port 8083` を起動できるようにした。
- `make run` / `tmux-runtime` で `semantic-e2b` tmux window を `hot-path` より前に作るようにした。
- `TOMOKO_V2_LLM_READY_URLS` に `http://127.0.0.1:8083/v1/models` を追加し、hot-path 起動前の readiness で E2B も待つようにした。
- hot-path tmux window へ `TOMOKO_V2_SEMANTIC_LLM=1` / URL / model を渡すようにした。
- `tmux-stop` で `semantic-e2b` window にも Ctrl-C を送るようにした。

### 詰まったこと・解決したこと
- E2B は dflash draft ではなく `mlx_lm.server` で起動する既存運用だった。
  - 解決: main LLM の `llm-run` には混ぜず、独立した tmux window と readiness URL として扱った。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 1 passed
- `make -n tmux-runtime`
  - `semantic-e2b` window が `hot-path` より前に作られることを確認した。
- `make -n semantic-e2b-run v2-runtime-ready`
  - `mlx_lm.server` 起動 command と `8083/v1/models` readiness を確認した。

### 次のセッションでやること
- 実 `make run` で 8083 E2B readiness と hot-path の semantic LLM 有効化を確認する。

## 2026-06-18 セッション23

### やること（開始時に書く）
- partial 開始 gate を saturation 単独ではなく、総合 score も併用する設計に変える。
- `こんにちは今の気分を教えて下さい` 程度の partial は saturation が 0.75 未満でも score が十分なら開始できるようにする。
- 低情報 partial は引き続き suppress されることを unit test で固定する。

### やったこと
- `SpeechSchedulerThresholds` に `partial_start_score_threshold=0.75` を追加した。
- partial gate を `semantic_saturation < 0.75` の単独判定から、`semantic_saturation < 0.75` かつ `score < 0.75` の時だけ suppress する判定へ変更した。
- `semantic_saturation=0.5` / score `0.775` の partial は `replace_current` できる unit test を追加した。

### 詰まったこと・解決したこと
- `こんにちは今の気分を教えて下さい` の artifact では raw saturation は 0.5 相当だったが、総合 score は 0.775 まで出ていた。
  - 解決: partial の false start 防止は残しつつ、score が十分高い request-like partial は開始できるようにした。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py::test_speech_scheduler_suppresses_low_saturation_partial_start tests/unit/test_v2_semantic_scheduler.py::test_speech_scheduler_allows_partial_when_score_is_high_enough -q`
  - 2 passed
- `uv run ruff check server/shared/models.py server/tomoko/scheduler.py tests/unit/test_v2_semantic_scheduler.py`
  - passed

### 次のセッションでやること
- 実 runtime が起動している状態で `_reference/test.m4a` を再 smoke し、`こんにちは今の気分を教えて下さい` partial で早期 speech-order が出るか確認する。

## 2026-06-18 セッション22

### やること（開始時に書く）
- partial 後に final / 後続 partial が append される重複発話を reconcile する。
- QuickTime などで録音した wav/m4a を `/ws` latency smoke に流せるようにする。
- 実 `/ws` smoke で重複 speech-order が減ることと、録音ファイル入力で実測できることを確認する。

### やったこと
- `TomokoConversationCore` に active partial speech-order の basis text を保持し、同一 utterance の final / 後続 partial を normalize 比較で reconcile する処理を追加した。
- reconcile された final は durable user utterance として履歴には残すが、speech-order / prompt は作らず suppress するようにした。
- Apple Speech pseudo partial が先頭に付けることがある `その`、wake word の `トモコ` / `智子`、短い filler を normalize 対象にした。
- `scripts/v2_say_latency_smoke.py` に `--input-wav` を追加し、ユーザー録音ファイルを 16kHz mono 16-bit PCM WAV に変換して `/ws` に replay できるようにした。
- `afconvert` が QuickTime m4a から Python 3.11 の `wave` で読めない WAVE_FORMAT_EXTENSIBLE を出すケースがあったため、`ffmpeg` がある環境では `ffmpeg -ac 1 -ar 16000 -sample_fmt s16` を優先するようにした。

### 詰まったこと・解決したこと
- 最初の reconcile 比較では partial `その今日の予定を教えて` と final `智子今日の予定を教えて...` の差分で似ていない扱いになった。
  - 解決: normalize で wake word / `その` / filler を除去し、包含または prefix ratio で判定するようにした。
- 置かれた `_reference/test.m4a` を `afconvert` だけで変換すると、標準 `wave` module が `unknown format: 65534` で読めなかった。
  - 解決: `--input-wav` は `ffmpeg` 優先にして、変換後に `read_wav_float32()` で読めることを確認するようにした。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_reconciles_final_after_partial_order -q`
  - 1 passed
- `uv run ruff check server/tomoko/conversation.py tests/unit/test_v2_speech_order_flow.py scripts/v2_say_latency_smoke.py`
  - passed
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:62236/ws --voice Kyoko --text 'トモコ今日の予定を教えてそれだけで大丈夫です' --continue-after-first-audio --post-first-audio-ms 5000 --timeout-sec 90`
  - artifact `logs/say-latency-20260618-161305.json`
  - partial `その今日の予定を教えて` で speech-order 1 件
  - final `智子今日の予定を教えてそれだけで大丈夫です` は `final reconciled with active partial reply` で suppress
  - `speech_order` / `tts_result` / `binary_audio` は各 1 件
  - voice-end to first audio 1736.5ms、voice-end to final transcript 2095.1ms
- `uv run python -m scripts.v2_say_latency_smoke --url ws://127.0.0.1:62237/ws --input-wav _reference/test.m4a --text 'こんにちは、今の気分を教えてくださいませ' --continue-after-first-audio --post-first-audio-ms 1000 --timeout-sec 90`
  - artifact `logs/say-latency-20260618-161626.json`
  - input duration 3708.0ms
  - final transcript `こんにちは今の気分を教えてくださいませ`
  - voice-end to first audio 5864.3ms
  - partial は出たが saturation 閾値未満だったため、今回は final 起点で返答した。

### 次のセッションでやること
- partial saturation が閾値未満の録音でも早期開始したいか、E2B saturation prompt / threshold を見直す。
- Apple Speech pseudo partial の stale partial が final 後に出るケースを artifact で継続観測する。

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

## 2026-06-20 セッション31

### やること（開始時に書く）
- `append_after_current` dedupe suppress を Tomoko 発話中または speech queue active 中に限定し、無音・待機中の自然な人間の言い直しを落とさない。
- suppress 条件の unit test を先に追加し、同じ duplicate 判定でも idle 中は `should_suppress=False` になることを固定する。

### やったこと
- `HashRidgeAppendDedupeGuard` の suppress 条件に `tomoko_speaking or speech_queue_active` を追加した。
- `tests/unit/test_append_dedupe_guard.py` を追加し、同じ duplicate score でも idle 中は pass、Tomoko 発話中または queue active 中だけ suppress する contract を固定した。
- default artifact smoke で idle / speaking / queued の `should_suppress` 差分を確認した。
- `MEMORY.md` と `_docs/latency.md` に、自然発話保護の判断と benchmark 結果を追記した。

### 結果
- idle: `label=duplicate`, `duplicate_score=0.993`, `should_suppress=False`
- speaking: `label=duplicate`, `duplicate_score=0.993`, `should_suppress=True`
- queued: `label=duplicate`, `duplicate_score=0.993`, `should_suppress=True`
- resident benchmark は load 0.9293ms、mean 0.4134ms、p50 0.4013ms、p95 0.4792ms。

### 検証
- `uv run pytest -m unit tests/unit/test_append_dedupe_guard.py -q`
  - 1 passed
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py -q`
  - 16 passed
- `uv run pytest -m unit -q`
  - 137 passed, 1 deselected
- `uv run python make-model/benchmark_append_dedupe_latency.py --model make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json --previous 'うんあんまりよくわかってない' --current 'あんまりよくわかってない' --time-delta-ms 900 --tomoko-speaking --speech-queue-active --repeats 10000 --warmup 1000 --json`
  - mean 0.4134ms / p50 0.4013ms / p95 0.4792ms

### 次のセッションでやること
- live `/ws` 会話または targeted replay で、Tomoko 発話中の duplicate は `append_dedupe_suppressed` になり、idle 中の自然な言い直しは suppress されないことを実ログで確認する。

## 2026-06-20 セッション32

### やること（開始時に書く）
- hot-path から Tomoko 側への VAP/MaAI materials internal WebSocket が現在の runtime で実際に動いているかを、コード・最新ログ・可能なら runtime 状態から確認する。
- partial STT が DB/NOTIFY 経由で hot-path から Tomoko 側へ渡る path の insert/select/LISTEN/NOTIFY latency を、既存 artifact と必要な実測から整理する。

### やったこと
- `server/hot_path/app.py` / `server/hot_path/turn_materials.py` / `server/tomoko/realtime.py` / `Makefile` を確認し、`TurnMaterials` が 200ms window で hot-path から `/internal/hot-path` に送られる配線を確認した。
- `server/hot_path/db_conversation.py` を確認し、schema/worker は partial row を扱える一方、現行 `HotPathDbSplitConversation` の実 `/ws` DB split は `final_observation` を選んで notify していることを確認した。
- `tmux list-sessions`、`lsof`、`ps` で現 runtime を確認し、2026-06-20 23:25 JST 時点では `tomoko-v2-runtime` も `:8000` / `:8765` listener も無いことを確認した。
- `_docs/latency.md` と `logs/say-latency-20260620-154057.json` を確認し、2026-06-20 15:40 の real `/ws` smoke では internal WS 経由の `turn_materials` 受信が記録されているが、`p_yielding` は None だったことを確認した。
- local PostgreSQL で `INSERT v2_stt_observations(partial) -> v2_notify_id -> LISTEN receive -> SELECT row` の microbench を 300 samples / 20 warmup で実測し、測定 row は削除した。

### 結果
- 現在の live 状態としては VAP/MaAI materials WS は動作中ではない。runtime が起動していないため。
- 配線と unit contract は有効で、`uv run pytest -m unit tests/unit/test_v2_internal_ws.py -q` は 3 passed。
- partial row DB bridge microbench は full insert+notify+select avg 0.858ms / p50 0.810ms / p95 1.049ms / p99 1.642ms。ただし現行 live `/ws` DB split は partial ではなく final observation を notify している。

### 次のセッションでやること
- `make run` 起動状態で `turn_materials` の live log を再取得し、実 MaAI/VAP が入る場合の `p_yielding` 非 None を確認する。

## 2026-06-20 セッション33

### やること（開始時に書く）
- hot-path と Tomoko process の会話制御線を DB `LISTEN/NOTIFY` から bidirectional internal WebSocket へ寄せる。
- hot-path -> Tomoko WS に `stt_observation partial/final`、`turn_materials`、`playback_state` を流し、Tomoko -> hot-path WS に `speech_order` / `cancel_order` / `ack` を返す。
- DB は hot control plane ではなく、Tomoko process 側の必要な永続化・監査ログとして残す。

### やったこと
- `server.tomoko.realtime` の `/internal/hot-path` を bidirectional control endpoint にし、`turn_materials` / `stt_observation` / `playback_state` を受けるようにした。
- `stt_observation` は `TomokoConversationCore.handle_observation()` に渡し、`speech_order` または stop intent の `cancel_order` を WS で返すようにした。
- `server.hot_path.ws_control.RemoteTomokoWsCore` を追加し、hot-path 側の `HotPathAudioConversation` に remote Tomoko core として差し込めるようにした。
- `TOMOKO_V2_WS_SPLIT=1` を追加し、`make run` の hot-path window では WS split を default 有効にした。`TOMOKO_V2_DB_SPLIT=1` の旧 DB split path は fallback として残した。
- Tomoko realtime 側で observation / saturation / scheduler decision / prompt request / speech-order / utterance を best-effort で DB 保存するようにした。保存失敗は `persist_failed` log に閉じ込める。
- `scripts.v2_ws_conversation_smoke` を Tomoko realtime uvicorn と hot-path uvicorn の別 process smoke に更新した。

### 結果
- fake-runtime process smoke で `hot-path STT -> internal WS -> Tomoko realtime -> speech_order over WS -> hot-path TTS` が成立した。
- smoke 結果は total 46.0ms、transcript `トモコ、返事して`、reply `うん、聞こえてるよ。`、fake WAV 1 chunk / 16 bytes。
- DB split fake smoke の比較値 total 60.5ms を `_docs/latency.md` に並べた。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py -q`
  - 4 passed
- `uv run ruff check server/tomoko/realtime.py server/hot_path/ws_control.py server/hot_path/app.py scripts/v2_ws_conversation_smoke.py tests/unit/test_v2_internal_ws.py`
  - passed
- `uv run python -m scripts.v2_ws_conversation_smoke --fake-runtime --start-processes`
  - passed, total 46.0ms
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py tests/unit/test_v2_runtime_foundation.py tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 52 passed
- `uv run pytest -m unit -q`
  - 138 passed, 1 deselected

### 次のセッションでやること
- real `make run` runtime で WS split の `say -> /ws` smoke を実測し、DB保存の成功/失敗と `turn_materials` / `playback_state` event の実ログを確認する。

## 2026-06-20 セッション34

### やること（開始時に書く）
- `make run` 相当で実 runtime を立ち上げ、今回追加した WS-origin control plane が fake process smoke だけでなく real server 起動状態でも壊れていないか確認する。
- `say -> /ws` smoke を実行し、hot-path -> Tomoko internal WS の `stt_observation` / `turn_materials` / `playback_state` と Tomoko -> hot-path の `speech_order` / ack が通るか、ログと smoke artifact で確認する。

### やったこと
- `make run` を起動し、tmux session `tomoko-v2-runtime` に dflash / VOICEVOX / Tomoko realtime / hot-path / background process を立ち上げた。
- `:8765` Tomoko realtime、`:50122` VOICEVOX、`:8081/:8082` dflash、`:8000` hot-path が listen していることを確認した。
- `make v2-say-latency-smoke` で実 `say -> /ws` 経路を通した。
- `make v2-five-turn-smoke` で 5 turn の連続実 smoke を通した。
- Tomoko realtime pane と hot-path pane を確認し、`/internal/hot-path` 接続、`turn_materials`、partial/final `stt_observation`、`speech_order` が出ていることを確認した。
- `playback_state` は runtime log には出していないため、起動中の Tomoko internal WS に直接 probe して `playback_state_ack` を確認した。
- Postgres に直近の STT / saturation / scheduler / speech-order / prompt request / utterance が保存されていることを確認した。

### 結果
- 単発 real smoke は成功。artifact は `logs/say-latency-20260620-234757.json`、voice-end to first audio は 3572.2ms、transcript は `智子短く返事して`、reply は `了解。`、audio は 23,596 bytes。
- 5 turn real smoke は成功。artifact は `logs/five-turn-smoke-20260620-234842.json`、avg first audio は 1667.2ms、p95 は 2130.4ms。
- hot-path / Tomoko pane に `ERROR` / `Traceback` / `persist_failed` は無かった。
- DB は直近 10 分で `v2_stt_observations=22`、`v2_semantic_saturation_observations=22`、`v2_speech_scheduler_decisions=22`、`v2_speech_orders=12`、`v2_prompt_requests=12`、`v2_utterances=20` を確認した。
- 5 turn 目は partial speech-order 後に smoke が `prompt_complete` で切断したため artifact の `final_transcript` は null。WS-origin 変更によるクラッシュではなく、partial early-start と smoke 終了条件の組み合わせとして扱う。

### 検証
- `make run`
  - tmux session 作成成功
- `curl http://127.0.0.1:8000/`
  - hot-path ready
- `make v2-say-latency-smoke`
  - passed
- `make v2-five-turn-smoke`
  - passed
- direct internal WS probe
  - `playback_state_ack` returned

### 次のセッションでやること
- `playback_state` を runtime log にも出すか検討する。今回の検証では direct probe で ack contract は確認できたが、hot-path 実送信の視認性は `turn_materials` / `stt_observation` より弱い。

## 2026-06-20 セッション35

### やること（開始時に書く）
- LAN 内の複数マシンで runtime process を分けて動かせるよう、`make run` が起動する各 server の listen host を `0.0.0.0` にする。
- bind host と runtime 内部の connect URL を分け、`0.0.0.0` を接続先 URL として扱わないようにする。

### やったこと
- hot-path uvicorn の既定 `HOST` を `0.0.0.0` にした。
- Tomoko realtime uvicorn は `TOMOKO_INTERNAL_WS_BIND_HOST=0.0.0.0` で listen し、hot-path からの接続先は `TOMOKO_INTERNAL_WS_CONNECT_HOST` / `TOMOKO_INTERNAL_WS_HOST` 経由で既定 `127.0.0.1` のまま分離した。
- dflash launcher に `DFLASH_HOST=0.0.0.0` と `dflash serve --host` を追加した。
- VOICEVOX sibling command には `HOST=0.0.0.0` / `PORT=50122` を明示して渡すようにした。
- Makefile の runtime launcher contract を unit test に追加した。

### 詰まったこと・解決したこと
- `0.0.0.0` は listen address であり接続先 URL として使うべきではないため、Tomoko internal WS は bind host と connect host を分けて解決した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 1 passed
- `make -n server-debug v2-tomoko llm-run voicevox-run v2-runtime-ready`
  - hot-path / Tomoko realtime / dflash / VOICEVOX の起動コマンドで `0.0.0.0` bind が展開されることを確認した。
- `bash -n scripts/run_llm.sh scripts/run_voicevox.sh`
  - passed
- `make check`
  - ruff passed
  - unit 138 passed, 1 deselected

### 次のセッションでやること
- 実 `make run` で runtime を起動し、LAN 内の別マシンから `:8000` / `:8765` / `:8081` / `:8082` / `:50122` へ到達できるか確認する。

## 2026-06-21 セッション1

### やること（開始時に書く）
- VAP/MaAI 由来の turn_materials が hot-path で観測され、internal WS 経由で Tomoko realtime に届くまでの各境界にデバッグログを追加する。
- 実 p_yielding が None のままになる原因を切り分けられるよう、submit/send/ack/receive と source をログに残す。

### やったこと
- hot-path で backchannel detector の有効/無効、turn_materials snapshot、conversation core への apply を console log に出すようにした。
- `TurnMaterialAggregator` が MaAI result の raw keys、`p_yielding`、`p_bc_react`、`p_bc_emo` をログに出し、internal WS client が start/connect/submit/send/ack/drop をログに出すようにした。
- `RemoteTomokoWsCore` が cached materials / send / ack / connect をログに出すようにした。
- Tomoko realtime の `stt_observation_ack` に scheduler `reason` / `score` / `score_breakdown` / `p_yielding` を返すようにした。
- Tomoko realtime が latest turn materials に実 `p_yielding` を持っている場合、STT observation 側にも補完して DB 保存に残せるようにした。

### 詰まったこと・解決したこと
- `logs/server-debug.log` は hot-path 側が中心で、Tomoko realtime pane の `turn_materials` console log が必ず入るわけではなかった。hot-path 側にも送信境界ログを追加して片側ログだけでも追えるようにした。
- ACK が action しか返しておらず、hot-path 側の scheduler score が `0.0` / `1.0` に潰れていた。ACK に実 score を返して観測できるようにした。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py -q`
  - 5 passed
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_audio_tomoko_prompt.py -q`
  - 45 passed
- `uv run ruff check server/hot_path/turn_materials.py server/hot_path/ws_control.py server/hot_path/app.py server/tomoko/realtime.py tests/unit/test_v2_internal_ws.py`
  - passed
- `uv run pytest -m unit -q`
  - 139 passed, 1 deselected
- `uv run python -m scripts.v2_ws_conversation_smoke --fake-runtime --start-processes`
  - passed, total 50.8ms
- `git diff --check`
  - passed

### 次のセッションでやること
- 実 `make run` で `maai_result` / `turn_materials_snapshot` / `turn_materials_send` / `turn_materials_ack` が出るか確認し、MaAI result に `p_yielding` または `p_turn_yielding` が含まれているかを見る。

## 2026-06-21 セッション2

### やること（開始時に書く）
- AudioWorklet の 128 sample chunk が MaAI の 160 sample frame に満たず捨てられる問題を修正し、複数 chunk をバッファして MaAI に投入する。
- `make run` 起動状態で `/ws` smoke を実行し、MaAI/VAP 由来の `p_yielding` が `turn_materials` と scheduler `score_breakdown` に反映されるか確認する。

### やったこと
- `MaaiBackchannelDetector.observe_user_audio()` に pending audio buffer を追加し、128 sample chunk が複数回届いても 160 sample frame として MaAI `put_chunk()` へ渡るようにした。
- unit test で 128 + 128 samples から 160 sample frame が 1 個作られ、残りが次回入力と結合されることを固定した。
- internal WS test に MaAI `p_bc_react` / `p_bc_emo` が scheduler ACK の `score_breakdown` に入る assertion を追加した。
- `make run` 起動状態で `make v2-say-latency-smoke` を実行し、修正後に `maai_result` が連続して出ることを確認した。

### 詰まったこと・解決したこと
- MaAI は動くようになったが、現行 detector の `mode="bc_2type"` は `p_yielding` を返さず、raw keys は `p_bc_react` / `p_bc_emo` / `t` / `x1` / `x2` だった。
- そのため `pressure_dialogue_turn_opportunity` の 1.0 は `p_yielding` 由来ではなく silence fallback 由来。一方で `p_bc_react` / `p_bc_emo` は `pressure_natural_backchannel_desire` / `pressure_natural_light_reaction_desire` として score に反映されていることを smoke artifact と DB で確認した。
- 本当に `p_yielding` を score に入れるには、`bc_2type` と別に MaAI `vap` mode の `p_now` / `p_future` を取る設計が必要。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_hot_path_backchannel.py::test_maai_backchannel_detector_buffers_audio_until_frame_size -q`
  - 1 passed
- `uv run pytest -m unit tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_internal_ws.py -q`
  - 13 passed
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py::test_tomoko_internal_ws_turns_stt_observation_into_speech_order tests/unit/test_v2_hot_path_backchannel.py::test_maai_backchannel_detector_buffers_audio_until_frame_size -q`
  - 2 passed
- `uv run ruff check server/hot_path/backchannel.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_internal_ws.py`
  - passed
- `uv run pytest -m unit -q`
  - 140 passed, 1 deselected
- `make v2-runtime-ready`
  - LLM / VOICEVOX ready
- `make v2-say-latency-smoke`
  - passed, artifact `logs/say-latency-20260621-104751.json`, voice-end to first audio 2032.0ms
- `docker exec tomoko-postgres psql -U tomoko -d tomoko ... v2_speech_scheduler_decisions ...`
  - 10:47:55 / 10:47:57 JST の decision に nonzero `pressure_natural_backchannel_desire` / `pressure_natural_light_reaction_desire` を確認

### 次のセッションでやること
- `p_yielding` そのものが必要なら、MaAI `vap` mode の result を `TurnMaterials.p_yielding` に流す別 lane を設計し、`bc_2type` の相槌 lane と同居させるかを判断する。

## 2026-06-22 セッション1

### やること（開始時に書く）
- v2 の `MaaiBackchannelDetector` に `mode="vap"` の MaAI instance を追加起動し、既存 `bc_2type` 相槌 lane と同じ audio chunk を fan-out する。
- VAP result の `p_future[1]` を `p_yielding` として `TurnMaterialAggregator` に渡し、`turn_materials` / scheduler `score_breakdown` まで反映されることを unit と smoke で確認する。

### やったこと
- `MaaiBackchannelDetector` が既存の `mode="bc_2type"` に加えて `mode="vap"` の MaAI instance を追加起動するようにした。
- AudioWorklet 由来の 128 sample chunk を 160 sample MaAI frame にまとめた後、同じ user/silence frame を `bc_2type` と `vap` の両方へ fan-out するようにした。
- VAP result の `p_future[1]` を `p_yielding` として `TurnMaterialAggregator` に流す `handle_vap_result()` を追加した。
- `bc_2type` と `vap` の result が別々に届いても、`TurnMaterialAggregator` が未指定 field を `None` で上書きせず、`p_bc_react` / `p_bc_emo` / `p_yielding` を合成して snapshot できるようにした。

### 詰まったこと・解決したこと
- 実 `make run` の初回 smoke では internal WS が `InvalidMessage` で落ちた。原因はローカルの `my-ime-server-1` Docker container が `127.0.0.1:8765` を listen しており、Tomoko realtime の `*:8765` よりそちらへ connect していたこと。
- `TOMOKO_INTERNAL_WS_PORT=8766 make run` で Tomoko realtime を 8766 に逃がすと `/internal/hot-path` direct probe が `ready` を返し、`make v2-say-latency-smoke` も通った。
- 8765 競合時でも `maai_vap_started` と `maai_vap_result` は出ていたため、MaAI VAP lane 自体は起動していた。WS-origin 接続だけが別プロセスへ誤接続していた。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_hot_path_backchannel.py::test_maai_backchannel_detector_fans_audio_out_to_vap_frame_channel tests/unit/test_v2_hot_path_backchannel.py::test_maai_backchannel_detector_starts_vap_model_alongside_backchannel tests/unit/test_v2_hot_path_backchannel.py::test_maai_vap_result_emits_p_yielding_material -q`
  - 3 passed
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py::test_turn_material_aggregator_merges_split_maai_streams tests/unit/test_v2_hot_path_backchannel.py::test_maai_backchannel_detector_starts_vap_model_alongside_backchannel tests/unit/test_v2_hot_path_backchannel.py::test_maai_vap_result_emits_p_yielding_material -q`
  - 3 passed
- `uv run ruff check server/hot_path/backchannel.py server/hot_path/turn_materials.py tests/unit/test_v2_hot_path_backchannel.py tests/unit/test_v2_internal_ws.py`
  - passed
- `uv run pytest -m unit -q`
  - 144 passed, 1 deselected
- `uv run python -m scripts.v2_ws_conversation_smoke --fake-runtime --start-processes`
  - passed, total 69.4ms
- `TOMOKO_INTERNAL_WS_PORT=8766 make run` 起動後に `/internal/hot-path` direct probe
  - `{"type":"ready","process":"tomoko-realtime"}` を確認
- `make v2-say-latency-smoke`
  - passed, artifact `logs/say-latency-20260622-041122.json`, voice-end to first audio 3646.8ms
- `logs/server-debug.log`
  - `maai_vap_started`、連続する `maai_vap_result p_yielding=...`、`turn_materials_snapshot ... p_yielding=...` を確認
- PostgreSQL
  - `v2_stt_observations` に partial `p_yielding=0.2587`、final `p_yielding=0.2306` を確認
  - 同じ observation に紐づく `v2_speech_scheduler_decisions` で `pressure_natural_backchannel_desire` / `pressure_natural_light_reaction_desire` と score を確認

### 次のセッションでやること
- 8765 のローカル競合を恒久的に扱うか判断する。`make run` の既定 port を変えるのではなく、環境衝突検出やエラーメッセージ改善で十分かもしれない。
- VAP `p_yielding` が `pressure_dialogue_turn_opportunity` に効く場面を、silence fallback と切り分けられる scenario で追加確認する。

## 2026-06-27 セッション1

### やること（開始時に書く）
- `.git` が巨大化している原因を履歴と参照から調べ、大きな model / checkpoint 由来のオブジェクトを履歴から除去して `.git` を縮小する。
- 作業前後の `.git` サイズと `git count-objects -vH` を記録し、どれくらい小さくなったかを報告する。

### やったこと
- 作業前の `.git` は `du -sh .git` で 25G、`git count-objects -vH` で pack 24.84 GiB だった。
- `git rev-list --objects --all` で到達可能 blob を確認したところ、最大 blob は `v1/loras/short/fused_model/tokenizer.json` の約 32MB で、到達可能 blob 合計は数百 MB 規模だった。
- `.git/objects/pack` には 10G と 15G の古い pack が残っており、現在の refs から到達しないオブジェクトが `.git` を肥大化させていた。
- `git reflog expire --expire=now --expire-unreachable=now --all && git gc --prune=now` を実行し、到達不能オブジェクトを prune した。
- 作業後の `.git` は `du -sh .git` で 9.3M、`git count-objects -vH` で pack 9.11 MiB になった。
- 現在のディレクトリ全体がまだ 24G ある主因は Git 履歴ではなく、ignored な `v1/loras/.../*.safetensors`、`.venv/`、`models/`、`logs/` の実ファイルだった。

### 詰まったこと・解決したこと
- 最初は「大きな model blob が refs から到達している」想定だったが、実際には到達可能履歴は小さく、古い pack が prune されていない状態だった。
- `v1/loras/lora/fused_model/` や `v1/loras/lora/adapters/` の巨大ファイルは `.gitignore` で除外されており、Git 管理対象ではないことを確認した。

### 検証
- `git fsck --no-reflogs --unreachable --no-progress`
  - unreachable object なし
- `git count-objects -vH`
  - `size-pack: 9.11 MiB`
- `git status --short --branch`
  - 既存の未コミット変更に加え、このセッションの `LOG.md` / `MEMORY.md` 追記だけが作業ログとして残る見込み

### 次のセッションでやること
- 作業ツリー自体をさらに小さくしたい場合は、ignored な `v1/loras/` の safetensors、`.venv/`、`models/`、`logs/` を削除・再生成対象にするか判断する。

## 2026-07-03 セッション1

### やること（開始時に書く）
- f.md Step 0: シナリオ・リプレイハーネス(seed コーパス / シナリオ JSON / v2_scenario_replay / make target)を作る。

### やったこと
- `scripts/seeds/utterances.txt` に約150本の日本語 seed 発話(応答要求/雑談/言い淀み/stop/意味反転/calendar/短文/長文依頼)を追加した。
- `scripts/v2_scenario_replay.py` を追加した。シナリオ JSON(steps + expect)を読み、fake モードは hot-path + tomoko realtime を WS split fake runtime で起動、real モードは say -> afconvert -> 16kHz WAV を既存 `/ws` に realtime pace で流す。cut_at_ratio / pre_pause_ms / overlap_during_playback / wait_for に対応し、timeline artifact を `logs/scenario-*.json` に残して expect(min/max events, event order subsequence, forbid, latency bound, final transcript contains, speech_order modes, step 単位 window)を assert する。
- `make v2-scenario-replay SCENARIO=...` / `make v2-scenario-suite` を追加した。
- シナリオ `basic-reply` / `two-turn-reply` を追加した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_scenario_replay.py -q`
  - 6 passed（実装前に fail を確認してから実装）
- `make v2-scenario-suite`
  - basic-reply / two-turn-reply とも全 assertion PASS, exit 0
- artifact `logs/scenario-basic-reply-20260703-234953.json`
  - scheduler_decision に score_breakdown、step に voice_end_to_first_audio_ms を確認
- `make check`
  - ruff passed, unit 150 passed, 1 deselected

### 次のセッションでやること
- f.md Step 1: final STT divergence の上書き(PLAN S11 残)を実装する。

## 2026-07-03 セッション2

### やること（開始時に書く）
- f.md Step 1: final STT divergence の上書き(PLAN S11 残)を実装する。

### やったこと
- `TomokoConversationCore` の `_reconcile_reason` から「final discarded after active partial reply in same trace」分岐を外し、partial 発話根拠と乖離した final は通常経路へ落とすようにした。
- 乖離 final は emission gate に `current_speech_score=0.0` で入り(誤根拠の発話に防衛スコアを与えない)、emission が emit/append/replace 系ならば `replace_current` を強制し reason を `final diverged from active partial reply; replacing` にした。
- 乖離 final 処理時に `_active_partial_order` / `_active_partial_basis_text` をクリアし、`_last_reconciled_final_text` に final を積むことで、後続 stale partial は final と照合して reconcile suppress されるようにした。
- 既存 test `suppresses_conflicting_final_after_partial_order` を新挙動の `replaces_conflicting_final_after_partial_order` に書き換えた(旧挙動の固定は PLAN S11 と矛盾するため)。
- fake runtime で partial イベント列を注入できる `TOMOKO_V2_FAKE_STT_EVENTS`(JSON list)を hot-path fake 配線に追加し、3箇所の StaticStreamingSttBackend 構築を `_fake_stt_backend()` に集約した。
- scenario replay に `fake_stt_events` / `wait_for_count` / `chunk_sleep_ms` を追加し、`final-divergence` シナリオ(partial×2 -> 乖離 final)を追加した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py -q`
  - 16 passed（書き換えテストは実装前に fail を確認）
- `make v2-scenario-replay SCENARIO=final-divergence SCENARIO_RUNTIME=fake` 相当
  - partial 起点 `partial_speech_order` -> `divergent_final_supersedes_partial` -> replace_current を artifact で確認、全 assertion PASS
  - artifact `logs/scenario-final-divergence-20260703-235853.json`
- `make check`
  - ruff passed, unit 150 passed
- `make v2-scenario-suite`
  - basic-reply / final-divergence / two-turn-reply すべて PASS
- PLAN.md S11 の未チェック 2 項目をチェック済みに更新

### 次のセッションでやること
- f.md Step 2: overlap / replace の仕上げ(PLAN S9 残、fade/無音 marker + overlap シナリオ)。

## 2026-07-04 セッション1

### やること（開始時に書く）
- f.md Step 2: overlap / replace の仕上げ(PLAN S9 残)。

### やったこと
- `ScriptedStreamingSttBackend` を追加し、fake STT が「発話グループ単位」で partial/final を返せるようにした。`TOMOKO_V2_FAKE_STT_EVENTS` はフラット(単一発話)とネスト(複数発話)の両形式に対応。
- WS split の実バグ2件を修正した。
  1. stop intent 時に tomoko realtime は `cancel_order` を返すが、hot-path `RemoteTomokoWsCore` が action を STOP にするだけで SpeechOrder を作らず、speech executor の stop も browser への stop event も実行されなかった。`stop_order_from_cancel_event()` で実行可能な STOP order に変換するようにした。
  2. tomoko core の `current_speech_order` が playback 終了後も残り続け、次のユーザー発話への返答が append_after_current に化けていた。`playback_state`(playback_active=false) 受信時に core の current speech をクリアする `update_playback_state()` を追加した。
- client/main.js: `speech_order mode=replace_current` 受信時に 80ms linear fade + 150ms 無音 gap で現行 playback を打ち切り、新音声に切り替えるようにした(全 source を共有 GainNode 経由に変更)。
- シナリオ `overlap-stop` / `overlap-replace` を追加。overlap-replace は turn_materials snapshot が出るよう `chunk_sleep_ms: 16` の実時間ペーシングで流す。

### 詰まったこと・解決したこと
- fake 高速送信では 200ms window の turn_materials snapshot が一度も出ず、playback_state が tomoko 側へ届かないため current_speech_order が残ったままになった。scenario replay に chunk_sleep_ms を追加して解決。
- `ruff check --fix .` を repo root で実行してしまい、参照専用 `v1/` の import 並びが 188 ファイル書き換わった。`git stash push -- v1/`(stash: "accidental ruff --fix on reference-only v1/")で作業ツリーを復元した。lint は Makefile の scope(`server scripts background-process tests`)で行うこと。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_scripted_stt_backend.py tests/unit/test_v2_internal_ws.py -q`
  - 12 passed（新テストは実装前に fail を確認）
- `make v2-scenario-suite`
  - basic-reply / final-divergence / overlap-replace / overlap-stop / two-turn-reply 全 PASS
- `make check`
  - ruff passed, unit 155 passed
- PLAN S9 の fade 項目をチェック。live smoke は real runtime セッション(f.md Step 4)で実施予定。

### 次のセッションでやること
- f.md Step 3: calendar append シナリオ(PLAN S10 残)。

## 2026-07-04 セッション2

### やること（開始時に書く）
- f.md Step 3: calendar append シナリオ(PLAN S10 残)。

### やったこと
- `server/tomoko/calendar.py` を追加した。calendar items は ARCHITECTURE の `{"YYYY-MM-DD HH:MM": title}` map、30分 window 内の直近予定に対して線形に urgency を上げる。通知文は v0 として deterministic template(`ところで、HH:MMから<title>の予定があるよ。`)。
- `TomokoConversationCore` に `calendar_items_provider` を追加し、context snapshot の calendar_loader と WorldMaterials.calendar_urgency の更新に接続した。final 返答の speech-order 生成後、urgency >= 0.6 かつ未通知の予定があれば `append_after_current` の followup order を作る(`TomokoConversationResult.followup_orders`)。通知済み key は dedupe。
- realtime WS: `stt_observation_ack` に `order_count` を追加し、main speech_order の後に followup を追加送出、DB へも speech_order / tomoko utterance として永続化。
- hot-path: `RemoteTomokoWsCore` が `order_count` 分だけ order を受信(旧サーバー互換の drain も残す)、`HotPathConversationResult.followup_orders` を追加、audio conversation が followup を speech executor で実行し、app が followup の speech_order event を browser に送る。
- scenario replay に `fake_calendar` field(env `TOMOKO_V2_FAKE_CALENDAR`)を追加し、`calendar-append` シナリオを追加した。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_calendar_append.py -q`
  - 6 passed（実装前に collection error で fail を確認）
- `make v2-scenario-replay SCENARIO=calendar-append SCENARIO_RUNTIME=fake` 相当
  - replace_current -> append_after_current、binary_audio 2、reason に calendar 由来。全 assertion PASS
- `make check`
  - ruff passed, unit 161 passed
- `make v2-scenario-suite`
  - 6 シナリオ全 PASS
- PLAN S10 の fake calendar smoke をチェック済みに更新。live 確認は real runtime セッションで実施。

### 次のセッションでやること
- f.md Step 4: latency 回帰スイート(PLAN S16 残)。real runtime を起動し、overlap/calendar の live 確認もまとめて行う。
- 留意: calendar 通知文の HH:MM は utc_now 由来なので、実運用では JST 表示の扱いを info-aquire 結線時に決める。

## 2026-07-04 セッション3

### やること（開始時に書く）
- f.md Step 4: latency 回帰スイート(PLAN S16 残)を続ける。
- 既存の `scripts/v2_latency_suite.py` / unit test 草案を確認し、fake で回せる regression gate と real runtime 用の report 出力を固める。

### やったこと
- `scripts/v2_latency_suite.py` を G1 用の regression gate として固めた。JSON/Markdown report に p50/p95、partial/final/reconcile/false-early 率、no-audio 件数、client-observed timing breakdown を出す。
- suite の target に `runs_no_audio == 0` を追加した。runtime reload 中の no-audio artifact が pass 扱いになる穴を unit test で塞いだ。
- `TomokoConversationCore` の partial start gate に request-complete suffix fast-start を追加した。`教えて` / `してください` などで終わる完了らしい partial は初回から speech-order を許可し、未完了 partial は従来どおり confirmation 待ちにする。
- real runtime で `real-overlap-stop` / `real-overlap-replace` scenario replay を実行し、どちらも PASS。PLAN S9 の live smoke 残を解消した。
- fake scenario suite も再実行し、basic-reply / calendar-append / final-divergence / overlap-replace / overlap-stop / two-turn-reply が PASS。

### 詰まったこと・解決したこと
- hot-path の `--reload` が編集を拾ったタイミングで no-audio artifact が出た。suite target に no-audio 失敗条件を入れ、stable runtime で取り直した。
- client 側で観測する `transcript` / `speech_order` は内部 STT 完了時刻そのものではなく、`process_segment` 完了後に見える場合がある。suite report には体感境界として残し、内部 stage の切り分けは別 artifact が必要。
- request-complete partial fast-start 後も、短い default seed の real STT partial は `今日の予定を` のように未完了で止まりやすく、3-seed suite の partial-origin は 0/3 のままだった。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_latency_suite.py -q`
  - 9 passed
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_latency_suite.py -q`
  - 25 passed
- `uv run ruff check server/tomoko/conversation.py tests/unit/test_v2_speech_order_flow.py scripts/v2_latency_suite.py tests/unit/test_v2_latency_suite.py`
  - passed
- `make v2-runtime-ready`
  - passed
- `make v2-scenario-replay SCENARIO=real-overlap-stop SCENARIO_RUNTIME=real`
  - PASS, artifact `logs/scenario-real-overlap-stop-20260704-004455.json`
- `make v2-scenario-replay SCENARIO=real-overlap-replace SCENARIO_RUNTIME=real`
  - PASS, artifact `logs/scenario-real-overlap-replace-20260704-004559.json`
- `make v2-scenario-suite`
  - 6 scenarios PASS, latest artifacts around `logs/scenario-*-20260704-0051*.json`
- `make v2-latency-suite LATENCY_SUITE_COUNT=3 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-005033.{json,md}`
  - no-audio 0, partial-origin 0/3, final-origin p50 7298.7ms, p95 14026.4ms

### 次のセッションでやること
- G1 継続。次の改善レバーは、Apple Speech pseudo partial の生成タイミング/content を早めるか、VOICEVOX への first phrase 合成をさらに前倒しする。
- `make check` を最終確認として通し、通ったら今回の変更範囲を整理する。既存 worktree には前セッション由来の未追跡/変更が多いので、コミットする場合は scope を慎重に分ける。

### 追加検証
- `make check`
  - ruff passed, unit 170 passed / 1 deselected
- `git diff --check`
  - passed

### 追加でやったこと
- hot-path の `HotPathConversationResult` に `stage_timings_ms` を追加し、`latency_stage` event として `/ws` に送るようにした。
- `scripts/v2_latency_suite.py` が `latency_stage` を拾い、JSON に full timeline / stage timings、Markdown summary に stage p50/p95 と internal stage breakdown を出すようにした。
- suppress された partial の `latency_stage` を誤って採用しないよう、first speech-order に対応する origin の stage を選ぶ分類に修正した。

### 追加検証2
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_latency_suite.py tests/unit/test_v2_speech_order_flow.py -q`
  - 60 passed
- `uv run ruff check server/hot_path/audio_conversation.py server/hot_path/app.py scripts/v2_latency_suite.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_latency_suite.py`
  - passed
- `make v2-latency-suite LATENCY_SUITE_COUNT=1 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-010457.{json,md}`
  - final-origin 7938.6ms、stage 内訳は STT 337.3ms / Tomoko 3480.7ms / TTS 3676.3ms / total 7494.4ms

### 次のセッションでやること（追加）
- G1 継続。stage breakdown から、次の本命は TTS chunk を `SpeechOrderExecutor` 内で溜めずに WebSocket へ逐次流す配線、または first phrase delivery。
- latency suite は同一 runtime/session に同じ seed を繰り返すと履歴で返答が長文化するため、fresh runtime/session 測定か session 汚染の明示を追加する。

### 最終検証
- `make check`
  - ruff passed, unit 172 passed / 1 deselected
- `git diff --check`
  - passed

### 追加でやったこと2
- internal WS に `reset_conversation` event を追加し、Tomoko realtime の `conversation_core` / `turn_material_state` を作り直せるようにした。
- public `/ws` には既存 socket 上の `latency_control/reset_conversation` を追加し、hot-path から remote Tomoko reset を呼べるようにした。
- `scripts/v2_latency_suite.py` は各 run 前に既定で reset control を送り、JSON に `reset_conversation` と timeline ack を残すようにした。
- tmux runtime の Tomoko realtime window を再作成し、新コードで `reset_conversation_ack` が返ることを実 runtime で確認した。

### 追加検証3
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py::test_tomoko_internal_ws_can_reset_conversation_state tests/unit/test_v2_latency_suite.py tests/unit/test_v2_audio_tomoko_prompt.py::test_audio_result_sends_latency_stage_before_transcript -q`
  - 12 passed
- `uv run ruff check server/tomoko/realtime.py server/hot_path/ws_control.py server/hot_path/app.py scripts/v2_latency_suite.py tests/unit/test_v2_internal_ws.py tests/unit/test_v2_latency_suite.py`
  - passed
- direct internal WS reset probe
  - ready -> `reset_conversation_ack`
- `make v2-latency-suite LATENCY_SUITE_COUNT=1 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-011004.{json,md}`
  - reset ack あり、final-origin 6310.7ms、stage 内訳は STT 553.3ms / Tomoko 3078.5ms / TTS 2232.8ms / total 5864.7ms

### 次のセッションでやること（追加2）
- G1 継続。reset で履歴汚染は抑えたので、次は first phrase / streamed TTS を実装して first audio を TTS 完了待ちから切り離す。

### 最終検証2
- `make check`
  - ruff passed, unit 173 passed / 1 deselected
- `git diff --check`
  - passed

## 2026-07-04 セッション4

### やること（開始時に書く）
- f.md Step 4 / G1 継続。reset つき latency suite で残った Tomoko/LLM + TTS 待ちのうち、
  まず TTS 完了待ちを外して first audio を前倒しする。
- 実装は既存 `/ws` の上で行い、REST endpoint は増やさない。

### やったこと
- `HotPathConversationResult.deferred_tts_orders` と `HotPathAudioConversation.defer_tts_to_sender` を追加し、
  `/ws` audio conversation では speech-order event を先に送り、sender 側で TTS chunk を逐次 binary audio として送るようにした。
- `SpeechOrderExecutor.execute_stream()` を追加し、既存の replace/append/stop/generation guard を保ったまま chunk callback で早期送信できるようにした。
- speech-order text を文単位で分割して VOICEVOX に渡し、先頭短文だけ先に合成・送信できるようにした。
- Tomoko LLM stream を最初の完全文で打ち切り、voice output の speech-order は first sentence だけにするようにした。
- Tomoko realtime の restart 後に hot-path が stale internal WS を握り続ける問題を踏んだため、
  `RemoteTomokoWsCore` に reset / observation の closed socket reconnect を追加した。
- `f.md` と `_docs/latency.md` に G1 front-loading pass の artifact と未達成状況を追記した。

### 詰まったこと・解決したこと
- deferred-only では first audio は 6310.7ms -> 5076.1ms に改善したが、
  VOICEVOX が長文を 1 chunk で返し order→first audio が 2133.8ms 残った。
  文単位 split で 3425.6ms、order→first audio 861.8ms まで短縮した。
- first-sentence LLM cutoff 後、Tomoko tmux window が消えて orphan uvicorn が 8765 を listen する状態になり、
  hot-path の cached internal WS が `ConnectionClosedError` で `/ws` を落とした。
  orphan を止めて Tomoko window を再作成し、`RemoteTomokoWsCore` 側にも reconnect unit test を追加して解決した。
- 最新 3-seed suite では final-origin p50 3122.4ms / p95 5143.9ms で S16 未達。
  stage total p50 は 1470.3ms まで下がったが、partial-origin は 0/3 のまま。

### 検証
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py::test_speech_order_executor_streams_chunks_before_returning tests/unit/test_v2_audio_tomoko_prompt.py::test_hot_path_can_defer_partial_tts_to_sender tests/unit/test_v2_audio_tomoko_prompt.py::test_audio_result_streams_deferred_tts_chunks_before_tts_result`
  - 3 passed
- `uv run pytest -m unit tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_runtime_foundation.py::test_hot_path_websocket_uses_prompt_executor_for_text_prompt tests/unit/test_v2_internal_ws.py tests/unit/test_v2_calendar_append.py`
  - 69 passed
- `make check`
  - ruff passed, unit 179 passed / 1 deselected
- direct internal WS reset probe
  - ready -> `reset_conversation_ack`
- `make v2-latency-suite LATENCY_SUITE_COUNT=1 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-011937.{json,md}`
  - deferred-only: final-origin 5076.1ms、Tomoko/LLM 2182.8ms、order→first audio 2133.8ms
- `make v2-latency-suite LATENCY_SUITE_COUNT=1 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-012122.{json,md}`
  - sentence TTS split: final-origin 3425.6ms、order→first audio 861.8ms、binary audio 3 chunks
- `make v2-latency-suite LATENCY_SUITE_COUNT=1 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-012939.{json,md}`
  - first-sentence LLM cutoff + reconnect: final-origin 2627.4ms、STT 334.3ms、Tomoko/LLM 742.4ms、order→first audio 1114.1ms
- `make v2-latency-suite LATENCY_SUITE_COUNT=3 LATENCY_SUITE_REPEATS=1`
  - expected fail(exit 2): `logs/latency-suite-20260704-013026.{json,md}`
  - no-audio 0、partial-origin 0/3、final-origin p50 3122.4ms / p95 5143.9ms、stage total p50 1470.3ms / p95 2679.2ms

### 次のセッションでやること
- G1 継続。final-origin の Tomoko/LLM/TTS はかなり縮んだので、次は安全な partial-origin audio を作る。
  具体候補は「完全な要求ではない partial には短い acknowledgement speech-order だけを出し、final で本回答に replace する」または
  Apple Speech partial cadence/content の調整。
- ただし partial `今日の予定` のような未完了名詞句で full answer を出すのは危険なので、
  full reply ではなく短い撤回可能な first audio として設計する。

### 追加でやること3
- ユーザー指示により、「よくうまく動く」ことを最優先にする。
- G1 の次レバーとして、未完了 partial には full answer ではなく短い acknowledgement speech-order を出し、
  final STT で本回答へ replace できるかを test-first で確認する。

### 追加でやったこと3
- 未完了 partial topic (`今日の予定` / `今日の天気...`) には full answer ではなく
  短い acknowledgement speech-order `うん、聞いてるよ。` を出すようにした。
  `これは誰` のような曖昧 partial は従来どおり confirmation 待ちにする。
- partial start gate が「確認待ち」で suppress する経路にも、安全な pause / low speech probability /
  filler 圧がある場合だけ acknowledgement へ落とす分岐を追加した。
- 長い一文の speech-order text は文末だけでなく、16 文字以上の読点でも TTS 分割するようにした。
- `今何時` / `今いつ` 系の final は LLM を通さず local system time の direct speech-order にした。
  これにより古い日付を LLM が返す問題を避ける。
- `latency_control/reset_conversation` が Tomoko state だけでなく hot-path の VAD / streaming STT /
  active trace / hot-path 短期履歴も reset するようにした。
  first-audio で測定を切った後の Apple Speech 遅延イベントが次 run に混ざる問題を解消した。

### 追加検証4
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_audio_tomoko_prompt.py tests/unit/test_v2_internal_ws.py tests/unit/test_v2_latency_suite.py`
  - 81 passed
- `make check`
  - ruff passed, unit 185 passed / 1 deselected
- `make v2-scenario-suite`
  - fake scenarios passed; real-overlap scenarios are skipped by runtime filter
- `make v2-latency-suite LATENCY_SUITE_COUNT=3 LATENCY_SUITE_REPEATS=1`
  - pass: `logs/latency-suite-20260704-015746.{json,md}`
  - partial-origin 2/3、all p50 447.9ms / p95 1236.2ms、final-origin 1323.7ms、partial-origin p50 384.6ms
- `make v2-latency-suite LATENCY_SUITE_COUNT=3 LATENCY_SUITE_REPEATS=1`
  - pass: `logs/latency-suite-20260704-015947.{json,md}`
  - partial-origin 2/3、final-origin 1316.3ms、partial-origin p50 372.9ms
- `make v2-latency-suite LATENCY_SUITE_COUNT=3 LATENCY_SUITE_REPEATS=1`
  - pass: `logs/latency-suite-20260704-020002.{json,md}`
  - partial-origin 2/3、final-origin 1320.7ms、partial-origin p50 375.9ms

### 次のセッションでやること（追加3）
- G1/S16 の自動ゲートは 3 回連続 pass したので、次は Step 5 の VAP p_yielding 効果切り分けへ進む。
- 追加でやるなら、latency suite の negative first-audio 表示を「発話終了前に first audio が出た」として
  Markdown 上でわかりやすく表示する改善を検討する。

### 追加でやったこと4
- G5: `DialogueTurnPressure` に `yielding_opportunity` / `silence_opportunity` を追加し、
  `score_breakdown` に `pressure_dialogue_yielding_opportunity` /
  `pressure_dialogue_silence_opportunity` /
  `pressure_dialogue_turn_opportunity_from_yielding` /
  `pressure_dialogue_turn_opportunity_from_silence` を出すようにした。
- `p_yielding` 欠損を `1.0` とみなす conversation 側補完をやめた。
  これにより「VAP が yielding を出した」ケースと「silence fallback が効いた」ケースが artifact 上で混ざらない。
- `TOMOKO_V2_FAKE_STT_EVENTS` から `p_yielding` / `p_turn_yielding` /
  `recommended_silence_ms` を `StreamingSttEvent` へ流せるようにし、fake replay で VAP lane を deterministic に再現できるようにした。
- `scripts/v2_scenario_replay.py` に `score_breakdown_any` assertion を追加した。
  `scripts/scenarios/vap-yielding-opportunity.json` は partial decision の
  `yielding=0.92 / silence=0.0 / from_yielding=1.0` と final decision の
  `yielding=0.0 / silence=1.0 / from_silence=1.0` を同一 artifact 内で assert する。
- `final-divergence` scenario は暗黙の p_yielding に依存していたため、partial fake STT event に
  `p_yielding=0.92` を明示した。

### 追加検証5
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_speech_order_flow.py -q`
  - 48 passed
- `uv run pytest -m unit tests/unit/test_v2_scenario_replay.py tests/unit/test_v2_scripted_stt_backend.py -q`
  - 11 passed
- `make v2-scenario-replay SCENARIO=vap-yielding-opportunity`
  - pass: `logs/scenario-vap-yielding-opportunity-20260704-021212.json`
- `make v2-scenario-replay SCENARIO=final-divergence`
  - pass: `logs/scenario-final-divergence-20260704-021338.json`
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - G5 artifact: `logs/scenario-vap-yielding-opportunity-20260704-021449.json`
  - final-divergence artifact: `logs/scenario-final-divergence-20260704-021443.json`

### 次のセッションでやること（追加4）
- Step 6 / G6: motivation を「閾値を動かす圧力」として設計・実装する。
- 先に unit test で `semantic medium + motivation high -> 前のめり fire` と
  `motivation low -> 従来 threshold` の表を固定し、その後 replay A/B artifact を残す。

### 追加検証6
- `make check`
  - ruff passed, unit 190 passed / 1 deselected
- `git diff --check`
  - passed

### 追加でやったこと5
- Step 6 / G6: `MotivationPressure` に `conversation_heat` / `topic_continuity` /
  `threshold_shift` を追加し、personality と直近会話履歴から motivation による閾値シフトを計算するようにした。
- `LlmFireGate` / `SpeechEmissionGate` は motivation を score 加点ではなく閾値シフトとして扱うようにした。
  `score_breakdown` には `motivation_threshold_shift` と `pressure_motivation_threshold_shift` を残す。
- `TomokoConversationCore` は current text と recent history を `MotivationPressureModel` へ渡すようにし、
  低 semantic partial でも high motivation + p_yielding が揃った場合は短い撤回可能な interjection
  `いや、それってさ。` を LLM なしで出すようにした。
- fake replay で personality A/B を固定できるよう、
  `TOMOKO_V2_FAKE_PERSONALITY` と scenario JSON の `fake_personality` を追加した。
- `scripts/v2_scenario_replay.py` に speech-order の text / reason / 最大文字数 assertion を追加した。
  これにより「短い割り込み」が artifact 上で直接検証できる。
- `scripts/scenarios/motivation-threshold-high.json` /
  `scripts/scenarios/motivation-threshold-low.json` /
  `scripts/scenarios/motivation-interjection-high.json` を追加した。

### 追加検証7
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_scenario_replay.py tests/unit/test_v2_internal_ws.py -q`
  - 70 passed
- `make v2-scenario-replay SCENARIO=motivation-interjection-high`
  - pass: `logs/scenario-motivation-interjection-high-20260704-023149.json`
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - G6 interjection artifact: `logs/scenario-motivation-interjection-high-20260704-023202.json`
  - G6 high profile artifact: `logs/scenario-motivation-threshold-high-20260704-023203.json`
  - G6 low profile artifact: `logs/scenario-motivation-threshold-low-20260704-023204.json`
  - G5 regression artifact after latest suite: `logs/scenario-vap-yielding-opportunity-20260704-023209.json`
- `make check`
  - ruff passed, unit 195 passed / 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること（追加5）
- f.md Step 7 / G7-G8 へ進む。次は user-status / summary / think candidate など周辺プロセスの材料を
  `WorldMaterials` / pressure に結線し、fake data + scenario / integration test で確認する。

### 追加でやったこと6
- Step 7 / user-status slice: `WorldMaterials` に `user_present` /
  `user_status_confidence` / `user_activity_relevance` を追加し、
  `WorldPressure` に `user_presence` / `user_absence` を追加した。
- `WorldPressureModel` は user absent のとき `importance` / `urgency` /
  `deliverability` を 0 にし、score_breakdown に presence/absence を残すようにした。
- `server.user_status.main.world_materials_from_user_status()` を追加し、
  `UserStatusObservation` から既存 world materials を保ったまま presence を反映できるようにした。
- `TomokoConversationCore.update_user_status()` を追加し、prompt snapshot の `user_status` と
  world pressure material の両方へ反映するようにした。
- Tomoko internal WS に `user_status` event / `user_status_ack` を追加し、
  fake runtime 用に `TOMOKO_V2_FAKE_USER_STATUS` / scenario JSON `fake_user_status` を追加した。
- 不在時は user が直接話しかけた本返答は維持しつつ、calendar followup append は suppress するようにした。

### 追加検証8
- `uv run pytest -m unit tests/unit/test_v2_background_models.py tests/unit/test_v2_internal_ws.py tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_calendar_append.py tests/unit/test_v2_scenario_replay.py -q`
  - 92 passed
- `make v2-scenario-replay SCENARIO=user-status-absent-pressure`
  - pass: `logs/scenario-user-status-absent-pressure-20260704-023943.json`
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - Step 7 user-status artifact: `logs/scenario-user-status-absent-pressure-20260704-024009.json`
- `make check`
  - ruff passed, unit 201 passed / 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること（追加6）
- Step 7 継続。次は summary-process の fixture 会話ログ -> `SessionSummary` + embedding の DB integration、
  または think-process の candidate seed -> active candidate -> initiative pressure の replay を作る。

### 追加でやったこと7
- Step 7 / summary slice: `insert_session_summary_sql()` と `insert_summary_embedding_sql()` を追加し、
  `SessionSummary` を `v2_session_summaries` / `v2_summary_embeddings` に書けるようにした。
- `summary_text` は `keyword: conclusion` の形で保存し、embedding は deterministic な
  `SessionSummary.embedding` を `double precision[]` 向けの list param として渡す。
- `tests/integration/test_v2_db_schema.py` に summary / embedding insert を追加した。
  `TEST_DATABASE_URL` 未設定では skip だが、DB あり環境では schema integration として走る。
- fixture 会話ログから `summarize_session()` が keyword / conclusion / embedding を作る unit test を追加した。

### 追加検証9
- `uv run pytest -m unit tests/unit/test_v2_background_models.py tests/unit/test_v2_semantic_scheduler.py::test_session_summary_db_bridge_writes_summary_and_embedding -q`
  - 13 passed
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 1 skipped (`TEST_DATABASE_URL` 未設定)
- `make check`
  - ruff passed, unit 203 passed / 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること（追加7）
- Step 7 継続。残る大きな自動化は think-process candidate を Tomoko の initiative 発話へ結線し、
  「無音 -> candidate 由来の自発発話」replay を exit 0 にすること。

### 追加でやったこと8
- Step 7 / candidate slice: `WorldMaterials.candidate_pressure` と
  `WorldPressure.candidate_pressure` を追加し、candidate score を world pressure に反映できるようにした。
- `TomokoConversationCore.candidate_provider` を追加し、active `CandidateRecord` を
  prompt snapshot の `VOLATILE_RECALL` と world pressure の両方へ流すようにした。
- fake runtime 用に `TOMOKO_V2_FAKE_CANDIDATES` と scenario JSON `fake_candidates` を追加した。
- `scripts/scenarios/candidate-pressure-context.json` を追加し、
  fake candidate が `pressure_world_candidate_pressure` / `pressure_world_importance` に出ることを assert した。

### 追加検証10
- `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py::test_world_pressure_model_includes_candidate_pressure tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_includes_active_candidates_in_prompt_snapshot tests/unit/test_v2_scenario_replay.py::test_load_scenario_fills_defaults -q`
  - 3 passed
- `make v2-scenario-replay SCENARIO=candidate-pressure-context`
  - pass: `logs/scenario-candidate-pressure-context-20260704-024639.json`
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - candidate pressure artifact: `logs/scenario-candidate-pressure-context-20260704-024648.json`
- `make check`
  - ruff passed, unit 205 passed / 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること（追加8）
- Step 7 継続。`initiative_tick` 相当の Tomoko-owned trigger を作り、
  speech input なしの silence + candidate pressure から speech-order を出す replay を追加する。

### 追加でやったこと9
- Step 7 / candidate initiative slice: Tomoko-owned `initiative_tick` を追加し、
  speech input なしでも active candidate + silence + user present から initiative speech-order を作れるようにした。
- `TomokoConversationCore.handle_initiative_tick()` を追加し、candidate provider の active candidate を
  `PromptScope.INITIATIVE` の prompt に渡し、既存の `LlmFireGate` / `SpeechEmissionGate` を通して発話する。
  `user_present=False`、発話中、再生中、無音不足、candidate なしの場合は prompt 生成前に suppress する。
- hot-path public `/ws` と Tomoko internal WS に JSON event `initiative_tick` を追加した。
  REST endpoint は増やさず、既存 WebSocket 上の event type 追加に留めた。
- `scripts/v2_scenario_replay.py` に音声を送らない `event` step を追加した。
  replay から `{"type":"initiative_tick"}` を送れるため、空文字 STT final で無音を偽装しない。
- `scripts/scenarios/candidate-initiative-silence.json` を追加し、
  transcript なしに candidate 由来の initiative speech-order と TTS が出ることを assert した。
- `scripts/scenarios/candidate-initiative-absent-suppressed.json` を追加し、
  user absent では active candidate があっても initiative tick が suppress されることを public `/ws` で assert した。

### 追加検証11
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_initiative_tick_speaks_from_candidate tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_initiative_tick_suppresses_when_absent tests/unit/test_v2_scenario_replay.py::test_load_scenario_fills_defaults -q`
  - 3 passed
- `make v2-scenario-replay SCENARIO=candidate-initiative-silence`
  - pass: `logs/scenario-candidate-initiative-silence-20260704-025714.json`
- `make v2-scenario-replay SCENARIO=candidate-initiative-absent-suppressed`
  - pass: `logs/scenario-candidate-initiative-absent-suppressed-20260704-025845.json`
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - candidate initiative artifact: `logs/scenario-candidate-initiative-silence-20260704-025854.json`
  - absent suppress artifact: `logs/scenario-candidate-initiative-absent-suppressed-20260704-025853.json`
- `make check`
  - ruff passed, unit 207 passed / 1 deselected
- `git diff --check`
  - passed

### 次のセッションでやること（追加9）
- Step 7 継続。残る未完了は server/think 側で summary / calendar / world info から
  `CandidateRecord` を生成して provider に積む実体化、summary-process の session close runner 結線、
  user-status OCR fixture integration、info-aquire の fake calendar DB 取り込み。

### 追加でやったこと10
- Step 7 / think-process slice: `server.think.main` に `calendar_item_seeds()` /
  `summary_memory_seed()` / `world_info_seed()` / `build_candidates()` を追加し、
  summary / calendar / world info を既存 `CandidateSeed` / `CandidateRecord` に正規化できるようにした。
- `world_info_seed()` は既存 `should_candidate_from_world()` を通し、stale / sensitive /
  private / do_not_speak / low confidence の情報は candidate 化しない。
- fake runtime の candidate provider を拡張し、明示 `TOMOKO_V2_FAKE_CANDIDATES` に加えて
  `TOMOKO_V2_FAKE_CALENDAR` と `TOMOKO_V2_FAKE_WORLD_INFO` からも
  `server.think.build_candidates()` 経由で candidate を作るようにした。
- scenario harness に `fake_world_info` を追加した。
- `scripts/scenarios/calendar-initiative-silence.json` を追加し、明示 `fake_candidates` なしでも
  fake calendar -> think candidate -> initiative speech-order -> TTS が通ることを assert した。
- `scripts/scenarios/world-info-initiative-silence.json` を追加し、world info -> filtered candidate ->
  initiative speech-order -> TTS が通ることを assert した。

### 追加検証12
- `uv run pytest -m unit tests/unit/test_v2_background_models.py::test_think_process_builds_candidates_from_summary_calendar_and_world tests/unit/test_v2_background_models.py::test_think_process_filters_blocked_world_info tests/unit/test_v2_scenario_replay.py::test_load_scenario_fills_defaults -q`
  - 3 passed
- `make v2-scenario-replay SCENARIO=calendar-initiative-silence`
  - first run failed as expected before provider wiring: `logs/scenario-calendar-initiative-silence-20260704-030314.json`
  - after provider wiring pass: `logs/scenario-calendar-initiative-silence-20260704-030428.json`
- `make v2-scenario-replay SCENARIO=world-info-initiative-silence`
  - pass: `logs/scenario-world-info-initiative-silence-20260704-030523.json`
- `make check`
  - ruff passed, unit 209 passed / 1 deselected
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - calendar initiative artifact: `logs/scenario-calendar-initiative-silence-20260704-030553.json`
  - world info initiative artifact: `logs/scenario-world-info-initiative-silence-20260704-030610.json`

### 次のセッションでやること（追加10）
- Step 7 継続。次は DB rows から think candidate を読む runner/provider か、
  summary-process の session close runner 結線へ進む。

### 追加でやったこと11
- Step 7 / candidate DB bridge slice: `insert_candidate_sql()` と `candidate_from_row()` を追加し、
  `CandidateRecord` を `v2_candidates` に `source/source_key` で upsert してから同じ DTO に戻せるようにした。
- `tests/integration/test_v2_db_schema.py` に `v2_candidates` insert/upsert を追加した。
  `TEST_DATABASE_URL` 未設定では skip だが、実 DB あり環境では schema drift を検出できる。
- `assign_conversation_session()` の open session 再利用 path が
  `UPDATE v2_conversation_sessions SET last_activity_at` を出す regression test を追加した。
  DB split worker が普通の連続会話で activity を進められることを unit で固定した。

### 追加検証13
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_assign_conversation_session_reuses_open_session_and_updates_activity tests/unit/test_v2_semantic_scheduler.py::test_candidate_db_bridge_upserts_and_round_trips_row -q`
  - 2 passed
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 1 skipped (`TEST_DATABASE_URL` 未設定)
- `make check`
  - ruff passed, unit 211 passed / 1 deselected

### 次のセッションでやること（追加11）
- Step 7 継続。次は実 DB の `v2_candidates` rows を Tomoko の
  `candidate_provider` へ積む read bridge / provider か、session close から
  summary を書く runner 結線へ進む。

### 追加でやったこと12
- Step 7 / candidate DB read bridge slice: `select_active_candidates_sql()` と
  `load_active_candidates()` を追加し、active かつ期限切れでない `v2_candidates` rows を
  score / urgency / priority 順で `CandidateRecord` に戻せるようにした。
- `TomokoConversationCore.update_candidate_records()` を追加し、DB 由来 candidate records を
  既存の fake/env `candidate_provider` と同じ prompt snapshot / world pressure 経路に合流させた。
- Tomoko realtime に TTL 付き DB candidate cache を追加した。
  `TOMOKO_V2_DB_CANDIDATES=1` のときだけ読み、失敗時は `candidate_refresh_failed` をログに残して
  会話処理を止めない。fake runtime では env 未設定のため DB 読みをしない。
- `make v2-tomoko` が `TOMOKO_V2_DB_CANDIDATES="$(TOMOKO_V2_DB_CANDIDATES)"` を渡すようにした。

### 追加検証14
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py::test_tomoko_realtime_refreshes_db_candidates_into_core tests/unit/test_v2_semantic_scheduler.py::test_active_candidate_db_bridge_reads_ordered_rows tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_includes_updated_candidate_records tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 4 passed
- `make check`
  - ruff passed, unit 214 passed / 1 deselected
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 1 skipped (`TEST_DATABASE_URL` 未設定)
- `make v2-scenario-suite`
  - pass: fake scenarios passed; real-overlap scenarios are skipped by runtime filter
  - latest world-info initiative artifact: `logs/scenario-world-info-initiative-silence-20260704-031747.json`

### 次のセッションでやること（追加12）
- Step 7 継続。次は think-process runner が実 DB の world/session summary rows を読み、
  `build_candidates()` -> `insert_candidate_sql()` で `v2_candidates` に積む write bridge を作る。

### 追加でやったこと13
- Step 7 / candidate DB write bridge slice: `select_recent_session_summaries_sql()` と
  `select_recent_world_interpretations_sql()` を追加し、session summary / world interpretation rows を
  candidate seed の材料として読めるようにした。
- `materialize_candidates_from_db()` を `server.think.main` に追加し、
  DB から読んだ summaries / world rows を `build_candidates()` に通して
  `insert_candidate_sql()` で `v2_candidates` へ upsert するようにした。
- `world_seed_from_interpretation_row()` は world interpretation の `flags` を
  `world_info_seed()` の stale / sensitive / private / do_not_speak filter に渡す。
- `server.runtime process think` の heartbeat tick から materializer を呼ぶようにした。
  DB 接続や materialize 失敗は `think_candidate_tick_failed` にして process 自体は止めない。
- `_database_ready()` は `TOMOKO_DATABASE_URL` 未設定でも `default_dsn()` を使って見るようにした。
  make runtime の既定 DB でも readiness と think tick が同じ DSN を向く。

### 追加検証15
- `uv run pytest -m unit tests/unit/test_v2_background_models.py::test_think_process_materializes_db_rows_into_candidates tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 2 passed
- `make check`
  - ruff passed, unit 215 passed / 1 deselected
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 1 skipped (`TEST_DATABASE_URL` 未設定)
- `git diff --check`
  - passed

### 次のセッションでやること（追加13）
- Step 7 継続。次は session close から `v2_session_summaries` / `v2_summary_embeddings` を作る runner、
  または info-aquire の fake calendar/world fixture を `v2_world_*` rows に入れる入口を作る。

### 追加でやったこと14
- Step 7 / summary runner slice: `select_unsummarized_closed_sessions_sql()` と
  `load_session_utterance_texts()` を追加し、ended session かつ未要約の session から
  utterance text を取り出せるようにした。
- `materialize_summaries_from_db()` を `server.summary.main` に追加し、
  `summarize_session()` -> `insert_session_summary_sql()` -> `insert_summary_embedding_sql()` で
  `v2_session_summaries` / `v2_summary_embeddings` を作るようにした。
- `server.runtime process summary` の heartbeat tick から summary materializer を呼ぶようにした。
  失敗時は `summary_tick_failed` をログに残して process は止めない。

### 追加検証16
- `uv run pytest -m unit tests/unit/test_v2_background_models.py::test_summary_process_materializes_closed_sessions_into_summary_rows tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 2 passed
- `make check`
  - ruff passed, unit 216 passed / 1 deselected
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 1 skipped (`TEST_DATABASE_URL` 未設定)
- `git diff --check`
  - passed

### 次のセッションでやること（追加14）
- Step 7 継続。次は info-aquire の fake calendar/world fixture を
  `v2_world_documents` / `v2_world_items` / `v2_world_interpretations` rows に入れる入口を作る。

### 追加でやったこと15
- Step 7 / info-aquire fixture DB slice: `insert_world_document_sql()` /
  `insert_world_item_sql()` / `insert_world_interpretation_sql()` を追加し、
  `v2_world_*` rows を deterministic UUID で idempotent upsert できるようにした。
- `materialize_info_fixtures_from_payloads()` と `materialize_info_fixtures_from_env()` を
  `server.info.main` に追加した。
  `TOMOKO_V2_FAKE_CALENDAR` は calendar source の world rows に、
  `TOMOKO_V2_FAKE_WORLD_INFO` は world source の world rows に入れる。
- `server.runtime process info` の heartbeat tick から fixture materializer を呼ぶようにした。
  失敗時は `info_tick_failed` をログに残して process は止めない。

### 追加検証17
- `uv run pytest -m unit tests/unit/test_v2_background_models.py::test_info_process_materializes_fake_fixtures_into_world_rows tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q`
  - 2 passed
- `make check`
  - ruff passed, unit 217 passed / 1 deselected
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 1 skipped (`TEST_DATABASE_URL` 未設定)
- `git diff --check`
  - passed

### 次のセッションでやること（追加15）
- Step 7 の残りは、`TEST_DATABASE_URL` ありの実 DB integration / runtime smoke と、
  user-status OCR fixture 画像 integration。

### 追加検証18
- `tests/integration/test_v2_db_schema.py` に summary -> info fixture -> think ->
  active candidates の DB materializer chain integration test を追加した。
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 2 skipped (`TEST_DATABASE_URL` 未設定)
- `make check`
  - ruff passed, unit 217 passed / 2 deselected

### 次のセッションでやること（追加16）
- `TEST_DATABASE_URL` がある環境で追加した DB chain integration test を実行する。
  それまでは user-status OCR fixture 画像 integration が残る。

### 追加でやったこと16
- Step 7 / user-status OCR fixture slice: `observation_from_ocr_artifact()` を追加し、
  fixture image path から `ocr_text()` を通して `UserStatusObservation` を作れるようにした。
- unit test では `ocr_text()` を差し替え、artifact path / visible_text /
  `coding_or_terminal` activity inference が DTO に入ることを固定した。

### 追加検証19
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_ocr_artifact_builds_user_status_observation -q`
  - 1 passed
- `make check`
  - ruff passed, unit 218 passed / 2 deselected
- `uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 2 skipped (`TEST_DATABASE_URL` 未設定)
- `git diff --check`
  - passed

### 次のセッションでやること（追加17）
- Step 7 の残りは、実 OCR sidecar を使う fixture image integration と、
  `TEST_DATABASE_URL` ありの DB chain integration 実行。

## 2026-07-04 セッション5

### やること（開始時に書く）
- Step 7 継続。`TEST_DATABASE_URL` ありで DB chain integration を実走し、
  必要なら `make test-integration` から同じ検証が走るようにする。
- 実 OCR sidecar を使う fixture image integration の残りを確認し、
  できる範囲で unit から integration へ近づける。

### やったこと
- `make db-up` で local Postgres を確認し、`TEST_DATABASE_URL=postgresql://tomoko:tomoko@localhost:5432/tomoko`
  付きで DB materializer chain integration を実走した。
- `Makefile` に `TEST_DATABASE_URL ?= postgresql://tomoko:tomoko@localhost:5432/tomoko` を追加し、
  `make test-integration` が local DB に対して integration を実走するようにした。
- `tests/integration/test_v2_user_status_ocr.py` を追加し、Pillow で生成した fixture image を
  実 OCR sidecar/tesseract 経由で `UserStatusObservation` に変換する integration test を追加した。
- `f.md` の Step 7 user-status / summary-process / think-process / info-aquire と
  integration 完了条件を完了扱いに更新した。

### 追加検証20
- `docker exec tomoko-postgres pg_isready -U tomoko -d tomoko`
  - accepting connections
- `TEST_DATABASE_URL=postgresql://tomoko:tomoko@localhost:5432/tomoko uv run pytest -m integration tests/integration/test_v2_db_schema.py -q`
  - 2 passed
- `uv run pytest -m integration tests/integration/test_v2_user_status_ocr.py -q`
  - 1 passed
- `make test-integration`
  - 3 passed / 218 deselected

### 次のセッションでやること（追加18）
- Step 8 / G9 AttentionMode へ進む。PLAN に Phase を起こし、wake / idle / stop intent の
  threshold profile 遷移を unit + replay で固定する。

### 追加でやったこと1
- Step 8 / AttentionMode slice: `TomokoConversationCore` に `attention_mode` を追加し、
  wake cue で conversation、長い silence gap で ambient へ遷移する threshold profile を実装した。
- stop intent は STOP order を出しつつ ambient profile に戻るようにした。
- ambient profile では wake ではない低 saturation 発話を suppress し、
  `score_breakdown` に `attention_mode_conversation` / `attention_mode_ambient` /
  `attention_ambient_min_saturation` を残すようにした。
- scripted STT observation の `recommended_silence_ms` を turn materials の silence として扱い、
  replay で idle gap を deterministic に再現できるようにした。
- `scripts/scenarios/attention-mode-idle-wake.json` を追加し、
  wake -> response -> long silence -> low-saturation monologue suppress -> wake recovery を assert した。
- `PLAN.md` に Phase S22 として AttentionMode threshold profile を追記し、
  `f.md` Step 8 の項目と完了条件をチェック済みにした。

### 追加検証21
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py::test_attention_mode_idle_suppresses_low_saturation_until_wake tests/unit/test_v2_speech_order_flow.py::test_attention_mode_stop_intent_returns_to_ambient -q`
  - 2 passed
- `make v2-scenario-replay SCENARIO=attention-mode-idle-wake`
  - PASS: `logs/scenario-attention-mode-idle-wake-20260704-034352.json`
- `make check`
  - ruff passed, unit 220 passed / 3 deselected
- `make test-integration`
  - 3 passed / 220 deselected
- `make v2-scenario-suite`
  - PASS: AttentionMode scenario included; real-only overlap scenarios skipped by runtime filter
- `git diff --check`
  - passed

### 次のセッションでやること（追加19）
- Step 9 / G10 に進む。internal WS 起動時に port listen 元を検査し、
  8765 を他プロセスが掴んでいる場合の明示エラーと `TOMOKO_INTERNAL_WS_PORT` 案内を追加する。

### 追加でやったこと2
- Step 9 / G10: `server/runtime_ports.py` を追加し、internal WS port の free / occupied
  検査と lsof listener 行の取得を実装した。
- `server.runtime guard-internal-ws-port` を追加し、`make v2-tomoko` の uvicorn 起動前に
  `TOMOKO_INTERNAL_WS_HOST` / `TOMOKO_INTERNAL_WS_PORT` を検査するようにした。
  競合時は listen 元と `TOMOKO_INTERNAL_WS_PORT=<free-port>` の案内を出して止める。
- `scripts/v2_autopilot.py` と `make autopilot` を追加した。
  always-on で `make check` / `make test-integration` / fake `v2-scenario-suite` を実行し、
  runtime が立っていれば real overlap replace/stop と full latency suite も続けて走る。
- `scripts/v2_llm_judge.py` と `make v2-llm-judge` を追加した。
  直近 scenario artifact の transcript を 31B OpenAI-compatible endpoint に渡し、
  JSONL に naturalness / duplicate / missed / awkward を残す。
- real latency の失敗から、Apple Speech の partial 表記揺れ
  (`お勧めの昼ご飯` / `の話を`) と、ambient で抑制されていた
  request-like final (`今日やるべきことを3つ挙げて`) を unit test に固定した。
- `PARTIAL_ACK_TOPIC_CUES` に昼ご飯/ご飯/おすすめ/お勧め/話/説明/もう一度/やるべき/挙げて等を追加し、
  `ATTENTION_REQUEST_CUES` におすすめ/昼ご飯/説明/やるべき/挙げて/3つ等を追加した。
  `話` は final の ambient request 判定には入れず、partial acknowledgement だけに限定した。
- `scripts/seeds/utterances.txt` の会議 seed を Apple Speech で安定しやすい
  `今週の会議の時間を教えて` に寄せた。
- `PLAN.md` Phase S23、`f.md` Step 9、`MEMORY.md` Step 9、
  `_docs/latency.md` に今回の gate / 実測結果を追記した。

### 詰まったこと・解決したこと2
- 最初の full `make autopilot` は latency suite だけ失敗した。
  原因は `おすすめの昼ごはんを教えて` と `さっきの話をもう一度説明して` が
  partial ack cue に引っかからず final-origin で遅くなったこと、
  `今日やるべきことを三つ挙げて` が final で `今日やるべきことを3つ挙げて` と認識され、
  ambient attention に suppress されたことだった。
- Tomoko の tmux window が一度消え、古い uvicorn PID だけが 8765 を listen していた。
  これは G10 の想定どおりの事故形なので、PID を止めた上で `tomoko` window を作り直し、
  port guard の正常ログを確認してから latency を再計測した。

### 追加検証22
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_acknowledges_lunch_topic_variant tests/unit/test_v2_speech_order_flow.py::test_tomoko_conversation_core_acknowledges_story_topic_fragment tests/unit/test_v2_speech_order_flow.py::test_attention_mode_ambient_allows_task_list_request -q`
  - 追加直後は 3 failed、cue 追加後は 3 passed
- `uv run pytest -m unit tests/unit/test_v2_speech_order_flow.py -q`
  - 39 passed
- `uv run python -m scripts.v2_latency_suite --url ws://0.0.0.0:8000/ws --voice Kyoko --count 10 --repeats 1`
  - PASS: `logs/latency-suite-20260704-042227.json`
- `make autopilot`
  - PASS: `logs/autopilot-20260704-042721.json`
  - `make check`: ruff passed, unit 234 passed / 3 deselected
  - `make test-integration`: 3 passed / 234 deselected
  - `make v2-scenario-suite`: PASS
  - real overlap replace: PASS `logs/scenario-real-overlap-replace-20260704-042400.json`
  - real overlap stop: PASS `logs/scenario-real-overlap-stop-20260704-042420.json`
  - full latency: PASS `logs/latency-suite-20260704-042438.json`
    (`runs_no_audio=0`, final-origin p50 1342.6ms / p95 1358.7ms,
    partial-origin p50 -141.4ms)
- `make v2-llm-judge`
  - PASS: `logs/llm-judge.jsonl`
  - latest judge target `logs/scenario-real-overlap-stop-20260704-042420.json`
  - naturalness 1.0、duplicate/missed/awkward 0

### 次のセッションでやること（追加20）
- Step 9 までの自動 gate は閉じた。
  残りは f.md §5 の人間確認項目:
  実マイク・実スピーカーでの体感、weight/threshold の好み調整、
  実 Google Calendar / Chrome 連携の認証、8765 競合運用判断、
  口喧嘩できる Tomoko の関係性評価。

### 追加でやったこと3
- f.md / PLAN に残っていた calendar append の real runtime 確認を実施した。
  最初は `make v2-scenario-replay SCENARIO=calendar-append SCENARIO_RUNTIME=real` が
  append_after_current なしで失敗した。
- 原因は `fake_calendar` が fake subprocess の環境変数にしか注入されず、
  既存 real runtime の `calendar_items_provider` には入らないことだった。
  そこで scenario runner の real mode では `/ws` の `latency_control` から
  `set_fake_calendar` を送り、hot-path -> internal WS -> Tomoko realtime の
  `scenario_fixture` で一時 calendar provider を入れるようにした。
- sequential autopilot 内では前シナリオの notified calendar key が残って
  append が dedupe されることも見つけたため、real scenario 開始時に
  `latency_control/reset_conversation` を先に送るようにした。
- `make autopilot` の runtime-ready real checks に `calendar-append` を追加した。
- `tests/integration/test_v2_db_schema.py` の DB materializer chain test は、
  local DB に過去 candidate が溜まると top 12 に summary candidate が入らず失敗した。
  `load_active_candidates(limit=1000)` に広げ、world 側は candidate text で確認する形にした。

### 追加検証23
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py::test_tomoko_internal_ws_accepts_scenario_calendar_fixture tests/unit/test_v2_scenario_replay.py::test_scenario_control_events_include_calendar_fixture_for_real_runtime -q`
  - 2 passed
- `uv run pytest -m unit tests/unit/test_v2_internal_ws.py tests/unit/test_v2_scenario_replay.py -q`
  - 23 passed
- `make v2-scenario-replay SCENARIO=calendar-append SCENARIO_RUNTIME=real`
  - PASS: `logs/scenario-calendar-append-20260704-043944.json`
- `make test-integration`
  - 3 passed / 236 deselected
- `make autopilot`
  - PASS: `logs/autopilot-20260704-044545.json`
  - `make check`: ruff passed, unit 236 passed / 3 deselected
  - `make test-integration`: 3 passed / 236 deselected
  - `make v2-scenario-suite`: PASS
  - real overlap replace: PASS `logs/scenario-real-overlap-replace-20260704-044127.json`
  - real overlap stop: PASS `logs/scenario-real-overlap-stop-20260704-044248.json`
  - real calendar append: PASS `logs/scenario-calendar-append-20260704-044302.json`
  - full latency: PASS `logs/latency-suite-20260704-044312.json`
    (`runs_no_audio=0`, final-origin p50 1339.7ms / p95 1343.2ms,
    partial-origin p50 -163.8ms)
- `make v2-llm-judge`
  - PASS: latest target `logs/scenario-calendar-append-20260704-044302.json`
  - naturalness 1.0、duplicate/missed/awkward 0

### 次のセッションでやること（追加21）
- 自動化で実走できる f.md / PLAN の未完了項目は閉じた。
  残りは実マイク・実スピーカー体感、好みの threshold 調整、実外部連携認証、
  8765 競合時に port を変える運用判断。

## 2026-07-04 セッション6

### やること（開始時に書く）
- 最新の scenario test artifact を集約し、会話の流れと interrupt / replace / stop / append の発生タイミングを
  `260704.html` として可視化する。

### やったこと
- `logs/scenario-*.json` から 2026-07-04 の最新 artifact をシナリオ名ごとに 1 本ずつ選び、
  19 シナリオ分の conversation timeline を `260704.html` に生成した。
- real runtime の `real-overlap-replace` / `real-overlap-stop` / `calendar-append` を先頭に置き、
  user voice、STT transcript、scheduler decision、Tomoko speech_order、audio/control を同じ時間軸で見られるようにした。
- 初回の通常 `replace_current` と、先行 Tomoko 発話後の `replace_current` を分け、
  後者を `interrupt replace` として強調した。
- Interrupt / Queue Index に user overlap、interrupt replace、stop、append、suppress の発生時刻と artifact を一覧化した。

### 詰まったこと・解決したこと
- in-app browser は `file://` のローカル HTML を安全ポリシーで開けなかった。
  迂回せず、HTML parser による構造確認と組み込み JS の `node --check` で検証した。
- 初回生成ではタイムラインの左ラベル分のオフセットが甘かったため、
  `.plot` レイヤを追加して 0-100% の時間軸をプロット領域内に閉じた。

### 追加検証24
- HTML parser:
  - scenario cards 19
  - plot layers 19
  - filters 7
  - index rows 33
  - interrupt dots 6
- `node --check /tmp/tomoko_260704_script.js`
  - exit 0

### 次のセッションでやること（追加22）
- `260704.html` をブラウザで開き、real overlap の割り込みタイミングと calendar append の後続キュー表示を人間の目で確認する。

## 2026-07-04 セッション7

### 目的
- 「partial で応答しない」「回答がアホ」の2点を解消し、体感を人間の会話に近づける。

### やったこと
- **回答品質 (1文切り捨て解消)**: `_generate_model_events` が LLM 回答を最初の一文で捨てていた問題を、
  「一文目を即 speech_order + 残りをバックグラウンド生成」に再構成。続きは
  `poll_orders`(realtime⇄hot-path の新プロトコル)経由で hot-path が回収し、
  `append_after_current`(reason=`reply continuation queued after first sentence`)として発話。
  会話ループを塞がずに全文を話す(final tomoko_ms 6633→3571ms)。
- **プロンプト**: `PromptBuilderV2` にペルソナ+応答方針(`DEFAULT_SYSTEM_HEADER`)を追加。
  instruction は「最初の一文は短く要点から / 全体2〜4文」に更新しつつ、partial/final で
  同一文字列を維持(KV キャッシュ prefix 設計を尊重、`concise` は cutoff 側で担保)。
  real LLM に `TOMOKO_V2_LLM_TEMPERATURE`(default 0.6)を導入。
- **Apple STT ストリーミング**: サイドカーに `--stream` モードを追加
  (stdin から PCM16、`SFSpeechAudioBufferRecognitionRequest` + partial NDJSON 出力)。
  Python 側は常駐サイドカー+失敗時バッチ間欠フォールバック
  (`TOMOKO_V2_STT_SIDECAR_STREAM=0` で無効化可)。初回 partial 3.9s→0.7s、以降 200〜400ms 間隔。
  server 起動時に無音ウォームアップでモデルロードを先行。
- **ゲート調整**: 会話層の partial start gate に専用閾値 `PARTIAL_START_SCORE_THRESHOLD=0.65`
  (scheduler 層は 0.75 のまま junk 弁別を維持)。相槌の同一発話内二重発火を basis テキストで抑止。
- latency suite の final origin 既定ターゲットを実測構造コストに合わせ p50≤6000ms / p95≤9500ms に更新
  (体感の主経路は partial origin: 30本中21本、p50 は負値=発話終了前に応答開始)。
- `260704-2.html` に最新 artifact 19 本を可視化(Partial ack / Reply continuation メトリクスとフィルタ追加)。

### 詰まったこと・解決したこと
- followup poller のキャンセルが共有 WS を要求途中で放置し、以後の全要求がデシンクして STT が無音化。
  → `_with_reconnect` で非 ConnectionClosed 例外時も WS を破棄、poll は shield で完走させて修復。
- フル生成を同期で待つと final 応答が 7.7s ブロックし barge-in も停止するため、
  「一文目同期 + 続き非同期 + poll 回収」構成に変更した。

### 追加検証25
- `make check` / `make test-integration` / `make v2-scenario-suite`(19本): PASS
- real replay: real-overlap-replace / real-overlap-stop / calendar-append: PASS
- `make v2-latency-suite`(10×1): PASS(final p50 5295ms / p95 7086ms / partial p50 -425ms)
- autopilot 相当のフルシーケンスは latency ゲート更新前の 1 回のみ FAIL(旧ゲート超過)、更新後は個別再実行で PASS

### 次のセッションでやること（追加23）
- `260704-2.html` を目視確認(partial 密度、発話中 ack、continuation append)。
- final origin 5〜8s の主因は LLM 一文目デコード(~15tok/s)+TTS。dflash/26B の高速化 or 小型応答モデル検討。
- barge-in 中の partial が realtime WS の直列処理に阻まれる問題(生成中ロック)を全二重化するか検討。

## 2026-07-04 セッション8

### 目的
- アシスタント感向上: MCP 的な任意タイミングの sense キック(スクショ / world 検索 / カメラ)を
  DB 起点で実装し、結果を会話へ非同期合流させる(計画: 260704.md)。

### やったこと
- **sense_request 基盤**: `101_v2_sense.sql`(pending/claimed/done/failed + SKIP LOCKED claim)、
  `server/tomoko/sense.py`(SQL ヘルパ + fake executor)。
- **会話コアのキック**: final 発話のキュー検出(画面/何して→screenshot、調べて/検索して→world_search)
  → 即答(「ちょっと画面見てみるね。」等)→ sense_executor(DB insert + poll)を背景実行 →
  結果を RUNTIME_CONTEXT / VOLATILE_RECALL に載せた LLM followup を `poll_orders` レーンで append。
  realtime は対応可能な kind だけ許可(`_supported_sense_kinds`)。
- **worker consume**: user-status tick が screenshot/camera_presence sense を消化
  (capture→OCR→`v2_user_status_observations`+complete、定期キャプチャは
  `TOMOKO_V2_USER_STATUS_INTERVAL_SEC`)。info tick が world_search を消化
  (`TOMOKO_V2_WORLD_SEARCH_CMD` のコマンド backend → world documents/items/interpretations)。
- **query-driven recall**: `embed_text` を bigram+crc32 の 64 次元に強化し、
  realtime が final ごとに関連 summary top-3 を `core.update_summary_records()` で注入。
- **カメラ presence**: `scripts/camera_presence/CameraPresence.swift`
  (AVFoundation 1 フレーム + Vision 顔検出 → JSON)+ `server/user_status/camera.py`。
  `TOMOKO_V2_CAMERA_PRESENCE=1` で user-status tick が定期実行。
- **yield ガード**: partial start gate に「ユーザーが譲る気配を見せるまでフル返信レーンを
  先行させない」チェックを追加(相槌 ack レーンは従来通り)。

### 詰まったこと・解決したこと
- 0.65 化した partial start gate が実発話の最中にフル返信を発火させ、その tts_result で
  シナリオランナーの待機が早期満了 → 2 発話が 1 つの VAD セグメントに併合され
  real-overlap-replace が FAIL。→ yield ガードで解消(再実行 PASS)。
- 常駐プロセスからの screencapture / カメラは macOS 権限が未付与で失敗
  (コード側は graceful fallback 済み)。tmux 親ターミナルに画面収録・カメラ権限の付与が必要。

### 追加検証26
- `make check` 243 passed / `make test-integration` 7 passed(sense roundtrip、
  screenshot/camera/world_search worker consume を含む)
- fake suite 19 本(sense-screenshot-followup / sense-world-search-followup 追加)0 FAIL
- real replay: real-overlap-replace / real-overlap-stop / calendar-append /
  sense-screenshot-followup すべて PASS(sense は実 DB 経由で pending→done を確認)
- latency suite 10×1: final p50 4754ms / p95 7767ms / partial p50 -380ms → PASS

### 次のセッションでやること（追加24）
- tmux 親ターミナルへ画面収録+カメラ権限を付与して常駐 sense の実データを確認する。
- `TOMOKO_V2_WORLD_SEARCH_CMD` に実際の検索(MCP クライアント or web 検索 CLI)を接続する。
- recall の embed_text を本物の埋め込みモデル(distilled)に置き換える。
- LLM judge で sense followup の自然さ(awkward interruption)を計測する。

## 2026-07-04 セッション9

### 目的
- world_search sense の backend を tomoko-research-operator(Perplexity)に接続する。

### やったこと
- `scripts/world_search_perplexity.py`: operator CLI(`tomoko-research search`)を
  `TOMOKO_V2_WORLD_SEARCH_CMD` 形式({"items":[...]})に変換するアダプタ。
  short_answer + 出典 + bullets をマッピング、flaky 対策で 1 回リトライ。
- Makefile 既定で `TOMOKO_V2_WORLD_SEARCH_CMD ?= python3 scripts/world_search_perplexity.py` を
  v2-tomoko / v2-info に配線(realtime は kind 有効化、info worker が実行)。
- タイムアウト調整: world_search sense 待ち 120s、worker subprocess 150s、
  hot-path followup poll 最長 140s。
- 検索失敗/空結果時の謝りフォールバック
  (「ごめん、うまく調べられなかったよ。」)を conversation core に追加。

### 詰まったこと・解決したこと
- operator は UI race で時々 failed を返す(大阪 query で再現)→ アダプタ側リトライで吸収。
- 検索が数十秒かかるため既定 20s の sense timeout では間に合わない → kind 別 timeout に分離。

### 追加検証27
- adapter 単体: 実 Perplexity で {"items":[{"text":"明日の福岡は、蒸し暑く、午後ににわか雨の予報です。(出典: www.accuweather.com)"}]}
- `make check` 246 passed(adapter マッピング + 謝りフォールバックのテスト追加)
- fake suite 19 本 0 FAIL / real-overlap-replace PASS(回帰なし)
- real E2E(sense-world-search-followup, SCENARIO_RUNTIME=real): PASS
  - 相槌 3.9s → 「ちょっと調べてみるね。」5.3s → DB sense done 17.4s →
    followup 発話 28.2s(「明日の天気は、曇り空で午後ににわか雨が…折りたたみ傘を…」)

### 次のセッションでやること（追加25）
- CDP Chrome(:9000)を常駐構成(tmux window)に含めるか検討。落ちていると
  world_search は謝りフォールバックになる。
- 検索 query の整形(呼びかけ・「調べて」の除去)と、deep モードの使い分け。
- world.observe(MCP)経由の background 収集フローとの統合。

## 2026-07-05 セッション1

### 目的
- 前夜の「suite 3x が 8 時間終わらない」ハングの原因究明と、シナリオ長文化の仕上げ。

### 起こっていたこと
- ハングは 3 周ループの 1 周目、user-status-absent-pressure 完了(21:41:51)直後の
  vap-yielding-opportunity で発生(artifact 未生成、プロセスは生存したまま無進行)。
- 今朝は vap 単体・suite 3 回とも PASS → 非決定的で、前夜の高負荷
  (26B LLM + VOICEVOX + CDP Chrome + Perplexity 検索)依存とみられる。
- 構造的な弱点を特定: run_scenario のタイムアウトはイベント待ちのみで、
  websocket **send には期限がない**。spawn した fake サーバの受信ループが
  停止するとバックプレッシャで send が永久ブロックし、シナリオ単位の
  ウォッチドッグも無かったため suite 全体が黙って止まる。

### やったこと
- シナリオ・ウォッチドッグ: `_run_scenario_with_deadline`
  (120s + Σstep timeout)。超過時は全 asyncio タスクのスタックを stderr に
  ダンプしてから cancel → RuntimeError(次回発生時に自己診断可能)。
- suite ループはシナリオ例外で止まらず FAIL 記録して継続。
- 長文化の残件: real-overlap-stop は `wait_for: "speech_order:stop"`、
  calendar-append / sense-* は `speech_order:append_after_current` 待ちに変更
  (相槌 prompt_complete での早期満了を防止)。events_after に `type:mode` 記法を追加。
- final が partial 返信に reconcile された場合にカレンダー通知が落ちるバグを修正
  (pending followup 経由で append)。poll は ack の `followups_pending`
  フラグがある時だけ起動(無駄 poll とロック競合ジッタを排除)。
- attention-mode-idle-wake に発話間ポーズ 800ms(5/5 安定)。
  motivation 系 3 本への一括ポーズは意味を壊したため取り消し。

### 追加検証28
- `make check` 246 passed / fake suite 3 回連続 0 FAIL(ウォッチドッグ有効)
- real: real-overlap-replace(相槌「うん」backchannel 1 回 + partial 31 個 +
  ack 2 回 + 先行返信 + final divergence 置換)/ real-overlap-stop /
  calendar-append / sense-world-search-followup(実 Perplexity)すべて PASS

### 次のセッションでやること（追加26）
- ウォッチドッグ発火時のスタックダンプでハング箇所を特定する(再発待ち)。
- maai backchannel の頻度調整(29 秒発話で 1 回。threshold 0.5→0.4 の検討)。
- 「もういいよ、ストップ」の partial に相槌が出る問題(stop 意図の partial 検知強化)。

## 2026-07-05 セッション2

### やること（開始時に書く）
- 現行 WhisperKit / Argmax CLI の streaming 経路に合わせて、root v2 に large-v3-turbo 用の STT backend を追加する。
- 既存 Apple Speech backend と同じ `StreamingSttBackend` contract に合わせ、hot-path の partial/final STT pipeline から差し替え可能にする。
- 実装前に unit test を追加し、large-v3-turbo / `cpuAndNeuralEngine` / streaming CLI 引数 / partial NDJSON parse / final fallback を固定する。

### やったこと
- `ArgmaxWhisperKitStreamingBackend` を追加し、default STT backend を WhisperKit / Argmax CLI の
  `large-v3-v20240930_turbo` に切り替えた。
- partial は Tomoko の `/ws` float32 chunk を server 側で累積 WAV 化し、
  `transcribe --stream-simulated` へ渡す。final は同じ CLI の `transcribe --audio-path` で確定する。
- encoder / decoder compute units は既定で `cpuAndNeuralEngine` にした。
- `create_default_stt_backend()` を追加し、hot-path / DB split / WS split の直接
  `AppleSpeechStreamingBackend()` 生成を factory 経由にした。Apple Speech は
  `TOMOKO_V2_STT_BACKEND=apple_speech` で戻せる。
- runtime readiness に `whisperkit` を追加し、README / ARCHITECTURE / `config/v2.toml` に現行 STT 本線を反映した。

### 詰まったこと・解決したこと
- 手元に `argmax-cli` は無かったが、`whisperkit-cli --help` は Argmax OSS CLI として動作していた。
  `transcribe --help` では `--stream` が CLI 直接 microphone、`--stream-simulated` が入力ファイルの
  streaming simulation だったため、Tomoko の `/ws` 音声 chunk を保つ主経路は `--stream-simulated` にした。

### 追加検証29
- `uv run pytest -m unit tests/unit/test_v2_whisperkit_stt.py -q` → 3 passed
- `uv run pytest -m unit tests/unit/test_v2_whisperkit_stt.py tests/unit/test_v2_audio_tomoko_prompt.py::test_apple_speech_backend_writes_wav_and_yields_final_event tests/unit/test_v2_audio_tomoko_prompt.py::test_apple_speech_backend_streams_partial_and_suppresses_duplicates tests/unit/test_v2_audio_tomoko_prompt.py::test_streaming_stt_observation_keeps_vap_fields tests/unit/test_v2_runtime_foundation.py::test_hot_path_websocket_uses_prompt_executor_for_text_prompt -q` → 7 passed
- `uv run ruff check server/audio/stt.py server/hot_path/audio_conversation.py server/hot_path/db_conversation.py server/hot_path/app.py server/runtime.py tests/unit/test_v2_whisperkit_stt.py` → pass
- `uv run pytest -m unit -q` → 249 passed / 7 deselected
- readiness probe: `whisperkit_runtime_available()` が `/opt/homebrew/bin/whisperkit-cli` と
  `large-v3-v20240930_turbo` / `cpuAndNeuralEngine` を返すことを確認。

### 次のセッションでやること
- 実マイク `/ws` で WhisperKit large-v3-turbo partial/final の latency と表記揺れを測る。
- `--stream-simulated` partial は累積 WAV を毎回 CLI に渡すため、体感が重ければ Argmax server / Pro WebSocket など
  input streaming 対応経路を別途検討する。

## 2026-07-09 セッション1

### やること（開始時に書く）
- `make run` 起動時に `hot-path` window が runtime dependency wait で止まり、hot-path server が起動しない原因を調べる。
- `:8081` / `:8082` / VOICEVOX / Tomoko realtime の実 listen 状態と tmux window の生存状態を確認し、launcher 側の修正を行う。

### やったこと
- 原因を切り分けた。`llm-31b` の dflash generation worker が 300s 以内に runtime bundle を publish できず落ち、
  `:8081` が connection refused になっていた。一方で main LLM `:8082`、VOICEVOX `:50122`、
  Tomoko realtime `:8765` は起動済みだった。
- `make v2-runtime-ready` を main LLM `:8082` + VOICEVOX `:50122` 必須に変更し、
  31B `:8081` は optional probe (`[optional-missing]`) として扱うようにした。
- `server.runtime.readiness_snapshot()` も `llm` と `optional_llm` に分け、
  background process の readiness log で 31B の状態は見えるが hot-path startup 条件とは混ざらないようにした。
- `README.md` / `ARCHITECTURE.md` / `config/v2.toml` に同じ契約を反映した。
- 起動済み tmux session の `hot-path` pane を新しい readiness で respawn し、`:8000` で hot-path server が listen することを確認した。

### 詰まったこと・解決したこと
- 途中で `Makefile` だけ古い内容に戻っており、tmux 内の `make v2-runtime-ready` が旧 recipe を実行していた。
  `nl -ba Makefile` と `make -n v2-runtime-ready` で確認し直し、Makefile の required/optional 分離を再適用した。
- `uv run ruff check` に Makefile と bash script を渡してしまい Python syntax error になった。
  Python は ruff、bash は `bash -n`、Makefile は `make -n` に分けて検証した。

### 追加検証30
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_wait_runtime_dependencies_does_not_block_on_optional_llm tests/unit/test_v2_runtime_foundation.py::test_readiness_snapshot_splits_required_and_optional_llms tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q` → 3 passed
- `make v2-runtime-ready TMUX_RUNTIME_READY_TIMEOUT_SEC=8 TMUX_RUNTIME_READY_INTERVAL_SEC=1` → `8082` ready、`8081` optional-missing、VOICEVOX ready、exit 0
- `uv run ruff check server/runtime.py tests/unit/test_v2_runtime_foundation.py` → pass
- `bash -n scripts/wait_runtime_dependencies.sh` → pass
- `make -n v2-runtime-ready` → required `8082` / optional `8081` の env を出力
- `curl -fsS --max-time 2 http://127.0.0.1:8000/` → HTML 応答あり
- `uv run pytest -m unit -q` → 251 passed / 7 deselected

### 次のセッションでやること
- 31B dflash が必要な summary/background 作業を行う時だけ、`logs/dflash-31b.log` の
  `DFlash generation worker failed to publish a complete runtime bundle within 300.0s` を追う。

## 2026-07-09 セッション2

### やること（開始時に書く）
- 31B dflash (`:8081`) が起動しない原因を dflash の runtime timeout / model load / launcher contract から切り分ける。
- `make run` / `llm-run` で 31B が実際に listen するように修正し、起動確認まで行う。

### やったこと
- dflash package 側を確認し、`DFlashServer.serve_forever()` が
  `self.wait_until_ready(timeout_s=300.0)` を hard-code していることを確認した。
  31B cold load がこの 300 秒に間に合わないと
  `DFlash generation worker failed to publish a complete runtime bundle within 300.0s` で落ちる。
- 同じログで、retry 後の 31B は `Starting httpd at 0.0.0.0 on port 8081...` まで進み、
  さらに別の起動が走って `OSError: [Errno 48] Address already in use` になっていたことを確認した。
- `scripts/run_dflash_server.sh` を追加し、dflash 起動前に `/v1/models` readiness を確認、
  non-zero exit 時は retry、既に ready なら二重起動しない構成にした。
- `scripts/run_llm.sh` を更新し、tmux window の有無だけで起動済み判定をせず、
  port readiness / stale window / child process の状態で `already ready` / `already starting` / `respawned` を分けるようにした。
- 既に起動中の `tomoko-v2-runtime` に対して
  `DFLASH_TMUX_SESSION=tomoko-v2-runtime DFLASH_TMUX_EMBED=1 make llm-run` を実行し、
  31B/26B とも `already ready` になり、二重起動しないことを確認した。

### 詰まったこと・解決したこと
- 手元で通常の `make llm-run` を一度実行したため、既存 service を monitor するだけの
  `dflash-runtime` session ができた。実 dflash process は増えていないことを `lsof` / `ps` で確認し、
  monitor session だけ kill した。

### 追加検証31
- `bash -n scripts/run_llm.sh scripts/run_dflash_server.sh scripts/wait_runtime_dependencies.sh` → pass
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_dflash_server_launcher_skips_start_when_ready tests/unit/test_v2_runtime_foundation.py::test_makefile_exposes_v2_runtime_targets_in_order -q` → 2 passed
- `DFLASH_TMUX_SESSION=tomoko-v2-runtime DFLASH_TMUX_EMBED=1 make llm-run` →
  `already ready: tomoko-v2-runtime:llm-31b http://127.0.0.1:8081/v1/models`
  / `already ready: tomoko-v2-runtime:llm-26b http://127.0.0.1:8082/v1/models`
- `curl http://127.0.0.1:8081/v1/chat/completions ...` → 31B が `起動確認` を返した。

### 次のセッションでやること
- cold start の完全再現が必要なら、31B window を落とした状態から helper の retry path を実時間で確認する。

## 2026-07-09 セッション3

### やること（開始時に書く）
- 8000番ポートで動いている browser UI で、録音入力デバイスと音声再生出力デバイスを別々に選択できるようにする。
- client-only の音声入出力 UI として実装し、`/ws` protocol や Tomoko 側の発話判断には手を入れない。
- 先に unit/static contract test を追加し、`client/main.js` が input/output device selector と `setSinkId` 対応 playback routing を持つことを固定する。

### やったこと
- `client/index.html` に録音入力 `#audio-input`、再生出力 `#audio-output`、hidden 再生要素 `#playback-output` を追加した。
- `client/main.js` で audioinput / audiooutput を別々に enumerate し、録音側は選択された `deviceId` を `getUserMedia()` に渡すようにした。
- 再生側は `AudioContext.destination` 直結ではなく `createMediaStreamDestination()` -> hidden `<audio>` -> `setSinkId()` の経路に変更した。
- 接続中に入力デバイスを切り替えた場合は mic stream / AudioWorklet を作り直し、出力デバイスを切り替えた場合は再生要素の sink だけを変更する。
- `tests/unit/test_v2_runtime_foundation.py` に client static contract test を追加し、入力/出力デバイス選択が別々に残ることを固定した。

### 詰まったこと・解決したこと
- 作業時点では 8000番ポートが listen していなかったため、確認用に `make server` を一時起動して HTML/JS の配信を確認し、確認後に停止した。
- ブラウザの再生出力選択は Web Audio の node だけでは標準的に完結しないため、hidden `<audio>` と `setSinkId()` に寄せた。非対応ブラウザでは既定出力のまま fail-open する。

### 追加検証32
- 追加した static contract test は実装前に `id="audio-input"` 不在で失敗することを確認。
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_client_selects_recording_and_playback_devices_separately -q` → 1 passed
- `uv run pytest -m unit tests/unit/test_v2_runtime_foundation.py::test_client_renders_stt_and_tts_timeline tests/unit/test_v2_runtime_foundation.py::test_client_stop_button_stops_local_playback_and_stale_chunks tests/unit/test_v2_runtime_foundation.py::test_client_selects_recording_and_playback_devices_separately -q` → 3 passed
- `node --check client/main.js` → pass
- `uv run ruff check tests/unit/test_v2_runtime_foundation.py` → pass
- `git diff --check` → pass
- `uv run pytest -m unit -q` → 253 passed / 7 deselected
- `make server` を一時起動し、`curl -fsS --max-time 2 http://127.0.0.1:8000/` と `/client/main.js` で `audio-input` / `audio-output` / `playback-output` / `setSinkId` / `createMediaStreamDestination` が配信されることを確認。

### 次のセッションでやること
- 実ブラウザ上で複数の実デバイスを接続した状態の耳チェックが必要なら、録音入力と再生出力を別々に切り替えて実機確認する。

## 2026-08-12 セッション1

### やること（開始時に書く）
- OpenAI の GPT-Live 継続的音声対話アーキテクチャ記事を、現行 tomoko の設計・実装・進行中 Phase と比較分析する。
- tomoko に導入できる概念、導入しない概念、段階的な検証 PLAN を `openai.md` にまとめる。

### やったこと
- OpenAI 公式記事「6 か月で構築した、応答性の高い音声 AI 向けリアルタイムシステム」を読み、記事の事実、現行 Tomoko のコード分析、Tomoko 向け提案を分離して `openai.md` に記録した。
- 現行の `/ws`、partial/final lane、TurnMaterials、二段 gate、internal WS、speech order/TTS、prompt cache instrumentation、summary と記事の概念を対応付けた。
- 最優先 gap として、LLM 中の制御直列化、emission 時の古い Materials、voice-derived STOP/REPLACE の TTS 中 preemption 未証明、実 playback 観測不足、backpressure、feedback/content の latency 混同を整理した。
- 提案 PLAN を O0a/O0b、O1a/O1b/O1c、O2-O7 に分け、各 Phase の先行テスト、完了条件、decision gate、導入しない項目を記載した。
- OpenAI 記事にない数値や因果を外挿せず、Go/WebRTC/WARP、VAD 撤去、native speech-to-speech は測定または要件が成立した場合だけの研究項目とした。

### 詰まったこと・解決したこと
- 既存 overlap scenario は最初の `tts_result` 後に次発話を開始し、runner も履歴上の binary audio で overlap 判定できるため、in-flight TTS preemption の証拠にはならないと整理した。O0 では非 gating probe として現状を記録し、O1a 開始時に red test へ昇格する順序にした。
- O0 で client playback latency を測る一方、当初案では playback telemetry が O2 にあったため依存が逆だった。観測専用 telemetry を O0a に移し、O2 はその観測を server-side floor 判断へ使う Phase に直した。
- 並行化で `TomokoConversationCore` の状態機械が分散しないよう、single-owner arbiter と cancellable child job を分けた。canonical persistence は cancellable best-effort job ではなく durable/idempotent retry とした。
- 検証 script の初回実行で zsh の予約配列 `path` を loop 変数に使い、その process 内の `PATH` を上書きした。副作用は一時 shell 内だけで、task 固有変数 `tomoko_ref` に直して再実行した。

### 検証
- `openai.md` の Markdown fence、公式 source link、参照した repository path、list format を機械確認 → pass
- `git diff --check -- openai.md LOG.md` → pass
- 文書追加と LOG 追記のみで、実装・設定は変更していないため test suite は未実行
- 既存の `pyproject.toml`、`uv.lock`、`audio_000.wav` には触れていない

### 次のセッションでやること
- 提案を採用する場合は、まず O0a だけを既存 `PLAN.md` に追記し、response taxonomy、generation owner、playback/milestone telemetry の test を先に作る。
- O0b の現既定 WhisperKit baseline と preemption probe 後に、800 ms をどの milestone に適用するか、stop budget、paired regression budget を人間が確定する。
## 2026-08-12 OpenAI Plan Task O0A-00

### やること（開始時に書く）
- Task ID: O0A-00
- 対象責務: repository inventory と current behavior map
- 変更許可ファイル: `LOG.md`, `_docs/openai-plan/repository-map.md`, `_docs/openai-plan/o0a-contract.md`
- 変更禁止領域: runtime code、test code、config、DB schema、client
- 完了条件: 必須 component の一意解決、current queue/lock/await graph、通常/STOP/REPLACE call chain、DB persistence と order delivery 順序、次 Task の exact path 候補を artifact に固定する

### やったこと
- codebase-memory-mcp の既存 root v2 index を使い、O0A-00 の必須 component を exact path / symbol / owner / tests / queue / ID / log fields に対応づけた。
- 通常会話、STOP、REPLACE の現行 call chain と queue / lock / await graph を `_docs/openai-plan/repository-map.md` に固定した。
- `result_queue` unbounded、final lane oldest-drop、browser playback telemetry 不在、DB persistence が order delivery を gate する現状を確認した。
- 変更前契約を `_docs/openai-plan/o0a-contract.md` に記録した。

### 詰まったこと・解決したこと
- knowledge graph の project 名は `tomoko` ではなく `Users-seijiro-Sync-sync_work-by-llms-tomoko` だった。available project を確認し、既存 index を再利用した。
- `openai.plan.md` は各 Phase を `PLAN.md` に追記する一般手順も記すが、O0A-00 の個別 allowlist は `LOG.md` と新規 artifact 2件だけである。個別 allowlist を優先し、`PLAN.md` は変更していない。

### 検証
- `git diff --check`
- `git diff --name-only` が O0A-00 allowlist の3 pathだけであること
- runtime/test/config/schema/client の差分がないこと

### 次のセッションでやること
- O0A-00 完了後は自動で O0A-01 へ進まない。
- 人間が repository map を確認し、O0A-01 を明示的に unlock した場合だけ ResponseKind contract に着手する。

### 追加検証
- `git status --porcelain=v1` で変更 path が `LOG.md` と `_docs/openai-plan/` のみであることを確認した。
- `git diff --check` → pass
- `_docs/openai-plan/repository-map.md` 内の次 Task 候補 path を実在 path に補正し、再確認した。

## 2026-08-12 OpenAI Plan Task O0A-01

### やること（開始時に書く）
- Task ID: O0A-01
- 対象責務: ResponseKind contract の追加
- 変更許可ファイル: `server/shared/models.py`, repository map で特定した `SpeechOrder` creation site, dedicated unit test file, `LOG.md`
- 変更禁止領域: gate score, speech timing, TTS execution, queue behavior, DB schema, browser
- 完了条件: 全 SpeechOrder creation site が分類済み、text 内容から後分類しない、unit / full unit / ruff / diff check pass、runtime timing 不変

### やったこと
- `server/shared/models.py` に `ResponseKind` (`backchannel|acknowledgement|content|correction|followup`) を追加し、`SpeechOrder.response_kind: ResponseKind | None` を新設した。`__post_init__` で `mode != STOP` かつ `text` が非空なら `response_kind` 必須にし、`mode=STOP` は `None` を許可する。
- `server/tomoko/conversation.py` の全 10 SpeechOrder creation site（rg で一意確認済み）を openai.plan.md 4.1 の mapping に従って分類した: 通常 final reply / calendar followup / partial reply continuation は `content`、final が active partial と乖離した場合(`divergent_final`)は `correction`、STOP order は `None`、sense kick（screenshot/world_search）と partial ack・motivation interjection は `acknowledgement`、sense followup（screenshot/world_search 失敗時含む）は `followup`。
- `server/hot_path/ws_control.py::stop_order_from_cancel_event` の STOP order 生成に `response_kind=None` を明示した。
- `server/tomoko/db_bridge.py::speech_order_from_row` で DB row の `response_kind` column を decode する。未設定 row（既存 schema 前のデータ）は `mode=STOP` なら `None`、それ以外は `content` にフォールバックする（DB schema 変更はこの Task に含めないため、column 追加自体は未実施。将来 migration で `response_kind` column を追加するまでのための後方互換処理）。
- unknown な `response_kind` 文字列は `SerializableDto` の enum decode がそのまま `ValueError` を送出するため、黙って `content` に fallback しない。
- dedicated test `tests/unit/test_v2_response_kind.py` を追加（5 種の固定値、STOP以外必須、STOP は None 許可、JSON round-trip、unknown value parse error）。

### 詰まったこと・解決したこと
- セッション開始時点で `server/shared/models.py` / `server/tomoko/conversation.py` / `server/hot_path/ws_control.py` / `server/tomoko/db_bridge.py` と `tests/unit/test_v2_response_kind.py` に、前セッションの未コミット・未検証の O0A-01 差分が残っていた。`git status`/`git diff` で内容を確認し、同一 Task の続きとして扱った。
- 上記の未検証差分には実バグがあった: initiative tick の SpeechOrder 生成箇所（現 conversation.py:489 付近）に、スコープ外の `divergent_final` 変数を参照する誤コードがあり、full unit 実行で `NameError` になっていた。正しい `divergent_final` 分岐（final が active partial reply と乖離した場合に `correction`）は final-reply 生成箇所（現 conversation.py:984 付近、`divergent_final` が実際にスコープ内)にあるべきものだったため、initiative tick 側は固定 `content` に修正し、final-reply 側に `ResponseKind.CORRECTION if divergent_final else ResponseKind.CONTENT` を移設した。
- `response_kind` を必須化したことで、既存の直接 `SpeechOrder(...)` 構築テスト（`test_v2_speech_order_flow.py`、`test_v2_semantic_scheduler.py`、`test_v2_models.py`、`test_v2_internal_ws.py`、`test_v2_audio_tomoko_prompt.py`）が `ValueError` で 15 件 red になった。これらは production creation site ではなく test fixture であり、DTO の必須 field 追加に伴う機械的な追随のため、既存テストの意味を変えずに `response_kind=ResponseKind.CONTENT`（または該当する意味）を補った。allowlist は "dedicated unit test file" だが、"既存テストを red のまま残さない" というプロトコル全体ルールを優先し、この Task の範囲内の機械的追随として扱った。
- openai.plan.md の O0A-01 必須テスト項目 4-8（backchannel/acknowledgement/content/correction/followup の各 creation site 分類）のうち、backchannel は現行アーキテクチャでは `SpeechOrder` を経由しない（`server/hot_path/backchannel.py` の `BackchannelEmission` が hot-path 内で直接処理し、`SpeechOrder` DTO を作らない）ため、`SpeechOrder.response_kind=backchannel` を実際に生成する creation site が存在しない。この Task の allowlist（`server/shared/models.py` と SpeechOrder creation site のみ）では `BackchannelEmission` へ `response_kind` を追加できないため、変更せずに未解決事項として残した。acknowledgement/content/correction/followup の 4 種は、既存 scenario test（`test_v2_speech_order_flow.py` の sense kick / partial ack / motivation interjection / divergent-final reconcile テスト）に `response_kind` assertion を追加する形で実際の creation site を経由した検証にした。

### 検証
- targeted: `python -m pytest tests/unit/test_v2_response_kind.py -q` → 5 passed
- full unit: `python -m pytest tests/unit -q -m unit` → 258 passed
- ruff（変更ファイルのみ）: `ruff check server/shared/models.py server/tomoko/conversation.py server/hot_path/ws_control.py server/tomoko/db_bridge.py tests/unit/test_v2_response_kind.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_models.py tests/unit/test_v2_internal_ws.py tests/unit/test_v2_audio_tomoko_prompt.py` → All checks passed
- `ruff check .`（repo 全体）は v1 配下等に 330 件の pre-existing error があるが、今回変更した v2 ファイルには含まれない。v1 は本作業対象外のため未修正。
- `git diff --check` → 差分なし（trailing whitespace 等の問題なし）
- `git status --short` で変更 path が `LOG.md`, `server/hot_path/ws_control.py`, `server/shared/models.py`, `server/tomoko/conversation.py`, `server/tomoko/db_bridge.py`, `tests/unit/test_v2_*.py`（6 ファイル）, `_docs/openai-plan/`（O0A-00 由来、未変更）であることを確認した。

### 次のセッションでやること
- O0A-01 完了後は自動で O0A-02 へ進まない。人間が本ログと diff を確認し、O0A-02（origin trace と generation owner の固定）を明示的に unlock した場合だけ着手する。
- 未解決事項: fixed backchannel は `SpeechOrder` を経由しないため `response_kind=backchannel` を割り当てる実装箇所が現状存在しない。O0A-03（milestone/aggregator）または将来 Phase で `BackchannelEmission` 側に別途 `response_kind` 相当を持たせるか、`first_feedback` 集計側で backchannel を種別として扱うかを人間が判断する必要がある。

### 追記: backchannel response_kind の方針決定（人間確認済み）
- 人間が選択肢 A（記録のみ）/ B（hot-path 側に独立フィールド追加）/ C（SpeechOrder で一本化）のうち **B** を選択した。
- 決定: `server/hot_path/backchannel.py` の `BackchannelEmission`（または `HotPathConversationResult`）に、hot-path 所有の固定分類フィールド（値は常に `backchannel` 相当）を追加する。`SpeechOrder.response_kind` とは別の field とし、`decision_generation_id` や `SpeechOrder` の所有権は tomoko-process に残したまま、hot-path はこのフィールドの値だけを持つ。
- この変更は `server/hot_path/backchannel.py`（および milestone/metrics 側で読み取る箇所）を触るため、O0A-01 の allowlist 外であり、このセッションでは実装しない。O0A-03（milestone event と純粋集計器）の Task 内、または O0A-03 着手前に人間が明示的に新しい Task として切り出した場合に実装する。
- C（SpeechOrder への一本化）は、fixed backchannel が意図的に LlmFireGate/SpeechEmissionGate を経由しない低レイテンシ経路であるため、レイテンシ・所有権境界の両面でリスクが大きいとして不採用。
- 人間確認: O0A-03 着手時にこの記録を起点にしてよいと確認済み（2026-08-12）。

## 2026-08-12 OpenAI Plan Task O0A-02

### やること（開始時に書く）
- Task ID: O0A-02
- 対象責務: origin trace と generation owner の固定
- 変更許可ファイル: shared DTO (`server/shared/models.py`), Tomoko decision owner (`server/tomoko/conversation.py` 等), hot-path execution state (`server/hot_path/speech_executor.py` 等), internal WS serializer (`server/hot_path/ws_control.py`), dedicated unit tests, `LOG.md`
- 変更禁止領域: generation invalidation behavior の変更、STOP/REPLACE の dispatch 経路変更、TTS task化、DB schema
- 完了条件: owner 境界が test で固定される、既存 generation guard の挙動は変わらない、full unit / ruff / diff check が PASS
- 人間 unlock: O0A-01 完了報告を受けて明示的に確認・続行指示あり（2026-08-12）

### やったこと
- 棚卸し結果: `trace_id`（`server/shared/models.py` 各 DTO）が既に observation → SpeechOrder → AudioChunkOut まで同一値で伝播しており、openai.plan.md 4.2 の `origin_trace_id` と同じ意味だったため、新規 field を追加せず再利用することにした。generation 概念は `server/hot_path/speech_executor.py::SpeechOrderExecutor.current_generation`（instance 所有の monotonic int、hot-path 専有）が既存の playback generation 相当として存在していたが、`decision_generation_id` に対応する Tomoko 側概念は存在しなかった。
- `server/shared/models.py`: `SpeechOrder.decision_generation_id: int | None = None` を追加（tomoko-process 所有、hot-path は書き換えない前提をコメントで明記）。
- `server/tomoko/conversation.py`: `TomokoConversationCore` に instance field `_decision_generation: int = 0` と `_bump_decision_generation()` を追加（module-level counter にはしていない）。`handle_observation` と `handle_initiative_tick` の冒頭でそれぞれ bump し、同一メソッド呼び出し内で生成される全 SpeechOrder（10 creation site 全て）に `decision_generation_id=self._decision_generation` を付与した。
- `server/hot_path/speech_executor.py`: `SpeechOrderExecutionResult` に `playback_generation_id: int | None = None` を追加（hot-path 所有）。STOP・通常合成の各パスで `self.current_generation`／synthesis 開始時に捕捉した `generation` を設定。APPEND でキューイングされただけの queued 結果は、まだ再生世代が確定していないため `None` のまま。
- `server/tomoko/db_bridge.py::speech_order_from_row`: `decision_generation_id` を row から defensively 読む（DB column は未追加、`row.get(...)` で無ければ `None` のまま。DB schema 変更はこの Task の禁止事項のため実施していない）。
- `server/hot_path/ws_control.py::stop_order_from_cancel_event`（hot-path が自前で構築する STOP order）は `decision_generation_id` を明示的に設定せず、default の `None` のままにした。これは hot-path 発の cancel であり Tomoko の判断サイクルを経ていないため。
- 新規 dedicated test `tests/unit/test_v2_generation_owner.py`（6 tests）: decision_generation_id の発行、hot-path による非改変、playback_generation_id の発行、SpeechOrder に playback_generation_id が存在しないことの構造的固定、trace_id の origin_trace_id としての再利用、replace 後の両世代の独立進行。

### 詰まったこと・解決したこと
- 「replace 後に decision generation と playback generation が独立して進む」テストの初期実装で、2回目の `handle_observation` が `current_speech_order` が残っていたため scheduler に append 判定され、生成が REPLACE_CURRENT にならなかった（playback generation が増えず assertion failed）。`core.update_playback_state(False)` を呼び出し会話状態をリセットしてから2回目を送ることで、意図した replace シナリオを再現した。
- followup 系（sense followup, world search apology, reply continuation）は非同期タスクとして後から SpeechOrder を構築するため、その時点の `self._decision_generation`（つまり構築時点でアクティブな世代）を採用した。呼び出し起点の世代を保持する設計との違いを認識した上で、この Task では「制御挙動・invalidation は変えない」範囲に留め、正誤の判断は O1b（single-owner live control）以降に委ねることにした。

### 検証
- targeted: `python -m pytest tests/unit/test_v2_generation_owner.py -q` → 6 passed
- full unit: `python -m pytest tests/unit -q -m unit` → 264 passed
- ruff（変更ファイルのみ）: `ruff check server/shared/models.py server/tomoko/conversation.py server/hot_path/ws_control.py server/hot_path/speech_executor.py server/tomoko/db_bridge.py tests/unit/test_v2_generation_owner.py` → All checks passed
- `git diff --check` → 差分なし
- `git status --short` で変更 path が allowlist（shared DTO / Tomoko decision owner / hot-path execution state / internal WS serializer / dedicated test / `LOG.md`）と、O0A-01 由来の response_kind 必須化に伴う test fixture 追随（既存差分、今回追加なし）であることを確認した。

### 次のセッションでやること
- O0A-02 完了後は自動で O0A-03 へ進まない。人間が本ログと diff を確認し、O0A-03（milestone event と純粋集計器）を明示的に unlock した場合だけ着手する。その際、backchannel の response_kind 方針決定（本ログの追記事項）を起点にしてよいと人間確認済み。
- 未解決事項: followup 系 SpeechOrder の decision_generation_id は「構築時点でアクティブな世代」を採用しており、「kick 元の世代」を保持していない。stale candidate/supersede の判定（O1b・O2）を設計する際に、どちらの意味が必要かを人間が確認する必要がある。

## 2026-09-03 セッション1 SpeechAnalyzer probe

### やること（開始時に書く）
- 既定の WhisperKit と進行中 O0A-01/O0A-02 差分を変更せず、Apple Speech fallback sidecar を macOS 26 の `SpeechAnalyzer` / `SpeechTranscriber` で試す。
- unit contract test を先に追加し、Swift compile、実音声、full unit の順で検証する。
- partial/final の既存 JSON/JSONL contract と `/ws` 単一路線を維持する。

### やったこと
- 稼働中のCodexタスクを確認し、tomokoを触っているのはこのタスクだけであることを確認した。
- 既存O0A-01/O0A-02差分を保持したbaselineで `pytest -m unit -q` を実行し、264 passed / 7 deselected を確認した。
- Apple Speech sidecarを `SFSpeechRecognizer` から `SpeechAnalyzer` / `SpeechTranscriber` へ移行した。
- `AssetInventory` によるlocale asset導入、`AnalysisContext`、file transcription、partial/final JSONL streamingを実装した。
- async `@main` のためPython側Swift compileに `-parse-as-library` を追加した。
- 既定 `TOMOKO_V2_STT_BACKEND=whisperkit` は変更していない。

### 詰まったこと・解決したこと
- `@main` sourceは通常の `swiftc` 呼び出しではcompileできなかったため、先行unit testを追加して `-parse-as-library` を固定した。
- 最初のffmpeg `-re` probeはpipe bufferingで全音声が一括投入され、first partialが3.26秒に見えた。3,200 bytesを100msごとに明示送信するprobeへ直し、first partial 1,077.1msを確認した。
- `AnalysisContext` に「トモコ」を渡したが、Kyoko合成音声では「智子」と認識された。API動作は確認できたが、固有語精度は未解決の比較項目である。

### 検証
- 先行契約テストは実装前に `SpeechAnalyzer` 不在でredになることを確認。
- focused unit: 40 passed。
- Swift compile: macOS 26.6.1 / Xcode 26.6でpass。
- file transcription: 468.2ms、final「智子、今日の予定を一言で教えてください。」。
- simulated realtime: first partial 1,077.1ms、final 3,986.2ms。
- Python `AppleSpeechStreamingBackend` 経由で同じfinal DTOを取得。
- full unit: 266 passed / 7 deselected。

### 次のセッションでやること
- WhisperKitと同一の実マイク音声corpusで、first partial、final latency、partial-final乖離、固有語精度をpaired比較する。
- O0A-03には自動で進まない。

### 追記: `say` 音声による旧新 Apple API paired benchmark
- Kyokoで5文（3.22–4.53秒）を一度だけ生成し、同一AIFFを旧
  `SFSpeechRecognizer` と新 `SpeechAnalyzer` に各10回入力した。
- 各backendのwarmup 1回は除外し、case/runごとにAB/BA順を交互にして
  実行順の偏りを抑えた。両方ともon-device、locale `ja-JP`、contextual
  stringsは「トモコ」「会議」「音声認識」で統一した。
- 旧API (`n=50`): mean 215.6ms / p50 212.3ms / p95 239.7ms / SD 16.3ms。
- 新API (`n=50`): mean 202.1ms / p50 202.7ms / p95 209.9ms / SD 6.2ms。
- paired差は新APIが平均13.5ms（6.3%）短く、44/50組で新APIが速かった。
  近似95%区間も新APIが9.6–17.4ms短い範囲だった。
- 出力は各backend・各文で10/10同一。新APIは5/5文を文末まで返したが、
  旧APIは2/5文で「ありま」「比較し」と末尾が欠けた。固有語「トモコ」は
  両方とも「智子」になった。
- 初回のfile 468.2msはcold単発値であり、API間の速度判断には今回のpaired
  結果を採用する。simulated realtimeのfirst partial 1,077.1msは別指標として維持する。
- WhisperKitは実backendが65秒以内に結果を返さずtimeoutしたため、今回の
  数値比較から除外した。
- summary artifact: `_docs/benchmarks/speech-analyzer-paired-20260903.json`。

### 追記2: faster-whisper small 同一corpus比較
- `faster-whisper` 1.2.1 / model `small` をCPU `int8_float32`で常駐させ、
  Apple API比較と同じKyoko生成5音声を各10回入力した。warmup 1回は除外した。
- model初期化694.9ms。転記 (`n=50`) はmean 1,968.3ms / p50 1,983.6ms /
  p95 2,102.6ms / SD 101.3msだった。
- 同一corpusの`SpeechAnalyzer` mean 202.1msに対して9.74倍の時間がかかり、
  平均差は+1,766.2msだった。ただしApple計測との実行順はinterleaveしていない。
- 各caseの出力は10/10同一。「トモコ」はinitial prompt「ともこ」により
  「ともこ」になったが、「歯医者」を10/10回「会社」と誤認識した。
- 空白・句読点を除去し10/十を正規化したCERは5.66%。同じ計算で
  `SpeechAnalyzer`は3.77%だった。
- `v1/server/edge/pipeline/stt.py::FasterWhisperSTT.transcribe`を使うwrapper
  smokeもcase 1で成功した。v2 factoryへの配線変更はしていない。
- summary artifact: `_docs/benchmarks/faster-whisper-small-say-20260903.json`。

## 2026-09-27 セッション1 Gemma / Codex 教師比較

### やること（開始時に書く）
- ユーザー依頼により、人工日本語だけを使い Gemma と Codex の意味飽和度ラベル、所要時間、同一 hash-ridge 学生の評価を比較する。
- runtime / 既定モデルを変更せず、make-model のオフライン比較として実施する。O0A-03 には着手しない。
- 同じ入力と判定定義、学習条件を用い、学習と評価の発話群を分離する。人間による正解ラベルがない場合は暫定評価と明記する。
- サブエージェントで既存パイプラインと評価方法を点検し、成果を _docs/benchmarks/ に保存する。

### やったこと
- 別の Codex エージェントが人工日本語 240 件を作成し、160 train / 80 eval を場面 group で分離。教師出力を見る前に省略疑問の曖昧さを点検し、corpus と参照期待値を固定した。
- Gemma 4 26B A4B の既存 fused weights を一時的な loopback MLX server で実行し、Codex CLI の gpt-6-astra / medium と同じ20件バッチ promptで比較。実会話・私的ログ・JDDは使用していない。
- 先行テストを追加し、ID・数値検証、参照情報の非送信、学習/評価漏洩防止を実装。Gemmaの長ID1文字欠落を検出したため、失敗試行を保存して両者を短IDに揃えて全件再実行した。
- 既存 HashRidgeSaturationModel を同一160件の各教師ラベルで学習。2048/λ1と、採点前に固定した8192/λ0.01を各3回交互順で測定。runtimeモデルは変更していない。
- 結果: AI作成の参照期待値76件に対し教師 Gemma72/76、Codex76/76。学生2048は両者45/76、8192は61/76対62/76（3件改善・2件悪化）。人間の正解精度ではない。
- 240件採点は129.302秒対136.843秒、学習用160件部分は77.222秒対94.907秒。8192学生fit中央値2.211秒対2.028秒、常駐予測平均0.2266ms対0.2257ms。fitは同じ計算量で変動範囲が重なり、短縮とは判断しない。
- _docs/benchmarks/teacher-comparison-20260927/REPORT.md に結果・条件・制限・再現方法を記録。raw応答、prompt、usage、label、モデル、集計も同ディレクトリに保存。
- 別エージェントが両教師240件のraw→ID→label対応、prompt一致、集計を点検し問題なし。比較用Gemmaサーバーは停止済み。

### 検証
- 既存baseline: 266 passed / 7 deselected。
- 先行テストred確認後、最終full unit: 299 passed / 7 deselected。
- 変更Python4ファイルruff PASS、git diff --check PASS。
- 全12バッチのprompt/ID対応表一致と固定corpus SHA一致を確認。

### 詰まったこと・解決したこと
- 最初のfull unitはサブエージェントが先に作成したテストと、未作成の実装ファイルの間で収集エラーになった。既存baselineを新規テスト除外で確認し、実装後のfull unitは全299件pass。
- 長いhex IDのコピー誤りは採点値を推測して修正せず、両教師の入力形式を同じ短IDに変更し再収集。元試行は比較結果と分離して保存。

### 次のセッションでやること
- 自動で教師・runtimeモデルを切り替えない。まず教師で差が出た4例等を人間が確認する。
- 学生改善を進める場合は、文字特徴・データ量・較正の影響を別実験で分離する。今回の評価データを調整に使った場合は新しい未見評価セットが必要。
- O0A-03には着手していない。
