# MEMORY.md

## 確定した判断

### append_after_current dedupe guard は LLM 前で重複 final を抑制する
2026-06-20 セッション30で、前セッションの shadow model を runtime に昇格した。
`TomokoConversationCore` は final STT の通常応答で LLM prompt を作る前に
`HashRidgeAppendDedupeGuard` を呼び、直前 final user text と current final user text を比較する。
`duplicate_score >= 0.85` かつ `continuation_score <= 0.45` かつ
`new_intent_score <= 0.45` かつ `time_delta_ms <= 5000` の時だけ suppress する。

重複 final は durable observation として返すが、LLM/TTS と in-memory prompt history には進めない。
これにより `うんあんまりよくわかってない` -> `あんまりよくわかってない` のような
filler 差分 duplicate は会話推論と音声生成を二重に走らせない。
補足 continuation と話題変更 new_intent は suppress しない。
artifact が無い場合や `TOMOKO_V2_APPEND_DEDUPE=0` の場合は fail-open で guard 無しにする。
resident hot predict は mean 0.442ms / p50 0.403ms / p95 0.580ms。

### append_after_current dedupe は public synthetic shadow model から始める
2026-06-20 セッション29で、22:04 前後の `logs/server-debug.log` に
`うんあんまりよくわかってない` と `あんまりよくわかってない` が連続 final STT になり、
それぞれ `append_after_current` speech-order を作っていることを確認した。
runtime suppress はまだ入れず、まず `make-model/` に Hash/Ridge 系の
`HashRidgeAppendDedupeModel` を追加し、shadow guard 用に
`duplicate_score` / `continuation_score` / `new_intent_score` / `label` / debug features を返す。

public artifact は実ログや JDD を混ぜず、手作り synthetic anchor だけから作る。
artifact は `make-model/artifacts/public-synthetic-append-dedupe-h2048-l005-model.json`、
labels は `make-model/data/public-synthetic/append-dedupe-labels.jsonl`。
実ログからの seed 抽出は `make-model/data/private-log-seeds/` にだけ出し、public artifact へ混ぜない。
320件 anchor eval は accuracy 1.0、resident hot predict は load 0.905ms、
mean 0.409ms / p50 0.402ms / p95 0.439ms。

### UI Stop は client 再生停止と hot-path generation stop の両方を行う
2026-06-20 セッション24で、Stop ボタンは `audio_control` を `/ws` に送っていたが、
hot-path は ACK を返すだけで、browser に予約済みの `AudioBufferSourceNode` や進行中 TTS generation を
止めていないことを確認した。
既に browser に送った audio chunk は server から止められないため、client は active source を保持し、
Stop で `source.stop()`、`playbackTime` reset、古い binary audio chunk の破棄を行う。
同時に hot-path は `SpeechOrderExecutor.stop_playback()` で generation を進め、遅れて生成された chunk を
discard できる状態にする。

### MaAI result poll は result_dict_queue を timeout 付きで読む
2026-06-20 セッション23で、最新 `logs/server-debug.log` に
`[tomoko:backchannel] maai_backchannel_poll_error error='TypeError'` が連続していることを確認した。
installed `maai.Maai.get_result()` は timeout 引数を受け取らず、内部では `result_dict_queue.get()` を呼ぶ。
hot-path では block しない poll が必要なので、MaAI instance が `result_dict_queue` を持つ場合は
`result_dict_queue.get(timeout=0.1)` を直接使う。これにより backchannel lane は fail-open のまま、
`get_result(timeout=...)` の TypeError でログを埋めない。

### partial start gate は saturation か総合 score のどちらかで確認段階へ進める
2026-06-20 セッション23で、実ログ上は partial の総合 score が 0.86〜0.96 まで出ていても、
saturation が 0.75 未満だと `partial start gate is waiting for more semantic saturation` で suppress され、
final transcript まで待っているケースを確認した。
前のめりな開始は「saturation が高い」または「materials/pressure を合成した score が高い」のどちらかで
確認段階へ進める。低情報 partial として止めるのは、saturation と score の両方が低い時だけにする。
ただし誤発火を避けるため、確認段階では従来通り類似 partial が 2 回続くことを要求する。

### LLM 前は pressure synthesis gate に限定する
2026-06-20 セッション21で、S19 の `InferenceStartGate` を `LlmFireGate` に置き換えた。
LLM 前では `main_reply` / `initiative` / `world_summary` のような intent 的な enum を作らない。
materials から各 pressure を作り、`LlmFireGate` が pressure score を合成して
`do_not_fire` / `fire` / `cancel_or_replace_pending` だけを返す。

通常発話裁定経路は
`materials -> pressures -> LlmFireGate -> LLM -> PreparedSpeechCandidate -> SpeechEmissionGate -> SpeechOrder`
とする。どの pressure が強かったかは `score_breakdown` に残すが、
LLM 前の gate decision には pressure source の種類を混ぜない。

### runtime distilled saturation default は実在 public synthetic artifact にする
2026-06-20 セッション22で、hot-path `/ws` 接続時に
`jdd-gemma26b-10000-plus-anchors-contrastive-tail-referential-saturation-model.json`
を load しようとして `FileNotFoundError` で落ちることを最新 `logs/server-debug.log` から確認した。
現在この checkout に実在する runtime 採用可能 artifact は
`make-model/artifacts/public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json`
なので、Makefile と Python default はこの public synthetic artifact を指す。
artifact が存在しない場合でも hot-path `/ws` は落とさず、deterministic saturation fallback で起動する。

### Materials -> Pressures -> Gates を発話判断語彙にする
2026-06-20 セッション19で、S18 の `TurnOpportunitySnapshot` /
`TurnSignalAggregator` / `SpeechScheduler` 中心の整理を
`Materials -> Pressures -> Gates` の語彙で置き換えた。
raw 音声情報、無音時間、VAP/MaAI、STT、外部情報、人格傾向は
`materials` と呼ぶ。materials は判断ではなく計算材料である。

`DialogueTurnPressure` / `NaturalSpeechPressure` / `MotivationPressure` /
`WorldPressure` は materials から計算された中間量であり、gate は原則として
pressure と `PreparedSpeechCandidate` を読む。`silence_ms` や `p_yielding` は
特定 pressure の所有物ではなく、複数 pressure model が共有 material として参照する。

通常発話裁定経路は
`materials -> pressures -> InferenceStartGate -> LLM -> PreparedSpeechCandidate -> SpeechEmissionGate -> SpeechOrder`
とする。`SpeechScheduler` は legacy unit と返却互換の型名として残すが、
`TomokoConversationCore` の通常発話裁定経路では `SpeechScheduler.decide()` を呼ばない。

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

### contrastive anchor 1000件追加で逆説末尾を低くする
2026-06-20 セッション10で、`make_anchor_teacher_labels.py --kind contrastive` を追加し、
`しかし` / `けど` / `だが` / `だけど` / `とはいえ` などで終わる final 発話を
`manual_contrastive_anchor` として 1000件追加した。
8000 teacher + 1000 general anchor + 1000 contrastive anchor で train した結果、
`それが良いと思うがしかし --final` は 0.8711 から 0.3536 に下がった。
`今日の予定を教えて --final` は 0.9345 で高いまま。
held-out JDD eval は binary_accuracy 0.8135、MAE 0.1877、RMSE 0.2410 と少し下がる。
逆説を強く抑える版として扱い、採用前に shadow 比較する。

### `contrastive_tail` 明示特徴で逆説低ラベルの漏れを減らす
2026-06-20 セッション11で、hash-ridge saturation scorer に `contrastive_tail` extra feature を追加した。
旧 artifact は追加特徴を無視して互換 predict できる。
同じ 10000件 train で再学習した `contrastive-tail-feature` model は、
`それが良いと思うがしかし --final` 0.3228、`それが良いと思うけど --final` 0.1869、
`それが良いと思う --final` 0.6226、`今日の予定を教えて --final` 0.9381。
JDD held-out eval は binary_accuracy 0.8210、MAE 0.1833、RMSE 0.2359。
逆説なし前半文は 0.4033 から 0.6226 に戻ったが、まだ中程度なので必要なら positive counter-anchor を足す。

