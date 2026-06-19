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

### saturation 蒸留モデル作成は root `make-model/` の offline workbench に閉じる
2026-06-19 セッション1で、Gemma 4 26B MLX 4bit / OpenAI-compatible endpoint を教師にして
partial prefix ごとの `SATURATION=0.0..1.0` ラベルを作り、hashed character n-gram +
ridge regression の軽量 scorer へ蒸留する `make-model/` を追加した。
初期学生モデルは runtime 採用ではなく、JSON artifact を作って Gemma semantic lane と
shadow 比較するための offline workbench とする。

### Japanese Daily Dialogue は ignored data として prefix dataset 化する
2026-06-19 セッション2で、Japanese Daily Dialogue を `make-model/data/external/` に clone し、
`make-model/data/japanese-daily-dialogue/corpus.jsonl` と `prefixes.jsonl` に変換した。
CC BY-NC-ND 4.0 / 非商用研究目的 / 再配布不可の扱いに合わせ、raw data、変換 corpus、
teacher labels、model artifacts は `.gitignore` された `make-model/data/` / `make-model/artifacts/`
配下に置き、repo には importer と README 手順だけを残す。

### JDD 1000件 teacher label は pipeline smoke であり本命評価ではない
2026-06-19 セッション3で、JDD prefix 先頭 1000 件を Gemma 4 26B teacher label 化し、
hash-ridge scorer を train/evaluate した。1000 label 作成は約19分で、評価は
binary_accuracy 0.817、MAE 0.1347、RMSE 0.1777。
ただし `--limit 1000` は先頭から取るため 43 utterances 分に偏り、
`今日の予定を教えて` の予測も 0.1294 と低かった。これは model 採用判断ではなく
end-to-end pipeline smoke として扱う。次は utterance 全体から prefix をサンプリングする。

### Gemma teacher input subset は seed 付きランダム抽出にする
2026-06-19 セッション4で、`make-model/generate_teacher_labels.py` に
`--sample-size` / `--sample-seed` を追加した。JDD 1000件評価は旧 `--limit 1000`
ではなく `--sample-size 1000 --sample-seed 20260619` を使い、JDD prefix 全体から
再現可能なランダム subset を作る。`--limit` は smoke 用の先頭 N 件として残す。

### 蒸留 saturation scorer の hot predict は sub-ms
2026-06-19 セッション5で、`make-model/benchmark_saturation_latency.py` を追加し、
`jdd-gemma26b-1000-saturation-model.json` を1回ロードした後に
`今日の予定を教えて` を warmup 1000 / repeats 10000 で測定した。
結果は mean 0.0744ms、p50 0.0734ms、p95 0.0878ms、max 2.1347ms。
CLI の `uv run python predict_saturation.py ...` で見える 111ms は起動・ロード・print 込みであり、
Tomoko runtime に resident model として組み込む場合の hot 判定コストは 0.1ms 前後と見る。

### teacher label prompt は runtime E2B semantic lane と同じ contract にする
2026-06-19 セッション6で、`make-model` の Gemma 26B teacher system prompt を
runtime の `OpenAICompatibleSaturationBackend` と同じ `SATURATION_SYSTEM_PROMPT` に揃えた。
user message も既存 `saturation_prompt()` を使い、「会話相手が今返し始めてよい度合い」の定義と
few-shot を含める。旧 `意味飽和度を採点する教師モデルです` だけの system 文言は使わない。
既存の `jdd-gemma26b-1000` artifact は旧 teacher prompt 由来なので、採用評価用には作り直す。
2026-06-19 セッション7で、teacher payload の user message に `saturation_prompt()` の
高い値/低い値の説明と few-shot が入ることを unit test で明示的に固定した。

### 10000件 teacher labels は train/eval split で評価する
2026-06-19 セッション8で、`make-model/split_teacher_labels.py` を追加した。
seed 付き shuffle で 10000 labels を 8000 train / 2000 eval に分ける。
手元の 10000 labels は `label_source=teacher_llm` 10000件で、8000 train の train metrics は
binary_accuracy 0.82725、MAE 0.1539、RMSE 0.2005。held-out 2000 eval は
binary_accuracy 0.8285、MAE 0.1795、RMSE 0.2327。

