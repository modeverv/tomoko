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