### referential positive anchor 1000件で指示語系を上げる
2026-06-20 セッション12で、`make_anchor_teacher_labels.py --kind referential` を追加した。
`それが良いと思う` / `それで問題ない` / `その方向でいい` / `これは違うと思う` などの
指示語・照応系完了文を `manual_referential_anchor` として 1000件追加した。
8000 teacher + 1000 general + 1000 contrastive + 1000 referential で train した結果、
`それが良いと思う --final` は 0.6226 から 0.7170 に上がった。
`それが良いと思うがしかし --final` は 0.3336 で低く維持し、
`今日の予定を教えて --final` は 0.9391。
JDD held-out eval は binary_accuracy 0.8215、MAE 0.1818、RMSE 0.2349。
referential anchor は MAE 0.0441 と近いが 0.75 threshold 付近の label が多く、binary_accuracy は 0.769。

### runtime semantic saturation は E2B endpoint ではなく蒸留 scorer を使う
2026-06-20 セッション13で、2026-06-18 セッション24の
`make run` が `semantic-e2b` window を起動する判断を上書きした。
今後の hot-path semantic saturation は
`make-model/artifacts/jdd-gemma26b-10000-plus-anchors-contrastive-tail-referential-saturation-model.json`
を resident load する蒸留 hash-ridge scorer を使う。
partial early start は partial 文字列でも `--final` 相当の特徴で採点し、0.75 以上が2回連続した時だけ通す。
ただし `はい` / `うん` など短い final STT は STT から即 final が来て1回しか判定できないため、
蒸留 scorer 側の short-ack ルールで低 saturation に clamp して吸収する。

### MaAI は hot-path 固定相槌専用センサーとして既定有効にする
2026-06-20 セッション15で、MaAI を VAP/VAD silence 制御ではなく、
hot-path の相槌専用 detector として使うことにした。
相槌候補は `うん` / `へえ` / `ほう` の3種に固定し、
`assets/backchannels/un.wav` / `hee.wav` / `hou.wav` を VOICEVOX speaker 8 / 1.5x /
16kHz mono WAV で事前生成した。相槌では main LLM と VOICEVOX runtime を呼ばず、
MaAI result の react/emo score が閾値以上、cooldown 外、Tomoko 音声出力中ではない時だけ
cached WAV を `/ws` から binary audio として返す。
`TOMOKO_V2_MAAI_BACKCHANNEL` は Makefile 既定で `1`。MaAI package が無い場合や
asset load に失敗した場合は hot-path 起動を落とさず no-op にする。

### 発話判断計算モデルは二段 gate として整理する
2026-06-20 セッション16で、発話判断計算モデルを `InferenceStartGate` と
`SpeechEmissionGate` の二段に分けて ARCHITECTURE.md に追記した。
`InferenceStartGate` は partial/final STT、semantic saturation、無音、外部調査結果、
calendar、motivation などから、Tomoko 側で重い推論を fire して発話候補を作るかを決める。
`SpeechEmissionGate` は生成済み候補を、ユーザー発話を遮ってでも hot-path に
speech-order として送るかを、motivation、semantic confidence、勘違いリスク、
interruption risk、stop intent、rejection/fatigue と競合させて決める。
現在の `SpeechScheduler` はこの二段の責務を一部同時に背負っているため、
今後の整理では DTO、ログ、report を二段 gate として分離して観測する。

### two-stage gate と internal WS snapshot を実装する
2026-06-20 セッション17で、`InferenceStartGate` と `SpeechEmissionGate` を
`server.tomoko.gates` に分けて実装した。
`TomokoConversationCore` は `InferenceStartGate -> SpeechScheduler -> SpeechEmissionGate`
の順に判断し、既存 scheduler は pressure score と text intent の中核として残す。

hot-path 側には `TurnSignalAggregator` を追加し、audio RMS と MaAI result
(`p_bc_react` / `p_bc_emo` / `p_yielding` 相当) を 200ms の
`TurnOpportunitySnapshot` に集約する。snapshot は direct hot-path conversation では
in-process の `TomokoConversationCore` に反映し、同時に `TOMOKO_INTERNAL_WS_URL`
へ internal WebSocket JSON として送る。Tomoko 側の `server.tomoko.realtime`
は `/internal/hot-path` で受け取り、latest snapshot を保持する。
この経路は durable state ではなく latest-wins の realtime 制御線として扱う。

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

## 2026-06-20 セッション18 確定した判断

### 公開用 saturation model は synthetic-only 系統を分ける
GitHub public に置きやすい semantic saturation artifact は、ネット上の会話コーパスを使わない
`public-synthetic` 系統として作る。
入力テキストは `make-model/generate_synthetic_saturation_corpus.py` の
Codex / ユーザー / 自作テンプレート由来 synthetic utterance だけにし、
初期 teacher label は Gemma 4 26B、補正 label は general / contrastive / referential /
life command の手作り anchor として provenance を明記する。
JDD 由来 artifact と混ぜず、公開時は `PUBLIC_SYNTHETIC_PROVENANCE.md` と
`MODEL_CARD.public-synthetic.md` を同時に確認する。

### teacher label 生成は incremental 書き込みを使う
Gemma 4 26B teacher label は 1 件 0.7〜1秒台かかることがあり、10000件実行は長時間になる。
`generate_teacher_labels.py --incremental --progress-every N` を使い、長時間実行を止めても
途中成果が JSONL に残るようにする。
2026-06-20 の smoke は Gemma teacher 200件を 160 train / 40 eval に分け、
manual anchors 4000件を train に足した。
最終候補 `public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json` は
held-out 40件で binary accuracy 0.90、hot predict mean 0.282ms。

## 2026-06-20 セッション25 確定した判断

### VAD 後 STT 前に低エネルギー segment gate を置く
最新 `logs/server-debug.log` では `vad_segment=574` に対して Apple Speech final 空文字が
492 件あり、Apple Speech と scheduler を無駄に回していた。
VAD hot loop は従来通り primitive のままにし、`AudioSpeechSegment` が完成した境界でだけ
duration / RMS を見る `SegmentSttGate` を置く。低エネルギー segment は `vad_segment_dropped`
として structured console log に残し、Apple Speech へ渡さない。

### active partial reply 後の同一 trace partial/final は追加発話しない
partial 由来の speech-order が既に出た同一 trace では、後続 partial が似ていなくても
`append_after_current` に進ませない。final も durable user utterance としては保存するが、
同一 trace の active partial reply 後なら prompt / speech-order を作らず discard/suppress する。
これは pseudo partial 誤認識が後から伸びて矛盾した追撃発話になるのを防ぐため。

### MaAI poll error は fail-open かつログを間引く
MaAI result poll は `result_dict_queue` / `output_queue` を優先して timeout 付きで読む。
queue 実装が `timeout` kwarg を受けない場合は timeout なし `get()` に fallback する。
それでも poll error が出る場合、backchannel lane は止めず、同じ error type は 1/10/100/1000 回目程度に
間引いて `maai_backchannel_poll_error` を出す。

### readiness は snapshot と transition を分けて記録する
起動直後の DB / LLM / VOICEVOX false は `readiness_snapshot` として記録し、
後で ready になった差分は `readiness_transition` として `component` / `path` / `previous_ready` /
`ready` を JSONL と console に残す。これにより `make run` / tmux runtime の契約を変えずに、
後続 ready 化を読み取れる。

### volatile recall は stable system prefix に入れない
dflash 直近ログでは prefix cache lookup 26 回に対して hit line 6 回、最後の lookup で
`evictions=27` まで増えていた。人格・履歴・直近文脈は削らず、揺れやすい `VOLATILE_RECALL` は
`SYSTEM` から外して `SESSION_TRANSCRIPT` 後に置き、OpenAI messages 変換では最後の user message
末尾へ足す。stable system prefix と過去 role messages を汚しにくくする。

## 2026-06-20 セッション26 確定した判断

### runtime context も stable system prefix に入れない
dflash log では直近 1200 行で prefix cache lookup 100 回に対して hit line 12 回、
最後の lookup は `hits=1+1` / `misses=17` / `evictions=27` / `prefill_tokens_saved=68` だった。
`PromptBuilderV2` では `summary[...]` / `calendar[...]` / `user_status=...` が system message に入り、
turn ごとの context 変化で先頭 token が揺れる可能性があった。

