# MEMORY.md

## 確定した判断

### v2 は v1 を継続実装せず root に作り直す
v1 の実装・テスト・ログ・知見は `v1/` を参照専用として保持し、v2 は root の
`server/` / `client/` / `tests/` / `scripts/` に新しい境界で実装する。

### PostgreSQL と id-only NOTIFY を source of truth にする
v2 の process 間連携は DB row を本体にし、`LISTEN/NOTIFY` payload は UUID 文字列だけにする。
payload に JSON や本文を載せる v1/実験的経路は v2 本線では採用しない。

### hot path は gate を持たない
`hot-path-process` は音声入出力、STT observation、LLM/TTS 実行だけを担当する。
話してよいか、どの prompt を実行するか、古い request を破棄するかは `tomoko-process`
の deterministic decision と prompt lifecycle が所有する。

### v2 初期は production scaffold を優先する
Apple Speech / VOICEVOX / Calendar / OCR / live conversation は外部実機依存を持つため、
まず interface、DB contract、unit-testable な deterministic model、smoke hook を作る。
実機 smoke の結果は `_docs/latency.md` と `LOG.md` に追記して昇格判断する。

### v1 から継承する判断
- VAD idle pre-roll は発話冒頭欠落対策として保持する。
- VAP は VAD 置換ではなく `p_yielding` 由来の silence ms side-channel として使う。
- VOICEVOX chunked は complete WAV chunk 境界を維持し、raw PCM は client に流さない。
- browser の audio device selector は client-only UI に閉じる。
- raw screenshot / camera frame は online prompt に直接入れない。
- prompt は安定 context を前半、current utterance を明示位置、volatile recall を後半に置く。

### v2 scaffold は Phase V2.0-V2.20 の境界を先に実装する
2026-06-18 の実装では、root v2 を production runtime へ直接つなぐ前に、全 Phase の boundary、
DTO、DB schema、deterministic model、process CLI、report hook、unit tests を先に揃えた。
外部 runtime を必要とする Apple Speech / VOICEVOX / LLM / OCR は interface と readiness hook に閉じ、
unit tests は fake backend または純関数で contract を固定する。

### v2 DB schema は additive な `100_v2_core.sql` に集約する
v2 用 table は既存 v1 schema を変更せず、`docker/postgres/init/100_v2_core.sql` に additive に作る。
NOTIFY は `v2_notify_id(channel_name, event_id)` を経由し、許可 channel と UUID payload だけを受け付ける。

### browser shell は `/client` static と `/ws` だけにする
root hot-path FastAPI は `/` で `client/index.html`、`/client/*` で静的ファイルを返し、runtime 通信は
`/ws` のみを使う。client は mic bytes 送信、audio stop command、JSON event 表示に留める。

### v2 runtime launcher は v1 と同じ dflash / VOICEVOX 操作感にする
2026-06-18 のセッション3で、root `Makefile` に v1 相当の `llm-run` / `llm-stop` /
`voicevox-run` / `tmux-runtime` / `run` / `stop` / `a` を復元した。
main LLM は dflash `8082` + `v1/loras/lora/fused_model` + `z-lab/gemma-4-26B-A4B-it-DFlash`、
summary/background LLM は dflash `8081` + Gemma 4 31B、VOICEVOX は sibling
`async-voicevox/run_streaming_voicevox.command` + `50122` を既定にする。

### v2 OCR はまず macOS capture + tesseract + OS metadata で実 runtime 化する
Apple Vision OCR へ切り替える余地は残すが、初期の実 runtime は `screencapture` で画像を取り、
`tesseract` で文字を拾い、`osascript` で front app / window title / Chrome title / URL を補助証拠として
保存する。VLM JSON ではなく OCR/OS metadata を主材料にする v1 thinker2 の判断を継承する。

### v2 hot-path の実 prompt smoke は `/ws` 上で完結させる
root `/ws` は text prompt smoke 用に `prompt` / `text_prompt` / `user_text` event を受け取り、
`PromptExecutor` 経由で dflash text event と VOICEVOX binary WAV chunk を返す。
client は server から届く binary WAV を再生するだけで、発話可否や retry などの状態判定は持たない。