### manual anchor 1000件追加で final 代表例は改善する
2026-06-19 セッション9で、`make-model/make_anchor_teacher_labels.py` を追加し、
手作り `manual_anchor` 1000件を 8000 teacher train split に足して 9000件で train した。
held-out JDD 2000 eval は binary_accuracy 0.8265、MAE 0.1802、RMSE 0.2334。
manual anchor 1000 eval は binary_accuracy 0.989、MAE 0.0453、RMSE 0.0619。
`今日の予定を教えて` は `predict_saturation.py` 既定の partial 扱いでは 0.4986、
`--final` 付きでは 0.9313。完了発話として評価する代表例は `--final` を付ける。

## 未解決の疑問（人間への確認待ち）

### [2026-06-18] live acceptance の実機検証タイミング
V2.20 の 10 分 live conversation smoke は Apple Speech / VOICEVOX / LLM runtime / OCR の
実機状態に依存する。scaffold と readiness check は実装するが、実測は runtime 起動後に別途行う。

## 気づき

### root `MEMORY.md` は Phase V2.0 で作成された
作業開始時点では root `MEMORY.md` が無く、v1 の `MEMORY.md` と root `LOG.md` の前回記録を参照した。

### pseudo partial STT は誤 partial でも speech-order を作ることがある
2026-06-18 セッション26の `logs/server-debug.log` では、同一ユーザー発話内で
partial `これは誰`、partial `これはダブルで出てるのか`、final `これはダブルで出ているのかST Tが`
がそれぞれ `append_after_current` speech-order を作り、3つの TTS が出た。
既存の active partial/final reconcile はテキスト類似時だけ効くため、Apple Speech pseudo partial の
途中誤認識が後続 partial/final と似ていない場合に網を抜ける。
対策候補は、同一 trace/VAD segment 内の partial は append せず replace/suppress へ寄せること、
または active partial がある間の後続 partial/final を「同じ発話の更新」として扱うことである。

### partial 応答開始は2回連続 high confirmation を要求する
2026-06-18 セッション27で、root v2 の partial speech-order 開始 gate を追加した。
partial で LLM/TTS へ進むには、`semantic_saturation >= 0.85`、scheduler score が
`partial_start_score_threshold` 以上、前回 high partial と normalize 後に大きく矛盾しないこと、
かつその状態が2回連続することを要求する。
1回目の high partial は `partial start gate is waiting for confirmation` で hold し、
矛盾する後続 partial は `partial start gate text changed too much` で hold する。

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

## 2026-06-18 セッション16 確定した判断

### v2 5ターン実 runtime smoke を同一 WebSocket で測る
v1 相当の multi-turn 実 runtime smoke として `make v2-five-turn-smoke` を追加した。
macOS `say` で5発話を作り、同一 `/ws` セッションに順番に流し、turn ごとの transcript /
model text / TTS text / first audio latency と全体 average / p95 / max を JSON artifact に残す。
2026-06-18 の実行では artifact `logs/five-turn-smoke-20260618-140934.json`、avg first audio
3491.2ms、p95 4387.7ms。turn 別 first audio は 2505.4 / 2869.6 / 3511.1 / 4182.1 / 4387.7ms。
turn が進むほど遅くなる傾向が見えたため、prompt/history増加と dflash cache hit を別途見る。

### 5ターン smoke artifact には会話 LLM prompt を保存する
`_send_prompt_execution_result` は `llm_prompt` event を `/ws` に流し、5ターン smoke は turn ごとの
`llm_prompt` を JSON に保存する。2026-06-18 の再実行 artifact は
`logs/five-turn-smoke-20260618-141915.json`。prompt chars は 136 / 199 / 260 / 341 / 379。

## 2026-06-18 セッション17 確定した判断

### DB split の session id は tomoko-process が DB で発番する
hot-path は raw STT observation を DB に入れて id-only NOTIFY するだけに保つ。
tomoko-process は final STT が durable utterance にできる時だけ open session を DB から読み、
open session が無ければ `v2_conversation_sessions` を新規発番する。
open session があり `last_activity_at` から idle gap を超えていれば、旧 session を
`close_reason='idle_gap'` で close して新 session を発番する。

### prompt history は現在発話を含めない
LLM prompt の `STABLE_CONTEXT` は同一 session の過去 user/tomoko 発話だけで作る。
現在の user 発話は `CURRENT_USER_UTTERANCE` のみに置き、stable context には入れない。
DB split では `v2_utterances` から同一 session の履歴を読んで prompt に渡し、
生成後に user durable utterance と Tomoko reply utterance を同じ session に保存する。