人格ヘッダと発話 instruction は stable system prefix に残し、summary / calendar / user_status は
`RUNTIME_CONTEXT` として `SESSION_TRANSCRIPT` 後に置く。candidate は引き続き `VOLATILE_RECALL` として
後ろに置く。OpenAI messages 変換では `RUNTIME_CONTEXT` と `VOLATILE_RECALL` を最後の user message に
追記し、過去の role message 列と system prefix を汚さない。

### prompt cache shape はログで見えるようにする
`prompt_cache_shape()` は system / instruction / transcript / runtime context / volatile recall の
chars と hash、transcript turn 数を返す。hot-path の `llm_prompt` event と `[tomoko:llm] prompt_send`
にはこの shape を出す。今後 dflash の `prefix cache hit` / `prefill_tokens_saved` / `evictions` と
prompt section の揺れを同じ run で比較できる。

## 2026-06-20 セッション27 確定した判断

### cache smoke は dflash counter と prompt cache shape を同時に読む
2026-06-20 21:38 の `make v2-five-turn-smoke` では avg first audio 1393.7ms / p95 2179.2ms だったが、
dflash 26B の prefix cache は 5 request 中 2 hit 相当、3 miss 相当だった。
`llm_prompt.cache_shape` では `system_hash` は全 turn で安定していたため、stable system prefix の
分離は効いている。一方で turn 3 以降は partial concise instruction hash に切り替わり、
dflash 側も `entries=8/8` のまま eviction が増え続けた。

このため、cache 改善の次手は人格・履歴・直近文脈を削ることではなく、通常応答と partial 応答で
system/instruction prefix を分けすぎないこと、また dflash cache 容量・保持状況を同じ smoke window で
確認することを優先する。`RUNTIME_CONTEXT` / `VOLATILE_RECALL` は今回 0 chars だったので、
実 context 入り scenario で別途再測する。

## 2026-06-20 セッション28 確定した判断

### partial でも INSTRUCTION は変えない
partial STT 由来の prompt だけ `INSTRUCTION` に「短く一文で返す」を足すと、system/instruction prefix が
早い位置で分岐し、dflash prefix cache の系列を割る。partial でも final でも
`INSTRUCTION:\n次のtomoko発話だけ返す。` に統一する。

2026-06-20 21:54 の変更後 `make v2-five-turn-smoke` では全 turn の `instruction_hash` が
`770c5bb7cb` に揃い、dflash 26B は 5 request 全てで `prefix cache hit` を出した。
miss counter は増えず、`prefill_tokens_saved` は 332 から 568 に増えた。

### smoke artifact には raw prompt と sent messages を両方残す
`llm_prompt.prompt_text` は Tomoko 内部の raw prompt であり、dflash へは OpenAI chat messages に
分解して送る。cache 調査では raw prompt だけだと判断を誤るため、`llm_prompt.sent_messages` に
実際に送る role/content 配列を残す。

### dflash cache 自体は同一 prompt と append prompt を保持できる
2026-06-20 21:53 の direct dflash probe では、ユニーク prompt 初回は miss、同一 prompt 2 回目は
`prefix cache hit 72/76`、prompt+append は `prefix cache hit 72/98` だった。従って、今回の
会話 smoke で cache rate が下がる主因は dflash が保持できないことではなく、partial/final 経路で
prompt/messages の prefix が揺れることにある。

## 2026-06-20 セッション31 確定した判断

### append_after_current dedupe suppress は Tomoko output active 中に限定する
`append_after_current` dedupe guard は、duplicate score が高くても
`tomoko_speaking` と `speech_queue_active` がどちらも false の idle 状態では suppress しない。
Tomoko が話している、または speech queue が active な時だけ LLM/TTS の重複追撃を落とす。

これにより無音・待機中に人間が自然に言い直した non-null final 発話は、
モデルが duplicate と見ても会話推論へ進める。guard の目的は「発話中/キュー中の append 重複抑制」であり、
自然発話の訂正や言い直しを広く削ることではない。

## 2026-06-20 セッション32 気づき

### VAP/MaAI materials WS は runtime 起動時だけ live になる
`make run` / `tmux-runtime` は tomoko realtime `:8765` を hot-path より先に起動し、
hot-path window に `TOMOKO_INTERNAL_WS_URL=ws://127.0.0.1:8765/internal/hot-path` を渡す。
hot-path `/ws` は audio chunk ごとに `TurnMaterialAggregator` で 200ms `TurnMaterials` を作り、
in-process direct core に反映しつつ internal WS client へ submit する。

2026-06-20 23:25 JST 時点では tmux runtime と `:8000` / `:8765` の listener が無かったため、
live 通信は動いていなかった。2026-06-20 15:40 の live smoke では `_docs/latency.md` に
`turn_materials` internal WS 受信が記録されているが、実 MaAI/VAP yield 値は無く
`p_yielding=None` だった。つまり経路は存在するが、今の live 状態確認では runtime 起動が前提になる。

### partial STT DB bridge は local DB で p95 約 1ms
local PostgreSQL に対して `INSERT v2_stt_observations(partial) -> v2_notify_id('v2_stt_observation') ->
LISTEN receive -> SELECT row` を 300 samples / 20 warmup で測った。
full insert+notify+select は avg 0.858ms / p50 0.810ms / p95 1.049ms / p99 1.642ms。
内訳平均は insert 0.367ms、notify execute 0.225ms、notify execute done から受信まで 0.052ms、
受信後 select 0.214ms。DB split fake smoke の transcript->order は既存 artifact 群で
0.055ms〜0.190ms だが、これは client event 上の粗い差分であり、DB microbench の方が
insert/select/notify の実コストを見る値として扱う。

ただしこれは DB component の partial row microbench であり、2026-06-20 23:30 時点の
`HotPathDbSplitConversation` の実 `/ws` DB split 経路は `final_observation` を選んで
`v2_stt_observation` NOTIFY している。schema と tomoko DB worker は partial row を処理できるが、
live hot-path DB split が partial を DB に流しているとは扱わない。

## 2026-06-20 セッション33 確定した判断

### hot conversation control は WS origin に寄せる
hot-path と Tomoko process の会話制御線は、DB `LISTEN/NOTIFY` RPC ではなく
internal WebSocket を origin にする。hot-path は `TOMOKO_V2_WS_SPLIT=1` で
`RemoteTomokoWsCore` を使い、`stt_observation`、latest `turn_materials`、
`playback_state` を Tomoko realtime `/internal/hot-path` に送る。
Tomoko realtime は同じ WS 境界で `speech_order`、`cancel_order`、各種 ack を返し、
hot-path は `SpeechOrderExecutor` で TTS/audio だけを実行する。

DB は hot control plane ではなく、Tomoko process 側の best-effort audit / replay store として残す。
Tomoko realtime は observation / saturation / scheduler decision / prompt request / speech-order /
utterance を保存できるが、保存失敗は会話 WS を落とさず `persist_failed` に閉じ込める。
`TOMOKO_V2_DB_SPLIT=1` の旧 DB split path は fallback / 比較用として残す。

fake-runtime process smoke では、hot-path と Tomoko realtime を別 uvicorn process で起動し、
`hot-path STT -> internal WS -> Tomoko -> speech_order over WS -> hot-path TTS` が total 46.0ms で通った。
同じ fake control-plane の DB split artifact は total 60.5ms。これは実 STT/LLM/TTS ではなく
制御線 smoke の比較として扱う。

## 2026-06-20 セッション34 確定した判断

### WS-origin control plane は `make run` 実 runtime でも成立する
`make run` で dflash `:8081/:8082`、VOICEVOX `:50122`、Tomoko realtime `:8765`、
hot-path `:8000` を起動し、`make v2-say-latency-smoke` と `make v2-five-turn-smoke` を通した。
hot-path は `TOMOKO_V2_WS_SPLIT=1` で起動し、Tomoko realtime log には `/internal/hot-path`
接続、`turn_materials`、partial/final `stt_observation`、`speech_order_created` が出た。
direct probe では `playback_state_ack` も返った。