### v2 STT / OCR runtime は macOS sidecar を root に持つ
2026-06-18 セッション4で、STT は `scripts/apple_speech_stt/` から build される Apple Speech sidecar、
OCR は `scripts/vision_ocr/` から build される Vision.framework sidecar を root v2 の実 runtime とした。
OCR は Vision を優先し、失敗時だけ tesseract fallback を使う。`/ws` の音声 conversation smoke は
VAD pre-roll -> STT observation -> tomoko durable utterance -> prompt execution -> binary WAV 返却を同じ
WebSocket 上で確認する。

### v2 hot-path は TTS 送出音を server-owned echo suppression window で扱う
2026-06-18 セッション5の実 runtime log で、Tomoko の TTS 出力がマイクへ回り込み、
同じ応答が数秒おきに LLM/VOICEVOX へ再投入される発話ループを確認した。
root v2 hot-path では、送出する complete WAV chunk の duration + grace 中は mic bytes を
VAD/STT に入れず、送出時に VAD pre-roll / 発話中バッファを reset する。
client は再生と表示だけを担当し、自己発話判定やリトライ判断は持たない。

### セッション5の echo suppression 判断はヘッドセット前提では否定する
2026-06-18 セッション6で、ユーザーはヘッドセットを使うため音声的な回り込みは起きない前提だと確認した。
その前提では Tomoko 発話中に mic bytes を VAD/STT 前で捨てると barge-in / 同時発話を壊す。
発話ループの主要候補は、無音・ノイズ VAD segment に対して Apple Speech が空文字 final を返し、
それを durable user utterance として採用して generic reply prompt が走る経路である。
root v2 では blank final STT は transcript observation としては見えても、durable utterance /
prompt request には昇格しない。

### v2 live debug は console-visible event stream を優先する
2026-06-18 セッション7で、tmux pane を見て原因追跡できるよう、runtime / hot-path / audio /
STT の主要境界は標準出力へ `[tomoko:<process>] event key=value` 形式で出すことにした。
JSONL は後追い分析用、console-visible log は live conversation 中の一次観測用として使う。
client UI には STT final と TTS result の timeline を表示し、ブラウザ上でも発話採用と音声出力を追う。

### v2 VOICEVOX speech speed は 1.5 を既定にする
2026-06-18 セッション8で、Tomoko の発話を早口にするため root v2 の VOICEVOX `speedScale`
既定値を `1.5` にした。実 runtime では `TOMOKO_V2_VOICEVOX_SPEED` で上書きできる。

### v2 final STT hallucination は辞書 block で durable utterance にしない
2026-06-18 セッション9で、実 `logs/server-debug.log` に出ていた単独 final STT `はい` / `い` と
blank を root v2 の初期 block 辞書に入れた。block は UI 表示だけで隠すのではなく、
`TomokoProcessCore` が durable utterance / prompt request に昇格しない境界で行う。
block された時は console に `stt_rule_blocked` / `stt_hallucination_blocked` を出す。

### v2 prompt は user と Tomoko の speaker 付き直近履歴を載せる
2026-06-18 セッション10で、root v2 の prompt stable context は user-only の
`recent_user_raw` だけでなく、LLM complete text を `recent_tomoko_raw` として次 turn に載せる。
履歴は `ConversationHistoryItem(speaker, text)` として `ContextSnapshot.recent_history` に保持する。

## 未解決の疑問（人間への確認待ち）

### [2026-06-18] live acceptance の実機検証タイミング
V2.20 の 10 分 live conversation smoke は Apple Speech / VOICEVOX / LLM runtime / OCR の
実機状態に依存する。scaffold と readiness check は実装するが、実測は runtime 起動後に別途行う。

## 気づき

### root `MEMORY.md` は Phase V2.0 で作成された
作業開始時点では root `MEMORY.md` が無く、v1 の `MEMORY.md` と root `LOG.md` の前回記録を参照した。