## 2026-06-18 セッション18 確定した判断

### main reply prompt は session transcript 形式にする
セッション履歴は `STABLE_CONTEXT` / `CURRENT_USER_UTTERANCE` ではなく、
`SYSTEM` / `INSTRUCTION` / `SESSION_TRANSCRIPT` として組み立てる。
`SESSION_TRANSCRIPT` には同一 session の `user:` / `tomoko:` 発話を順に並べ、
最後に現在 user 発話を置く。これにより 5ターン smoke artifact で会話 LLM に渡した prompt を
会話ログとしてそのまま読める。

### dflash prefix cache は prompt_text 文字列ではなく chat template 後 token prefix で見る
`SYSTEM` / `SESSION_TRANSCRIPT` / `INSTRUCTION` の exact order と、
`SYSTEM` / `INSTRUCTION` / `SESSION_TRANSCRIPT` の append-only 文字列 prompt は、どちらも
単一 user message として送る限り dflash prefix cache が hit しなかった。
理由は chat template 後の token 列では previous request の assistant 生成位置と
next request の user message 継続位置が一致しないため。

`SESSION_TRANSCRIPT` を OpenAI chat completion へ送る直前に `user` / `assistant` role の
message list に分解すると、2ターン目以降で dflash `prefix cache hit` が出た。
2026-06-18 の smoke artifact は `logs/five-turn-smoke-20260618-145708.json`。
dflash log では `prefix cache hit 40/63`, `59/86`, `82/112`, `108/132` tokens、
`prefill_tokens_saved` は 1822 から 2111 まで増えた。avg first audio は 2354.5ms、p95 は 3073.2ms。

## 2026-06-18 セッション19 確定した判断

### semantic saturation LLM は Gemma E2B を別 endpoint で見る
既存 dflash 8081/8082 は request の `model` 指定を受けても起動中の 31B/26B で返す。
また dflash は Gemma E2B 用 draft が無く、`mlx-community/gemma-4-e2b-it-OptiQ-4bit` を直接 serve できない。
Gemma E2B semantic lane の観測は `mlx_lm.server` など別 OpenAI 互換 endpoint を使う。
今回の smoke では `mlx_lm.server --model mlx-community/gemma-4-e2b-it-OptiQ-4bit --port 8083` を使った。

### Gemma E2B semantic prompt は compact few-shot にする
従来の説明文だけの saturation prompt では、Gemma E2B が
`トモコ、今日の予定を教えて` に `SATURATION=0.1` を返した。
`えっと -> 0.1`、`トモコ、今日の予定を教えて -> 0.95`、
`ただ、やっぱり -> 0.2` の compact few-shot prompt にすると同じ入力で
`SATURATION=0.95` を 290〜440ms 程度で返した。

### prefix-window smoke では final 前 early OK が観測できた
現行 Apple Speech sidecar は final-only のため、今回の実測は say 音声 prefix window を
疑似 partial として replay する推定である。`トモコ、今日の予定を一言で教えて。` では、
2400ms partial `智子今日の予定を` までは saturation 0.3 で OK なし。
3000ms partial `智子今日の予定を一言で教え` で saturation 0.8、E2B 判定 281.3ms、
estimated decision 3281.3ms from speech start となり、full final STT available 3634.0ms より
352.7ms 早く `would_start_llm=true` になった。artifact は
`logs/semantic-early-smoke-20260618-151319.json`。

## 2026-06-18 セッション20 確定した判断

### v2 Apple Speech partial は v1 と同じ pseudo streaming 方式で戻す
v1 の `AppleSpeechStreamingBackend` は Swift sidecar の true partial ではなく、Python 側で
音声 chunk を累積し、`stream_min_audio_ms` を超えた後に `stream_interval_ms` 間隔で
Apple Speech final transcription を再実行して `is_final=False` として扱っていた。
v2 も同じ方式で `streaming` / `stream_interval_ms` / `stream_min_audio_ms` /
`_last_stream_text` 抑制を移植した。