単発 smoke は artifact `logs/say-latency-20260620-234757.json`、voice-end to first audio 3572.2ms、
transcript `智子短く返事して`、reply `了解。`。5 turn smoke は
`logs/five-turn-smoke-20260620-234842.json`、avg first audio 1667.2ms / p95 2130.4ms。
hot-path / Tomoko panes に `ERROR` / `Traceback` / `persist_failed` は出なかった。

Tomoko process 側 DB 保存も動作しており、直近 smoke 後の 10 分 window では
`v2_stt_observations=22`、`v2_semantic_saturation_observations=22`、
`v2_speech_scheduler_decisions=22`、`v2_speech_orders=12`、`v2_prompt_requests=12`、
`v2_utterances=20` が保存されていた。

注意点として、5 turn smoke の 5 turn 目は partial で speech-order が出た直後に smoke が
`prompt_complete` で切断したため artifact 上の `final_transcript` は null になる。
これは今回の WS-origin 変更によるクラッシュではなく、partial early-start と smoke 終了条件の組み合わせとして扱う。

## 2026-06-20 セッション35 確定した判断

### runtime server は LAN 前提で `0.0.0.0` に bind する
LAN 内の複数マシンで hot-path / Tomoko realtime / dflash / VOICEVOX を分けて動かせるよう、
`make run` 系の server listen address は既定で `0.0.0.0` にする。

ただし `0.0.0.0` は listen address であり connect URL ではない。
Tomoko internal WS、dflash、VOICEVOX への接続先は `*_CONNECT_HOST` または既存 URL 変数で分け、
同一マシン構成の既定接続先は `127.0.0.1` のままにする。
別マシンへ分離する場合は `TOMOKO_INTERNAL_WS_HOST` / `TOMOKO_V2_LLM_URL` /
`TOMOKO_V2_LLM_READY_URLS` / `TOMOKO_V2_VOICEVOX_URL` /
`TOMOKO_V2_VOICEVOX_READY_URL` を LAN IP へ上書きする。

## 2026-06-21 セッション2 気づき

### MaAI `bc_2type` は `p_yielding` ではなく react/emo を返す
AudioWorklet 由来の `/ws` audio chunk は 128 samples で届く一方、MaAI の `MaaiInput.Chunk`
は 160 samples 単位なので、hot-path 側で複数 chunk をバッファしてから `put_chunk()` する。
この修正後の `make v2-say-latency-smoke` では `maai_result` が連続して出るようになった。

ただし現行の MaAI detector は `mode="bc_2type"` で起動しており、result の raw keys は
`p_bc_react` / `p_bc_emo` / `t` / `x1` / `x2` で、`p_yielding` は含まれない。
そのため `pressure_dialogue_turn_opportunity` の `p_yielding` 成分はまだ実 VAP yielding ではなく、
silence fallback 由来である。一方で `p_bc_react` / `p_bc_emo` は
`NaturalSpeechPressureModel` に入り、`pressure_natural_backchannel_desire` /
`pressure_natural_light_reaction_desire` として scheduler score に反映される。

実 `make run` smoke artifact は `logs/say-latency-20260621-104751.json`。
DB の `v2_speech_scheduler_decisions` では 10:47:55/10:47:57 JST の decision に
`pressure_natural_backchannel_desire=0.225585.../0.299129...`、
`pressure_natural_light_reaction_desire=0.162144.../0.230968...` が保存された。
本当に `p_yielding` を score に入れるには、`bc_2type` とは別に `vap` mode の result
(`p_now` / `p_future`) を取得する設計にする。

## 2026-06-22 セッション1 確定した判断

### MaAI は `bc_2type` と `vap` の dual lane で起動する
v2 hot-path の MaAI は、相槌素材用の `mode="bc_2type"` と turn-yielding 用の
`mode="vap"` を同時に起動する。AudioWorklet の 128 sample chunk は hot-path で
160 sample MaAI frame にバッファし、同じ user/silence frame を両 lane に fan-out する。

`bc_2type` は `p_bc_react` / `p_bc_emo` を返し、`vap` は `p_future[1]` を
`p_yielding` として `TurnMaterials` に渡す。両 result は別タイミングで届くため、
`TurnMaterialAggregator.observe_maai_result()` は result に含まれる field だけを更新し、
未指定 field を `None` で上書きしない。

実 `make run` smoke artifact は `logs/say-latency-20260622-041122.json`。
`logs/server-debug.log` で `maai_vap_started`、連続する `maai_vap_result p_yielding=...`、
`turn_materials_snapshot ... p_yielding=...` を確認した。DB では 04:11 JST の
`v2_stt_observations` に partial `p_yielding=0.2587`、final `p_yielding=0.2306` が保存され、
同じ observation に紐づく scheduler decision で score / score_breakdown が作られた。

注意点として、この環境では `my-ime-server-1` Docker container が `127.0.0.1:8765` を listen
していたため、Tomoko realtime を既定 8765 で起動すると hot-path の internal WS connect が
別プロセスへ誤接続して `InvalidMessage` になった。検証では `TOMOKO_INTERNAL_WS_PORT=8766 make run`
で回避した。これは設計判断というよりローカル環境衝突として扱う。

## 2026-06-27 セッション1 気づき

### `.git` 肥大化は到達不能 pack と現作業ツリーの ignored model を分けて見る
2026-06-27 時点で `.git` は 25G だったが、`git rev-list --objects --all` で見える到達可能 blob は
最大約 32MB、合計も数百 MB 規模だった。肥大化の主因は現在 refs から到達しない古い pack であり、
`git reflog expire --expire=now --expire-unreachable=now --all && git gc --prune=now` により
`.git` は 9.3M、pack は 9.11 MiB まで縮小した。

一方で checkout 全体がまだ 24G ある主因は `.git` ではなく、ignored な
`v1/loras/lora/fused_model/*.safetensors`、`v1/loras/lora/adapters/*.safetensors`、
`.venv/`、`models/`、`logs/` の実ファイルである。`v1/loras` で Git 管理されているのは
README / script 類だけで、safetensors は `.gitignore` 対象だった。

## 2026-07-04 セッション3 確定した判断

### latency suite は no-audio を必ず失敗として扱う
`scripts/v2_latency_suite.py` は `runs_no_audio == 0` を必須ターゲットにする。
first audio p50/p95 が計算できない no-audio run を pass 扱いにすると、runtime reload 中の無音失敗を
latency 改善と誤認するため。

### request-complete partial は初回 confirmation で通す
partial STT が `教えて` / `してください` / `お願い` / `?` など request 完了らしい suffix で終わる場合は、
2 回目の類似 partial を待たずに partial speech-order を許可する。
`これは誰` のような未完了 partial は従来どおり confirmation 待ちにする。

### latency suite の transcript / speech-order timing は client-observed 境界
現行 real `/ws` では、client が受け取る `transcript` / `speech_order` event は
Tomoko 側の `process_segment` が LLM/TTS 実行まで終えた後に見えることがある。
そのため suite の timing breakdown は体感境界として扱い、内部 STT/LLM/TTS stage 切り分けには
server log または別の internal timing artifact を使う。

## 2026-07-04 セッション3 気づき

### G1 の latest single-seed 失敗は STT ではなく Tomoko/LLM と TTS が主因
`latency_stage` 追加後の `logs/latency-suite-20260704-010457.{json,md}` では、
final-origin first audio 7938.6ms の内訳が STT 337.3ms、Tomoko/LLM 3480.7ms、TTS 3676.3ms、
stage total 7494.4ms だった。
同じ seed を同一 runtime に繰り返し流すと session history の影響で返答が長文化し、
TTS latency をさらに悪化させる。G1 suite は session 汚染を明示するか、fresh runtime/session での測定導線が必要。

### TTS streaming は backend だけでなく hot-path result 境界を変えないと first audio に効かない
`VoicevoxChunkedTtsBackend` は async iterator だが、現行 `SpeechOrderExecutor.execute()` は
全 chunk を集め終えてから `HotPathConversationResult` を返し、その後 `_send_prompt_execution_result()` が
まとめて browser へ送る。したがって VOICEVOX の segment/chunk 設定だけでは first audio は前倒しされない。
first phrase delivery を効かせるには、speech-order event を先に送ったうえで TTS chunk を生成順に
result queue / WebSocket へ流す配線が必要。