## 2026-06-18 セッション13 確定した判断

### v2 main conversation は SpeechOrder を主契約にする
`PromptRequest` は互換用に残すが、音声会話の主線は
`STT observation -> SemanticSaturationJudge -> SpeechScheduler -> LLM text -> SpeechOrder -> SpeechOrderExecutor`
に寄せる。LLM は発話本文だけを生成し、speak / suppress / replace / append / stop の判断は
`SpeechScheduler` が `score_breakdown` 付きで行う。

### scheduler smoke は fake と real say の二段に分ける
`make v2-scheduler-conversation-smoke` は外部 runtime なしで縦切り contract を固定し、
`make v2-scheduler-say-latency-smoke` は起動済み dflash / VOICEVOX / Apple Speech で
実 `/ws` audio path を測る。2026-06-18 の real smoke では voice-end to first audio が
2862.5ms、artifact は `logs/scheduler-say-latency-20260618-132107.json`。

### DB 分離は schema と bridge helper を先に固定する
`v2_speech_orders` / `v2_speech_scheduler_decisions` /
`v2_semantic_saturation_observations` と `v2_speech_order` NOTIFY channel を追加した。
常駐 LISTEN worker と hot-path の DB 書き込み接続は次の実装単位として残し、現時点の実 `/ws`
会話は in-process vertical path で動かす。

## 2026-06-18 セッション14 確定した判断

### DB 分離 smoke は hot-path と tomoko-process を完全別 process で通す
`TOMOKO_V2_DB_SPLIT=1` の hot-path は STT observation を DB に insert して
`v2_stt_observation` を id-only NOTIFY する。`tomoko-db` process は
`v2_stt_observation` を LISTEN し、semantic saturation / scheduler decision /
speech-order を DB に保存して `v2_speech_order` を id-only NOTIFY する。
hot-path は `v2_speech_order` を LISTEN して `SpeechOrderExecutor` で TTS/audio を実行し、
`v2_audio_output_events` を保存する。NOTIFY 欠落に備え、同じ trace_id の未実行 order を
短時間 polling で回収する。

### DB split の prompt request は未永続 context snapshot を参照しない
tomoko-process 側の `PromptRequest` は現時点では scheduler/LLM の中間契約であり、
DB smoke では context snapshot row をまだ永続化しない。そのため `v2_prompt_requests`
への保存は未永続の `context_snapshot_id` / `utterance_id` / `candidate_id` FK を持たせず、
音声出力の request row は hot-path が speech-order id で作る。

### fake DB split smoke の latency
`make v2-db-split-smoke` は fake STT / fake LLM / fake TTS で process 間 DB bridge だけを測る。
2026-06-18 の smoke は total 67.6ms、transcript->order 0.1ms、order->first audio 0.2ms。
artifact は `logs/db-split-smoke-20260618-133937.json`。

## 2026-06-18 セッション15 確定した判断

### DB split runtime は process lifetime connection を持つ
DB split の初回実装は hot-path が発話ごとに LISTEN / write / order load / recovery poll /
audio event 保存の connection を開き、tomoko-db worker も通知ごとに work connection を開いていた。
2026-06-18 セッション15で、hot-path は `/ws` ready 前に `v2_speech_order` LISTEN connection と
write/read connection を warm し、その後の STT insert / order load / recovery polling /
audio event 保存で再接続しないようにした。tomoko-db worker も `v2_stt_observation` LISTEN
connection と work connection を process lifetime で保持する。

### process-lifetime DB connection 後の split latency
fake DB split smoke は server 内部 total 15.8ms、notify->order 13.5ms、order->first audio 2.3ms
まで下がった。実 Apple Speech / dflash / VOICEVOX の分離版 say smoke は voice-end to first audio
2153.8ms、server STT-start to audio-ready 1733.9ms、notify->order 826.3ms、order->VOICEVOX ready
607.4ms。artifact は `logs/say-latency-20260618-140145.json`。