### 実 `/ws` path で final 前の E2B speech-order は確認できたが hot-path としてはまだ重い
`logs/say-latency-20260618-152817.json` では、partial `その今日の予定を教えて` が
elapsed 9168.0ms で出て、Gemma E2B saturation 0.8 相当、scheduler `replace_current`、
speech-order 作成まで進んだ。final transcript は elapsed 14276.5ms なので、
final STT より 5108.4ms 早い。
ただし first audio は voice-end から 4492.5ms 後で、ユーザー発話終了前の発話開始にはなっていない。
原因は partial STT / E2B saturation / LLM / TTS を WebSocket audio receive loop 内で await しており、
音声受信と VAD final 検出が詰まるため。次は partial 処理を audio receive loop から非同期に逃がす。

## 2026-06-18 セッション21 確定した判断

### partial / final processing は WebSocket receive loop から逃がす
`/ws` の audio receive loop で partial STT / E2B / LLM / TTS を await すると、
音声受信と VAD final 検出が詰まる。hot-path direct conversation では
`AudioPartialLane` と `AudioFinalLane` を持ち、receive loop は VAD と queue 投入だけを行う。
partial lane は queue に溜まった chunk を coalesce して Apple Speech pseudo partial の再実行回数を減らす。
final lane は partial lane が idle になるまで短く待ってから final STT を始め、Apple Speech を奪い合わない。

### partial reply は concise prompt にする
partial early-start は最初の音声到着が目的なので、partial observation から作る prompt は
`短く一文で返す` にする。これにより今回の smoke では partial WAV が 271916 bytes から
112684 bytes 程度まで縮んだ。

### async lane 後も reconcile は未完了
clean smoke の best artifact `logs/say-latency-20260618-160201.json` は voice-end to first audio 860.5ms。
final 確認込みの `logs/say-latency-20260618-160314.json` は first audio 1515.6ms、
final transcript 5123.1ms。前回の 4058〜4492ms より改善した。
ただし partial 由来の発話後に final / 後続 partial が append される重複はまだ残る。
次は同一 utterance の partial speech-order と final speech-order を reconcile する。

## 2026-06-18 セッション22 確定した判断

### partial speech-order 後の final は durable 保存だけして重複発話させない
partial 由来の speech-order が既に出ている同一 utterance について、後から final STT が来た場合は
final を durable user utterance として履歴に保存する。ただし Tomoko の speech-order / prompt は作らず、
`final reconciled with active partial reply` で suppress する。
これにより partial reply の後に final reply が append される二重発話を止める。

### Apple Speech partial の比較では wake word と filler 差分を normalize する
pseudo streaming partial は `その今日の予定を教えて` のように先頭へ `その` が付くことがあり、
final は `智子今日の予定を教えて...` のように wake word を含むことがある。
reconcile 判定では `トモコ` / `智子` / `その` / `えっと` / `あの` を除去した上で、
包含または prefix ratio で同一 utterance とみなす。

### 録音ファイル smoke は ffmpeg 優先で 16kHz mono PCM WAV に変換する
`scripts/v2_say_latency_smoke.py --input-wav` は QuickTime などの録音ファイルを `/ws` に replay できる。
`afconvert` は m4a 入力で Python 3.11 の `wave` が読めない WAVE_FORMAT_EXTENSIBLE を出す場合があるため、
`ffmpeg` が存在する環境では `ffmpeg -ac 1 -ar 16000 -sample_fmt s16` を優先する。
clean smoke `logs/say-latency-20260618-161626.json` では `_reference/test.m4a` を実測できた。

## 2026-06-18 セッション23 確定した判断

### partial start gate は saturation 単独ではなく総合 score も見る
`こんにちは今の気分を教えて下さい` の artifact では raw semantic saturation は 0.5 相当だったが、
reply pressure と saturation weight を足した総合 score は 0.775 まで出ていた。
この状態を `partial_start_saturation_threshold=0.75` だけで suppress するのは保守的すぎる。
今後は partial について、saturation が 0.75 未満でも score が 0.75 以上なら開始を許す。
低情報 partial は saturation と score の両方が低い時だけ suppress する。

## 2026-06-18 セッション24 確定した判断

### make run は E2B semantic endpoint も tmux runtime に含める
Gemma E2B semantic saturation endpoint は main dflash LLM とは別に `mlx_lm.server` で起動する。
`make run` / `tmux-runtime` では `semantic-e2b` window を `hot-path` より前に作り、
`http://127.0.0.1:8083/v1/models` を readiness に含める。
hot-path には `TOMOKO_V2_SEMANTIC_LLM=1`、URL、model を渡し、partial saturation が実 E2B に向くようにする。