### latency suite は測定前に Tomoko conversation state を reset する
同じ seed を同一 runtime に繰り返し流すと、session history の影響で Tomoko 返答が長文化する。
2026-07-04 セッション3で `/ws` の `latency_control/reset_conversation` から internal WS の
`reset_conversation` へ転送する導線を追加し、`scripts/v2_latency_suite.py` は各 run 前に既定で reset する。
reset 後 artifact `logs/latency-suite-20260704-011004.{json,md}` では final-origin 6310.7ms、
STT 553.3ms、Tomoko/LLM 3078.5ms、TTS 2232.8ms、stage total 5864.7ms。
履歴汚染は抑えられたが、S16 目標にはまだ届かない。

## 2026-07-04 セッション4 確定した判断

### audio conversation の speech-order TTS は sender 側で実行する
`HotPathAudioConversation` は `/ws` audio conversation では speech-order を作った時点で
`HotPathConversationResult.deferred_tts_orders` に渡し、TTS 完了を待たずに result queue へ返す。
`_send_audio_conversation_result()` は `speech_order` event を先に送り、その後
`SpeechOrderExecutor.execute_stream()` の chunk callback から binary audio を逐次送る。

このため audio conversation の deferred path では `tts_result` は binary audio 送信後の summary event になる。
text prompt path (`prompt` / `text_prompt` / `user_text`) は従来どおり `tts_result` → binary audio の順序を維持する。
`latency_stage.stage_timings_ms["tts_ms"] == 0.0` は「TTS が無くなった」ではなく、
TTS が hot-path result 生成後の sender 境界へ移ったことを意味する。

### voice output は first sentence を最短単位にする
G1 latency のため、Tomoko LLM stream は最初の完全文(`。！？!?`)で打ち切り、
speech-order text は first sentence のみとする。
さらに speech-order text は VOICEVOX 合成前に文単位へ分割し、先頭短文から順に合成・送信する。
この方針により、reset 後 1-seed は 6310.7ms -> deferred-only 5076.1ms ->
sentence split 3425.6ms -> first-sentence cutoff 2627.4ms まで改善した。

最新 3-seed artifact `logs/latency-suite-20260704-013026.{json,md}` は no-audio 0、
partial-origin 0/3、final-origin p50 3122.4ms / p95 5143.9ms、stage total p50 1470.3ms / p95 2679.2ms。
S16 は未達で、残る主因は partial-origin が出ていないことと、final-origin では
voice_end→final/order の待ち + first short TTS がまだ積み上がること。

### RemoteTomokoWsCore は stale internal WS を捨てて一度 reconnect する
Tomoko realtime を再起動した後、hot-path が古い internal WS を握り続けると
`ConnectionClosedError` で public `/ws` handler 全体が落ち、latency suite が no-audio になる。
`RemoteTomokoWsCore` は reset / observation request で `websockets.ConnectionClosed` を受けたら
cached socket を close/drop し、1 回だけ reconnect して同じ request をやり直す。

## 2026-07-04 セッション4 気づき

### 次の G1 レバーは final-origin shaving ではなく安全な partial-origin audio
first-sentence cutoff 後、1-seed artifact `logs/latency-suite-20260704-012939.{json,md}` では
STT 334.3ms、Tomoko/LLM 742.4ms、order→first audio 1114.1ms まで縮んだ。
一方で real partial は `今日` / `今日の予定` のような未完了名詞句で止まり、
full reply を出すには危険なため partial-origin は 0/3 のまま。

次は partial `今日の予定` のような未完了入力に full answer を出すのではなく、
短い acknowledgement / filler の speech-order を出し、final で本回答へ replace する設計が候補。
これなら first audio 目標に効きつつ、final STT divergence の上書き設計とも整合する。

## 2026-07-04 セッション4 追加の確定した判断

### incomplete topic partial は full answer ではなく acknowledgement を出す
`今日の予定` / `今日の天気は...` のような未完了 topic partial は、full reply ではなく
`うん、聞いてるよ。` の短い acknowledgement speech-order を出す。
active partial ack は final reconcile の根拠にしないため、final STT が来たら本回答で replace できる。
一方、`これは誰` のような曖昧 partial は従来どおり partial start gate の confirmation 待ちにする。

この方針で `logs/latency-suite-20260704-015746.{json,md}`、
`logs/latency-suite-20260704-015947.{json,md}`、
`logs/latency-suite-20260704-020002.{json,md}` が 3 回連続 pass した。
最新値は partial-origin 2/3、final-origin 約 1.32s、partial-origin p50 約 0.38s。

### clock question は LLM ではなく local system time の direct speech にする
`今何時` / `今いつ` / `今の時間` 系は LLM に渡さず、local system time から
`今はH時MM分だよ。` を生成する。
G1 測定中に LLM が古い日付・時刻を返す問題があり、これは world knowledge ではなく
local runtime fact として deterministic に扱うほうが正しい。

### latency reset は hot-path VAD/STT も reset する
`latency_control/reset_conversation` は Tomoko conversation state だけでなく、
hot-path の VAD buffer、streaming STT stream、active trace、hot-path 短期履歴も reset する。
first-audio で測定を切った後、Apple Speech の遅延 final/partial が次 run に混ざると
latency suite が誤って pass/fail するため、測定境界では物理入力側も含めて reset する。

## 2026-07-04 セッション4 G5 の確定した判断

### p_yielding 欠損は full yielding とみなさない
`TurnMaterials.p_yielding is None` は「VAP が yielding を出した」ではなく「値がない」として扱う。
conversation 境界で欠損を `1.0` に補完すると、`turn_opportunity` が VAP 由来なのか
silence fallback 由来なのかを score_breakdown / artifact で切り分けられないため。

### turn_opportunity は yielding と silence の内訳を score_breakdown に残す
`DialogueTurnPressure` は `turn_opportunity` の合成値だけでなく、
`yielding_opportunity` と `silence_opportunity` も保持する。
Tomoko の `score_breakdown` には
`pressure_dialogue_yielding_opportunity`、
`pressure_dialogue_silence_opportunity`、
`pressure_dialogue_turn_opportunity_from_yielding`、
`pressure_dialogue_turn_opportunity_from_silence`
を出し、DB / scenario artifact からどちらの入力が効いたかを確認できるようにする。

### G5 regression は scripted p_yielding で deterministic に固定する
say 音声だけでは VAP の `p_yielding` が安定して立たない可能性があるため、
G5 の自動回帰は `TOMOKO_V2_FAKE_STT_EVENTS` に `p_yielding` を明示する fake replay で固定する。
`logs/scenario-vap-yielding-opportunity-20260704-021449.json` では、
partial decision が `yielding=0.92 / silence=0.0 / from_yielding=1.0`、
final decision が `yielding=0.0 / silence=1.0 / from_silence=1.0` として同一 artifact 内で PASS した。

## 2026-07-04 セッション4 G6 の確定した判断

### motivation は score 加点ではなく gate threshold shift として扱う
`MotivationPressure.threshold_shift` は `LlmFireGate` / `SpeechEmissionGate` の score に直接加点しない。
会話熱量、話題継続度、personality から作った shift で fire / emit / append / replace の閾値を下げ、
`score_breakdown` には `motivation_threshold_shift` と
`pressure_motivation_threshold_shift` を観測用に残す。
これにより「なぜ前のめりになったか」を DB / scenario artifact から読める一方、
pressure score そのものの意味を崩さない。

### high motivation の低 semantic partial は短い撤回可能 interjection にする
semantic saturation が低く、request-complete ではない partial でも、
`threshold_shift >= 0.12` かつ `p_yielding >= 0.7` なら full answer ではなく
固定の短い interjection `いや、それってさ。` を出す。
この speech-order は active partial reply として final reconcile しないため、
final STT が来たら通常回答で replace できる。

`logs/scenario-motivation-interjection-high-20260704-023202.json` では
text / reason / 文字数 / `motivation_interjection=1.0` /
`pressure_motivation_threshold_shift>=0.12` が PASS した。
unit では partial 直後に LLM が呼ばれず、final で通常回答に replace されることも固定している。

### fake_personality は A/B replay 専用の検証 fixture として使う
`TOMOKO_V2_FAKE_PERSONALITY` は fake runtime scenario のための fixture であり、
runtime hot-path へ personality や motivation を渡す設計ではない。
hot-path は引き続き speech-order を物理実行するだけで、motivation の判断は Tomoko process 側に閉じる。

G6 の A/B regression は `logs/scenario-motivation-threshold-high-20260704-023203.json` と
`logs/scenario-motivation-threshold-low-20260704-023204.json` で確認した。
high profile は conversation heat 1.0 時に `threshold_shift >= 0.1`、
low profile は同条件で `threshold_shift <= 0.02` を満たす。

## 2026-07-04 セッション4 Step 7 user-status の確定した判断

### user presence は WorldMaterials から WorldPressure へ落として観測する
`UserStatusObservation.present` は `WorldMaterials.user_present` に写し、
`WorldPressure.user_presence` / `user_absence` として score_breakdown に残す。
user absent のときは world/candidate/calendar 由来の自発的な発話材料を届けないため、
`WorldPressureModel` は `importance` / `urgency` / `deliverability` を 0 にする。
一方で user が実際に STT で話しかけた本返答は DialoguePressure 側で成立するため、
presence absent だけで直接返答までは止めない。

### user absent では calendar followup append を出さない
calendar append は user への自発的な付加通知なので、
`world_materials.user_present is False` のとき `_maybe_calendar_followup()` は何も出さない。
`logs/scenario-user-status-absent-pressure-20260704-024009.json` では、
同じ近接 calendar 条件でも speech_order が main reply 1 件だけになり、
`pressure_world_user_absence=1.0` / `pressure_world_urgency=0.0` として PASS した。

### fake_user_status は周辺プロセス結線の自動回帰 fixture
`TOMOKO_V2_FAKE_USER_STATUS` と scenario JSON の `fake_user_status` は、
user-status process から Tomoko process へ材料が届いた後の挙動を fake runtime で固定するための fixture。
runtime 経路としては internal WS の `user_status` event / `user_status_ack` を追加し、
Tomoko core の `update_user_status()` が prompt snapshot と world materials の両方を更新する。

## 2026-07-04 セッション4 Step 7 summary の確定した判断

### SessionSummary は summary と embedding を同じ id で冪等 insert する
`SessionSummary` は `v2_session_summaries` に `id=session_summary.id` で保存し、
対応する `v2_summary_embeddings` も同じ UUID を row id として使う。
テーブルは別なので id の共有は問題なく、同じ summary を再処理しても
`ON CONFLICT (id) DO NOTHING` で重複 embedding を増やさない。

`summary_text` は `keyword: conclusion` の可読テキストとして保存する。
embedding は `SessionSummary.embedding` を `double precision[]` 用の list param に変換する。
unit では `insert_session_summary_sql()` / `insert_summary_embedding_sql()` を固定し、
integration schema test には `v2_session_summaries` と `v2_summary_embeddings` の insert を追加した。

## 2026-07-04 セッション4 Step 7 candidate の確定した判断

### candidate は prompt context と WorldPressure の両方へ流す
think-process が積む `CandidateRecord` は、Tomoko core の `candidate_provider` から取得し、
active candidate だけを `ContextSnapshot.candidates` に入れる。
prompt では既存の `VOLATILE_RECALL` (`candidate[source:key]=text`) として使い、
発話判断では最大 `candidate_score` を `WorldMaterials.candidate_pressure` に落とす。

`WorldPressureModel` は `candidate_pressure` を importance / urgency / relevance に反映し、
`pressure_world_candidate_pressure` を score_breakdown に残す。
fake runtime では `TOMOKO_V2_FAKE_CANDIDATES` / scenario `fake_candidates` で注入し、
`logs/scenario-candidate-pressure-context-20260704-024648.json` で
`pressure_world_candidate_pressure=0.9` を確認した。

残る Step 7 の中心は、speech input なしの `initiative_tick` 相当を Tomoko process が所有し、
silence + candidate pressure から speech-order を作る replay を追加すること。

## 2026-07-04 セッション4 Step 7 candidate initiative の確定した判断

### 自発発話 tick は Tomoko process が所有し既存 WebSocket event として流す
speech input なしの自発発話開始は、hot-path やブラウザの状態判定ではなく
Tomoko process の `TomokoConversationCore.handle_initiative_tick()` が所有する。
public `/ws` と internal `/internal/hot-path` には `initiative_tick` event type だけを追加し、
REST endpoint は増やさない。

`handle_initiative_tick()` は active candidate、turn materials の silence / speech probability、
user presence、current speech/playback を見て、prompt 生成前に suppress できる。
発話可能な場合だけ `PromptScope.INITIATIVE` の prompt を作り、
既存の `LlmFireGate` / `SpeechEmissionGate` を通して speech-order にする。

### 無音 replay は空文字 STT final で偽装しない
`scripts/v2_scenario_replay.py` は `steps[].event` を受け付ける。
これにより `{"type":"initiative_tick"}` を直接 public `/ws` に送れるため、
無音を `text=""` の STT observation として偽装しない。
artifact では transcript が 0 件のまま scheduler_decision / speech_order / TTS を確認する。

`logs/scenario-candidate-initiative-silence-20260704-025854.json` では
`pressure_world_candidate_pressure>=0.9`、`pressure_world_deliverability>=0.9`、
speech_order reason `candidate pressure initiative tick`、binary_audio / prompt_complete が PASS した。
`logs/scenario-candidate-initiative-absent-suppressed-20260704-025853.json` では
user absent により `pressure_world_user_absence=1.0`、speech_order 0、prompt_complete 0 が PASS した。

## 2026-07-04 セッション4 Step 7 think-process candidate helper の確定した判断

### think-process の最小責務は CandidateSeed への正規化に置く
Step 7 の実体化では、summary / calendar / world info をいきなり Tomoko に直結せず、
まず `CandidateSeed` に正規化してから `CandidateStore.upsert_seed()` で active
`CandidateRecord` にする。
`server.think.main.build_candidates()` は calendar items、`SessionSummary`、world info seeds を受け取り、
重複 source/source_key を store 境界で dedupe する。

### world info は既存 filter を通ってから candidate 化する
`world_info_seed()` は `server.info.main.should_candidate_from_world()` を通す。
confidence 低下、stale、sensitive、private、do_not_speak のいずれかに該当する情報は
candidate seed を返さない。
この filter は world information の安全弁であり、Tomoko の発話 gate に渡す前の think-process 側責務に置く。

### fake calendar/world info は replay 用の candidate 供給 fixture
fake runtime では `TOMOKO_V2_FAKE_CALENDAR` と `TOMOKO_V2_FAKE_WORLD_INFO` からも
`build_candidates()` 経由で candidate provider に active candidate を積む。
明示 `TOMOKO_V2_FAKE_CANDIDATES` は従来通り直接注入として残す。

`logs/scenario-calendar-initiative-silence-20260704-030553.json` では
明示 fake candidate なしで calendar material から `candidate_source=calendar` の
initiative speech-order が出た。
`logs/scenario-world-info-initiative-silence-20260704-030610.json` では
world info から `candidate_source=world` の initiative speech-order が出た。
どちらも transcript 0 のまま public `/ws` -> internal WS -> Tomoko gate -> TTS まで PASS した。

## 2026-07-04 セッション4 Step 7 candidate DB bridge の確定した判断

### CandidateRecord は source/source_key で冪等 upsert する
think-process が作った `CandidateRecord` は `v2_candidates` に保存し、
`source/source_key` の一意性で同じ材料を更新する。
`insert_candidate_sql()` は priority / urgency / intrusion / maturity /
candidate_score / lifecycle / context_tags をまとめて upsert し、
`candidate_from_row()` は DB row を同じ DTO に戻す。

この境界は Tomoko core の `candidate_provider` が DB 由来 candidates を読む前段であり、
fake runtime の `TOMOKO_V2_FAKE_*` 経路とは分けて検証する。
unit では upsert SQL と DTO round-trip を固定し、integration schema test では
`v2_candidates` への insert/upsert を追加した。

### DB split の session reuse は last_activity_at 更新で表す
open session が idle gap 未満で再利用されるとき、
`assign_conversation_session()` は新規 session を作らず
`UPDATE v2_conversation_sessions SET last_activity_at = ... WHERE id = ...` だけを出す。
この経路は連続会話の通常 path なので、unit regression test で直接固定する。

## 2026-07-04 セッション4 Step 7 candidate DB read bridge の確定した判断

### Tomoko realtime は active candidates を TTL 付きで読む
`v2_candidates` に積まれた active candidate は、Tomoko realtime が
`TOMOKO_V2_DB_CANDIDATES=1` のときだけ読む。
読み込みは `select_active_candidates_sql()` / `load_active_candidates()` を通し、
`lifecycle='active'` かつ `expires_at IS NULL OR expires_at > now()` の rows を
`candidate_score DESC, urgency DESC, priority DESC, created_at DESC` の順で取得する。

取得結果は `TomokoConversationCore.update_candidate_records()` に渡し、
既存の fake/env `candidate_provider` と同じ `_candidate_items()` 経路に合流させる。
これにより DB 由来 candidate も prompt snapshot の `VOLATILE_RECALL` と
`WorldPressure.candidate_pressure` に反映される。

### DB candidate 読み込み失敗は会話を止めない
DB candidate refresh は補助材料なので、失敗しても stt observation / initiative tick の処理は止めない。
失敗時は `candidate_refresh_failed` を console log に残し、直前に読めた candidate records は
core 側の通常状態として保持される。
fake runtime scenario では `TOMOKO_V2_DB_CANDIDATES` を渡さないため、DB なしでも replay suite が通る。

## 2026-07-04 セッション4 Step 7 candidate DB write bridge の確定した判断

### think-process は summary/world rows から v2_candidates を materialize する
think-process の DB 実体化は `materialize_candidates_from_db()` に置く。
この関数は `v2_session_summaries` と `v2_world_interpretations` を読み、
既存の `build_candidates()` で `CandidateRecord` に正規化してから
`insert_candidate_sql()` で `v2_candidates` へ `source/source_key` upsert する。

session summary は `summary_memory_seed()` に流し、world interpretation は
`world_seed_from_interpretation_row()` から `world_info_seed()` に渡す。
world row の `flags` は stale / sensitive / private / do_not_speak filter として扱い、
candidate 化してよい情報だけを `v2_candidates` に積む。

### runtime の think heartbeat は candidate materializer tick を持つ
`server.runtime process think` は heartbeat ごとに candidate materializer tick を実行する。
DB 接続や materialize に失敗しても process は止めず、
`think_candidate_tick_failed` をログに残して次の heartbeat を待つ。
`_database_ready()` は `default_dsn()` を見るため、`TOMOKO_DATABASE_URL` 未設定でも
make runtime の既定 DB と readiness / think tick が揃う。

## 2026-07-04 セッション4 Step 7 summary runner の確定した判断

### summary-process は ended session から索引 summary を作る
summary-process の DB 実体化は `materialize_summaries_from_db()` に置く。
対象は `ended_at IS NOT NULL` かつ `v2_session_summaries` がまだ存在しない
`v2_conversation_sessions` で、session 内の `v2_utterances.text` を時系列に読んで
`summarize_session()` に渡す。

生成した `SessionSummary` は `insert_session_summary_sql()` と
`insert_summary_embedding_sql()` で保存する。
ここで作る summary は原本ではなく検索・候補化のための索引であり、
think-process が後段で `summary_memory_seed()` に流して candidate 化する。

### runtime の summary heartbeat は失敗しても process を止めない
`server.runtime process summary` は heartbeat ごとに summary materializer tick を実行する。
DB 接続や materialize に失敗しても process は止めず、
`summary_tick_failed` をログに残して次の heartbeat を待つ。

## 2026-07-04 セッション4 Step 7 info-aquire fixture DB の確定した判断

### fake calendar/world fixture は v2_world_* rows に idempotent upsert する
info-aquire の DB 実体化では、`TOMOKO_V2_FAKE_CALENDAR` と
`TOMOKO_V2_FAKE_WORLD_INFO` を直接 Tomoko に渡すのではなく、
まず `v2_world_documents` / `v2_world_items` / `v2_world_interpretations` に保存する。
document / item / interpretation の id は source/source_key から deterministic UUID で作り、
heartbeat が同じ fixture を何度処理しても rows が増殖しないようにする。

calendar fixture は source `calendar` の world rows として保存し、
world info fixture は source `world` の world rows として保存する。
後段の think-process は `v2_world_interpretations` を読み、
`world_info_seed()` の confidence / flags filter を通して candidate 化する。

### runtime の info heartbeat は fixture materializer tick を持つ
`server.runtime process info` は heartbeat ごとに fixture materializer tick を実行する。
DB 接続や materialize に失敗しても process は止めず、
`info_tick_failed` をログに残して次の heartbeat を待つ。

## 2026-07-04 セッション4 Step 7 user-status OCR artifact の確定した判断

### OCR fixture path から UserStatusObservation までを一つの境界にする
user-status の fixture 画像 integration では、画像 path と OS metadata から
`UserStatusObservation` を作る境界を `observation_from_ocr_artifact()` に置く。
この関数は `ocr_text(path)` を呼び、既存の `build_user_status_observation()` へ渡す。

artifact path は DTO の `artifact_path` に残し、OCR 結果は `visible_text` と activity inference に使う。
unit では `ocr_text()` を差し替えて path -> OCR text -> `coding_or_terminal` 判定を固定した。
残る実機側の確認は、Vision OCR / tesseract sidecar を使う fixture image integration で行う。

## 2026-07-04 セッション5 Step 7 integration 完了の確定した判断

### make test-integration は local DB と実 OCR fixture を実走する
`TEST_DATABASE_URL ?= postgresql://tomoko:tomoko@localhost:5432/tomoko` を Makefile に置き、
`make test-integration` は既定で local Postgres に対して integration tests を実行する。
`make db-up` 済みの local container では、
`tests/integration/test_v2_db_schema.py` の DB materializer chain と
`tests/integration/test_v2_user_status_ocr.py` の OCR fixture image test が実走し、
3 passed になった。

### Step 7 の周辺プロセス chain は unit と integration の両方で閉じた
Step 7 の fake data chain は、info fixture -> `v2_world_*` rows ->
think materializer -> `v2_candidates` -> Tomoko realtime candidate refresh ->
initiative speech-order まで unit / replay / DB integration で確認した。
user-status は fake user_status WS と実 OCR fixture image の両方で確認し、
absent 時の initiative suppress も replay で固定した。

## 2026-07-04 セッション5 Step 8 AttentionMode の確定した判断

### AttentionMode は tomoko-process 所有の threshold profile として扱う
会話中か、聞き取りに戻っているかの判定は hot-path / client に置かない。
`TomokoConversationCore.attention_mode` が `conversation` / `ambient` profile を所有し、
wake cue で conversation、長い silence gap または stop intent で ambient に戻る。

この profile は新しいターン状態機械ではなく、発話 gate の閾値プロファイルである。
ambient profile では wake ではない低 semantic saturation 発話を suppress し、
再 wake で conversation profile に戻す。

### hot-path へ渡すのは観測可能な profile breakdown だけにする
hot-path は `attention_mode` を判断せず、従来通り `speech_order` / `cancel_order` を物理実行する。
tomoko-process は `score_breakdown` に `attention_mode_conversation` /
`attention_mode_ambient` / `attention_ambient_min_saturation` を残し、
artifact から profile 遷移を検証できるようにする。

`logs/scenario-attention-mode-idle-wake-20260704-034352.json` では
wake -> response -> long silence -> low saturation monologue suppress -> wake recovery が PASS した。
`make v2-scenario-suite` でも AttentionMode scenario を含めて PASS している。

## 2026-07-04 セッション5 Step 9 回帰ゲートと運用の確定した判断

### internal WS port guard は uvicorn 起動前に止める
`make v2-tomoko` は uvicorn を起動する前に
`server.runtime guard-internal-ws-port` を実行する。
既定 port 8765 が空いていれば `[tomoko:runtime] internal_ws_port_available ...` を出す。
他プロセスが listen していれば、PID / command / address を含む listener 行と、
`TOMOKO_INTERNAL_WS_PORT=<free-port>` の案内を出して非 0 exit する。

既定 port は変えない。
競合が起きたときだけ、作業者が明示的に `TOMOKO_INTERNAL_WS_PORT` を変える。
これは 2026-07-04 の作業中に Tomoko の tmux window が消え、
古い uvicorn PID だけが 8765 を掴んだ状態を再発見したための運用防壁である。

### autopilot は always-on gate と runtime-ready gate を分ける
`make autopilot` は常に `make check` / `make test-integration` /
fake `v2-scenario-suite` を実行する。
その後、hot-path `/ws` が ready なら real overlap replace/stop と
full `v2-latency-suite` も実行する。
runtime が未起動なら real gate は skipped として artifact に残し、
unit / integration / fake replay の回帰ゲートは落とさない。

最新の成功 artifact は `logs/autopilot-20260704-042721.json`。
`make check` は 234 passed / 3 deselected、
`make test-integration` は 3 passed / 234 deselected、
fake scenario suite / real overlap replace / real overlap stop /
latency suite が全て exit 0 だった。

### LLM-as-judge は gate ではなく観測レイヤにする
`make v2-llm-judge` は直近 scenario artifact の transcript を
31B OpenAI-compatible endpoint に渡し、`logs/llm-judge.jsonl` に保存する。
31B が使えない場合は skipped JSONL を残すだけで、通常の regression gate は落とさない。
fenced JSON を返すモデルでも parse する。

最新実行では `logs/scenario-real-overlap-stop-20260704-042420.json` を judge し、
naturalness 1.0、duplicate/missed/awkward 0 だった。

### 実 STT 表記揺れは partial acknowledgement cue に寄せて吸収する
real latency suite では Apple Speech が `昼ごはん` を `昼ご飯`、
`おすすめ` を `お勧め`、`さっきの話...` の partial を `の話を` のように返した。
これらは full answer を早撃ちするのではなく、safe acknowledgement
`うん、聞いてるよ。` を early speech-order として出す cue に寄せる。

`話` は partial acknowledgement topic cue には入れるが、
ambient attention の final request cue には入れない。
これにより、独り言としての「話」を conversation 復帰扱いしすぎず、
実測で遅かった partial だけを前倒しできる。

### ambient でも request-like final は会話復帰させる
AttentionMode ambient は低 saturation の独り言を suppress するが、
`今日やるべきことを3つ挙げて` のような request-like final は
conversation profile に戻して応答する。
`やるべき` / `挙げて` / `あげて` / `三つ` / `3つ` は
request cue として扱う。

最新 full latency suite `logs/latency-suite-20260704-042438.json` は
`runs_no_audio=0`、final-origin p50 1342.6ms / p95 1358.7ms、
partial-origin p50 -141.4ms で PASS。
前回 no-audio になっていた `今日やるべきことを三つ挙げて` も
partial-origin で初回音声が出た。

### real scenario の fixture は latency_control 経由で注入する
fake scenario の `fake_calendar` は subprocess 起動時の環境変数で入るが、
既に起動済みの real runtime には届かない。
real runtime で一時 fixture を使う場合は、ブラウザ通常ロジックではなく
scenario / latency harness 用の `latency_control` lane を使う。

`scripts/v2_scenario_replay.py` は real mode の接続直後に
`latency_control/reset_conversation` を送り、その後必要なら
`latency_control/set_fake_calendar` を送る。
hot-path はこれを internal WS の `scenario_fixture` に変換し、
Tomoko realtime が `TomokoConversationCore.update_calendar_items_provider()` に
一時 calendar provider を入れる。

reset を先に送る理由は、同じ予定 key が前シナリオで通知済みになっていると
`_notified_calendar_keys` により append followup が dedupe されるため。
これにより `make autopilot` 内で real overlap の後に real calendar append を走らせても、
`logs/scenario-calendar-append-20260704-044302.json` が
replace_current -> append_after_current で PASS した。

### autopilot の real gate は calendar append も含める
`make autopilot` の runtime-ready real checks は
real overlap replace / real overlap stop / real calendar append / full latency suite を含む。
最新成功 artifact は `logs/autopilot-20260704-044545.json`。
`make check` は 236 passed / 3 deselected、
`make test-integration` は 3 passed / 236 deselected、
fake scenario suite、real overlap replace/stop、real calendar append、full latency が全て exit 0。

最新 full latency suite `logs/latency-suite-20260704-044312.json` は
`runs_no_audio=0`、final-origin p50 1339.7ms / p95 1343.2ms、
partial-origin p50 -163.8ms で PASS。
最新 LLM-as-judge は `logs/scenario-calendar-append-20260704-044302.json` を評価し、
naturalness 1.0、duplicate/missed/awkward 0 だった。

### DB materializer chain integration は育った local DB を前提に広めに読む
local `tomoko` DB は integration / runtime smoke を繰り返すと `v2_candidates` が育つ。
`load_active_candidates(limit=12)` のような狭い上位取得だけで
今回 materialize した summary candidate を探すと、既存 candidate に押し出されて
flaky になる。

integration test では `limit=1000` で active candidates を読み、
summary source が存在することと、world materializer 由来の雨テキストが candidate に載ることを確認する。
world interpretation の入力 `source_key` は candidate 化の際に interpretation row UUID へ変わるため、
入力 source_key の完全一致では検証しない。

## 2026-07-05 セッション2 確定した判断

### root v2 の STT 本線は WhisperKit / Argmax CLI large-v3-turbo
root v2 の default STT backend は Apple Speech ではなく
WhisperKit / Argmax CLI の `large-v3-v20240930_turbo` とする。
encoder / decoder compute units はどちらも `cpuAndNeuralEngine` を既定にする。

Apple Speech backend は削除せず、`TOMOKO_V2_STT_BACKEND=apple_speech` で戻せる
fallback として残す。

### Argmax CLI の `--stream` は hot-path 主経路に使わない
手元の `whisperkit-cli transcribe --help` では `--stream` は
CLI が microphone を直接読む経路であり、Tomoko のブラウザ `/ws` float32 chunk を受け取る経路ではない。
そのため root v2 の partial STT は、server 側で累積した発話 chunk を WAV にして
`transcribe --stream-simulated` に渡す。final STT は同じ CLI の
`transcribe --audio-path` で確定する。

この判断は `/ws` 単一路線、クライアント非ロジック、hot-path 所有の VAD/STT 境界を守るためである。

## 2026-07-09 セッション1 確定した判断

### hot-path startup readiness は main 26B LLM と VOICEVOX だけを必須にする
`make run` の hot-path window は、main 会話 LLM `:8082` と VOICEVOX `:50122` が ready なら
`/ws` server を起動する。summary/background 用 31B route `:8081` は optional readiness として
probe し、落ちていれば `[optional-missing]` を出すが startup の exit code には含めない。

2026-07-09 の実測では、`llm-31b` の dflash generation worker が
`DFlash generation worker failed to publish a complete runtime bundle within 300.0s` で落ち、
`:8081` が connection refused になった。その状態でも `:8082` / `:50122` / `:8765` は起動済みで、
hot-path `:8000` は新しい readiness で起動できた。

この判断は、hot-path が会話入口の physical audio interface であり、
summary/background LLM load の失敗でユーザーとの `/ws` 入口まで塞がないためである。

## 2026-07-09 セッション2 確定した判断

### dflash launcher は tmux window ではなく `/v1/models` readiness を真実にする
31B dflash (`:8081`) が起動しない主因は、dflash 本体が
`wait_until_ready(timeout_s=300.0)` を hard-code しており、
31B cold load が 300 秒以内に runtime bundle を publish できない場合に
process を落とすことだった。

同じ起動ログでは、次の retry が `Starting httpd at 0.0.0.0 on port 8081...`
まで進んで 31B は実際に listen した。その後さらに重複起動が走り、
`OSError: [Errno 48] Address already in use` が出ていた。

`scripts/run_llm.sh` は tmux window の有無だけではなく、実際の
`http://127.0.0.1:<port>/v1/models` readiness を見て起動済み判定する。
stale window は respawn し、既に ready な port は再起動しない。
`scripts/run_dflash_server.sh` は dflash process の non-zero exit を retry する。
