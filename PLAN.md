# PLAN.md

この PLAN は、Tomoko v2 を「計算モデルを持って会話する存在」にするための新しい実装計画である。

旧 PLAN は `PLAN.old.md` に退避した。旧 PLAN の完了済み項目は否定しない。
root v2 の bootstrapping、DTO、DB schema、runtime launcher、Apple Speech STT、Vision OCR、
dflash / VOICEVOX 接続、WebSocket smoke、fake conversation smoke は、この PLAN の前提として扱う。

## 最初のゴール

まず実現するものは、以下である。

```text
mic/STT
  -> tomoko-process
  -> SpeechScheduler
  -> tomoko-process の LLM で発話テキスト生成
  -> speech-order
  -> hot-path-process の TTS/audio execution
  -> browser playback
```

Tomoko は「STT final が来たので返す」だけではなく、
partial / final STT、意味飽和度、無音、間合い、candidate、calendar、user status を材料にして、
その時点で最善の `speech-order` を出す。

hot-path-process は人格判断を持たない。
hot-path-process は Tomoko の物理インターフェースとして、耳と口だけを担当する。

## 新しい基本方針

- `tomoko-process` が人格・文脈・計算モデル・LLM 発話生成を所有する。
- `hot-path-process` は mic / VAD / STT / TTS / audio chunk / playback control を所有する。
- hot-path は speech-order の `replace_current` / `append_after_current` / `stop` だけを実行する。
- LLM は scheduler の最終判断者ではない。LLM は saturation 推定と発話テキスト生成に使う。
- 意味飽和度 LLM 出力は、最初は `SATURATION=0.0..1.0` のみとする。
- provisional / speculative という概念は Tomoko 側の主概念にしない。
- Tomoko はその時点の最善判断として speech-order を出す。間違ったら次の speech-order で上書きする。
- 発話判断計算モデルは binary classifier ではなく、重み付き pressure model + scheduler とする。
- すべての scheduler decision は `score_breakdown` を structured log / DB に残す。
- client は従来通り、音声入出力と表示だけを担当する。
- PostgreSQL は source of truth、LISTEN/NOTIFY は id-only wakeup とする。

## 現在の前提

以下は再実装し直さない。必要なときだけ新方針に合わせて整理する。

- root v2 の基本ディレクトリ、Makefile、README、LOG、MEMORY は存在する。
- `server/shared/models.py` に DTO 集約の土台がある。
- `server/shared/notify.py` に id-only notify helper がある。
- `docker/postgres/init/100_v2_core.sql` に v2 core table の土台がある。
- hot-path WebSocket shell は存在する。
- Apple Speech STT sidecar と fake/real STT smoke は存在する。
- VOICEVOX chunked TTS backend は存在する。
- dflash OpenAI-compatible backend は存在する。
- fake `/ws` conversation smoke は存在する。
- real `say -> /ws -> STT -> LLM -> VOICEVOX` latency smoke は存在する。
- user-status OCR / runtime readiness / tmux runtime launcher は存在する。

## 完了条件の共通ルール

- 実装前に失敗する unit test を追加する。
- DB schema / NOTIFY を変える Phase は integration test を追加する。
- latency に影響する Phase は `_docs/latency.md` に first content / first audio / total latency を追記する。
- scheduler / speech-order / audio replace の decision は structured log に残す。
- `LOG.md` に開始、完了、検証、次回作業を追記する。
- 設計判断が確定した場合は `MEMORY.md` に追記する。
- 既存 `v1/` は参照専用。変更しない。

## Phase S0: PLAN migration

旧計画を履歴として退避し、新方針の PLAN を source of truth にする。

### 実装手順

- [x] 旧 `PLAN.md` を `PLAN.old.md` に退避する。
- [x] 新しい `PLAN.md` を作る。
- [x] `LOG.md` に計画差し替えの結果を追記する。

### 完了条件

- [x] `PLAN.old.md` が存在し、旧計画が残っている。
- [x] 新 `PLAN.md` が「計算モデルを持って会話する Tomoko」への実装順になっている。
- [x] `git diff --check -- PLAN.md PLAN.old.md LOG.md` が通る。

## Phase S1: speech-order and scheduler DTO contracts

まず境界 DTO を固定する。これが以後の実装の背骨になる。

### 実装手順

- [x] `server/shared/models.py` に `SpeechOrderMode` を追加する。
  - `replace_current`
  - `append_after_current`
  - `stop`
- [x] `SpeechOrder` DTO を追加する。
  - `id`
  - `text`
  - `mode`
  - `reason`
  - `priority`
  - `supersedes_order_id`
  - `trace_id`
  - `created_at`
- [x] `SpeechSchedulerInput` DTO を追加する。
- [x] `SpeechPressureState` DTO を追加する。
- [x] `SpeechSchedulerWeights` DTO を追加する。
- [x] `SpeechSchedulerThresholds` DTO を追加する。
- [x] `SpeechSchedulerOutput` DTO を追加する。
  - `action`
  - `text_intent`
  - `llm_prompt_basis`
  - `reason`
  - `score`
  - `score_breakdown`
- [x] `SemanticSaturationResult` DTO を追加する。
  - `saturation`
  - `source`
  - `basis_text`
- [x] old `PromptRequest` は互換用に残すが、main conversation の新契約は `SpeechOrder` に寄せる。
- [x] DTO round-trip / slots / enum value の unit test を追加する。

### 完了条件

- [x] 新 DTO がすべて `server/shared/models.py` にある。
- [x] hot loop には DTO を増やしていない。
- [x] `pytest -m unit tests/unit/test_v2_models.py` が通る。

## Phase S2: SemanticSaturationJudge v0

意味飽和度を最小構造で導入する。

### 実装手順

- [x] `server/tomoko/semantic.py` を作る。
- [x] `SemanticSaturationJudge` interface を定義する。
- [x] LLM 出力 schema は固定行 `SATURATION=0.0..1.0` のみにする。
- [x] `parse_saturation_output()` を作る。
- [x] malformed / out-of-range / missing の fallback を定義する。
- [x] LLM が使えないときの deterministic fallback を作る。
  - 空文字、短すぎる文字列は低 saturation
  - 疑問形、依頼形、命令形、呼びかけ完了は高め
  - 「ただ」「でも」「というか」「一個だけ」は saturation を下げる
- [x] stable prefix を使う helper を作る。
- [x] saturation 判定結果を JSONL に残す。
- [x] parser / fallback / stable prefix の unit test を追加する。

### 完了条件

- [x] `SATURATION=...` だけを受ける parser test が通る。
- [x] deterministic fallback の代表ケース test が通る。
- [x] LLM が壊れた出力を返しても scheduler input が作れる。

## Phase S3: SpeechScheduler v0

重み付き pressure model と action selection を実装する。

### 実装手順

- [x] `server/tomoko/scheduler.py` を作る。
- [x] `SpeechScheduler` を実装する。
- [x] pressure のイベント加算を実装する。
  - semantic saturation high -> reply pressure
  - candidate generated -> initiative pressure
  - calendar near -> calendar pressure
  - user overlap / stop -> interruption penalty
  - rejection -> recent rejection penalty
- [x] pressure の指数減衰を実装する。
- [x] `SpeechSchedulerWeights` の default を定義する。
- [x] `SpeechSchedulerThresholds` の default を定義する。
- [x] `intent_score` と `score_breakdown` を返す。
- [x] action selection を実装する。
  - `stop`
  - `replace_current`
  - `append_after_current`
  - `enqueue`
  - `suppress`
- [x] scheduler decision を structured log に残す。
- [x] unit test を追加する。
  - ユーザー発話への応答で `replace_current`
  - 現在発話中の calendar notice で `append_after_current`
  - stop intent で `stop`
  - interruption penalty が高いと `suppress`
  - new score が current score + margin を超えると `replace_current`

### 完了条件

- [x] `SpeechScheduler` が LLM なしで deterministic に action を返す。
- [x] `score_breakdown` が必ず返る。
- [x] `pytest -m unit tests/unit/test_v2_speech_scheduler.py` が通る。

## Phase S4: tomoko-process owns main LLM text generation

メイン会話 LLM を hot-path から tomoko-process 側に寄せる。

### 実装手順

- [x] hot-path にある chat backend を tomoko 側からも使える場所へ移す。
  - 例: `server/llm/chat.py`
- [x] TTS backend は hot-path 側に残す。
- [x] `PromptBuilderV2` は tomoko-process の発話テキスト生成用に残す。
- [x] `TomokoConversationCore` を作る。
  - STT observation を受ける
  - session / history を更新する
  - saturation を判定する
  - scheduler を呼ぶ
  - LLM で発話 text を生成する
  - `SpeechOrder` を返す
- [x] fake chat backend で `SpeechOrder(text=...)` が作れる unit test を追加する。
- [x] 既存 `HotPathAudioConversation` の main LLM 実行は一時的に互換 path として残し、新 path の test を先に通す。

### 完了条件

- [x] tomoko-process 側だけで `STT observation -> SpeechOrder` が作れる。
- [x] main LLM 発話生成は scheduler output を根拠にしている。
- [x] LLM は speak / not speak / mode を決めていない。

## Phase S5: hot-path speech-order executor

hot-path-process を speech-order の物理実行機にする。

### 実装手順

- [x] `server/hot_path/speech_executor.py` を作る。
- [x] `SpeechOrderExecutor` を実装する。
- [x] `replace_current` を実装する。
  - 現在の TTS / audio chunks を捨てる
  - 必要なら短い無音 marker を挟む
  - 新 order の TTS chunk を流す
- [x] `append_after_current` を実装する。
- [x] `stop` を実装する。
- [x] request id / generation で古い chunk を捨てる。
- [x] client には判断を置かず、server event / binary chunk に従わせる。
- [x] fake TTS backend で replace / append / stop の unit test を追加する。

### 完了条件

- [x] hot-path は `SpeechOrder` を受けて TTS/audio を返せる。
- [x] 古い generation の audio chunk は送られない。
- [x] `replace_current` / `append_after_current` / `stop` の unit test が通る。

## Phase S6: in-process vertical conversation smoke

DB / NOTIFY 分離の前に、同一 process 内で新方針の縦切りを通す。

### 実装手順

- [x] fake STT -> `TomokoConversationCore` -> fake LLM -> `SpeechOrderExecutor` -> fake WAV の smoke を作る。
- [x] `make v2-scheduler-conversation-smoke` を追加する。
- [x] 既存 `make v2-conversation-smoke` と役割を分ける。
  - old: hot-path が prompt executor を直接呼ぶ互換 smoke
  - new: tomoko scheduler が speech-order を作る smoke
- [x] timeline event に scheduler decision / speech_order / audio chunk を出す。
- [x] latency log に fake vertical slice の結果を追記する。

### 完了条件

- [x] `STT -> saturation -> scheduler -> LLM text -> speech-order -> TTS` が fake runtime で通る。
- [x] `make check` が通る。
- [x] `_docs/latency.md` に fake vertical slice の測定を追記する。

## Phase S7: DB and NOTIFY bridge

process 分離に戻す。DB row が本体、NOTIFY は id-only wakeup とする。

### 実装手順

- [x] `v2_speech_orders` table を追加する。
- [x] `v2_speech_scheduler_decisions` table を追加する。
- [x] `v2_semantic_saturation_observations` table を追加する。
- [x] NOTIFY channel に `v2_speech_order` を追加する。
- [x] hot-path-process は STT observation を DB に insert し、`v2_stt_observation` を NOTIFY する。
- [x] tomoko-process は `v2_stt_observation` を LISTEN し、scheduler decision と speech-order を DB に保存する。
- [x] tomoko-process は `v2_speech_order` を NOTIFY する。
- [x] hot-path-process は `v2_speech_order` を LISTEN し、speech-order を実行する。
- [x] recovery polling を入れる。
- [x] integration test を追加する。

### 完了条件

- [x] NOTIFY payload は UUID のみ。
- [x] DB に scheduler decision / speech-order / audio event が残る。
- [x] PostgreSQL integration test で insert / select / notify path が通る。

## Phase S8: real runtime scheduler conversation

実 dflash / VOICEVOX / Apple Speech で会話を通す。

### 実装手順

- [x] `make tmux-runtime` で real runtime を起動する。
- [x] `make v2-runtime-ready` を通す。
- [x] `say -> /ws -> Apple Speech -> tomoko scheduler -> dflash -> speech-order -> VOICEVOX -> browser/audio` の smoke を作る。
- [x] first content / first audio / total latency を測る。
- [x] `score_breakdown` と実際の発話 timing を同じ artifact に残す。
- [x] `_docs/latency.md` に追記する。

### 完了条件

- [x] real runtime で Tomoko が scheduler decision を経由して返答する。
- [x] hot-path は main LLM を直接呼んでいない。
- [x] first audio latency が記録されている。

## Phase S9: overlap, replace, and stop behavior

話している途中の上書き体験を作る。

### 実装手順

- [x] Tomoko 発話中に user speaking が入ったとき、scheduler が `replace_current` または `stop` を出せるようにする。
- [x] stop intent は `stop` action に変換する。
- [x] `replace_current` 時に古い TTS chunk が混ざらないことを test する。
- [ ] 必要なら短い無音 / fade event を入れる。
- [ ] live smoke で、発話途中の上書きが破綻しないことを確認する。

### 完了条件

- [x] ユーザーが覆い被さって話したとき、古い音声を止められる。
- [x] 新 speech-order の音声へ切り替わる。
- [x] stop intent で append queue まで消える。

## Phase S10: append-after-current and calendar/candidate speech

「返答の直後に別件を続ける」体験を作る。

### 実装手順

- [x] calendar urgency を scheduler input に入れる。
- [x] candidate pressure を scheduler input に入れる。
- [x] 現在発話中で calendar pressure が十分高い場合、`append_after_current` を選べるようにする。
- [x] `append_after_current` queue の unit test を追加する。
- [ ] fake calendar scenario の smoke を作る。
- [ ] live で「返答 -> 予定通知」が続くことを確認する。

### 完了条件

- [x] user reply の後に calendar notice を append できる。
- [x] append queue は stop / replace で正しく消える。
- [x] scheduler decision の reason で、なぜ append したか説明できる。

## Phase S11: partial STT and early speech-order

STT final を待たない会話に近づける。

### 実装手順

- [x] partial STT observation を DB に保存する。
- [x] stable prefix を scheduler input に渡す。
- [x] partial の saturation が高い場合に speech-order を出せるようにする。
- [ ] final STT が後からずれた場合、新 speech-order で上書きする。
- [x] 「ただ」「でも」「というか」などの意味変化で replace / suppress できるようにする。
- [x] offline replay / fake partial stream test を追加する。

### 完了条件

- [x] final STT 前に speech-order を出す path がある。
- [ ] 後続 partial / final の意味変化で上書きできる。
- [x] false early のログが分析できる。

## Phase S12: tuning and evaluation loop

重みを体験で調律する。

### 実装手順

- [x] scheduler decision artifact を集約する report を作る。
- [x] weight / threshold を dataclass default から必要最小限だけ config 化する。
- [x] `make v2-scheduler-report` を追加する。
- [x] human label を付けられる形式で logs を出す。
- [x] false interruption / too quiet / too chatty / missed calendar notice を分類する。

### 完了条件

- [x] なぜ話したか、なぜ黙ったか、なぜ上書きしたかを report で読める。
- [x] 少数の weight / threshold で挙動を調整できる。
- [x] 調整結果を `MEMORY.md` に追記できる。

## 後回しにするもの

まずは計算モデルを持って会話する Tomoko を作るため、以下は後回しにする。

- 汎用推論 queue system
- task 機能
- 複雑な semantic schema
- multi-field semantic split LLM output
- VLM 画像そのものの online prompt 混入
- client-side decision
- Redis などの追加 state store

## 次に着手する Phase

次は Phase S1 から始める。

最初の実装単位は以下。

```text
SpeechOrder / SpeechSchedulerInput / SpeechSchedulerOutput DTO
  -> unit test
  -> SemanticSaturationJudge parser
  -> SpeechScheduler v0
```

ここまで通ったら、tomoko-process 側で fake STT から speech-order を作る縦切りに進む。

## 2026-06-18 セッション13 進捗追記

S1-S6 と S8/S12 の主線は実装・検証済み。
`make v2-scheduler-conversation-smoke` は fake 縦切りで通り、
`make v2-scheduler-say-latency-smoke` は起動済み real runtime 上で
`scheduler_decision` / `speech_order` / `score_breakdown` を含む artifact を残した。

未チェックで残すものは、DB 常駐 LISTEN worker / recovery polling、発話途中の live overlap 確認、
fake/live calendar append smoke、final STT divergence による上書きである。

## Phase S13: DB-backed conversation session prompt history

ARCHITECTURE.md の「会話セッション境界を DB に明示する」を DB split 本線に入れる。
hot-path は引き続き raw STT observation だけを書き、session id の発番と durable utterance 化は
tomoko-process が行う。

### 実装手順

- [x] tomoko-process が final STT を受けた時、open session が無ければ `v2_conversation_sessions` を発番する。
- [x] open session があり、`last_activity_at` から無音 gap を超えた場合は旧 session を close して新 session を発番する。
- [x] `v2_utterances` に同一 session の user 発話を保存する。
- [x] Tomoko の返答も同じ session の `v2_utterances` に保存する。
- [x] prompt の `STABLE_CONTEXT` は同一 session の過去発話から作り、現在の user 発話は `CURRENT_USER_UTTERANCE` だけに置く。
- [x] 5ターン smoke artifact で LLM prompt に同一 session の履歴が積まれることを確認する。

### 完了条件

- [x] `pytest -m unit` が通る。
- [x] DB split smoke が通り、session / utterance / speech-order が DB に残る。
- [x] 5ターン目 prompt で現在発話が stable context と current の両方に重複しない。

## Phase S14: prefix-cache friendly session transcript prompt

dflash prefix cache が multi-turn 会話で効くように、main reply prompt を session transcript 形式へ変更する。
人間確認用 artifact には `SYSTEM` / `INSTRUCTION` / `SESSION_TRANSCRIPT` を残し、
LLM transport では `SESSION_TRANSCRIPT` を OpenAI chat roles に分解して送る。

### 実装手順

- [x] main reply prompt を `SYSTEM` / `INSTRUCTION` / `SESSION_TRANSCRIPT` 形式に変更する。
- [x] `SESSION_TRANSCRIPT` に同一 session の `user:` / `tomoko:` 履歴と現在 user 発話を append-only に積む。
- [x] 文字列 prompt が next turn で previous turn の prefix になることを unit test で固定する。
- [x] LLM 送信時に `SESSION_TRANSCRIPT` を `user` / `assistant` role の message list に分解する。
- [x] hot-path executor と tomoko-process chat backend の両方で同じ role 分解を使う。
- [x] 5ターン smoke artifact に新 prompt を保存し、dflash prefix-cache-stats を確認する。

### 完了条件

- [x] `pytest -m unit` が通る。
- [x] `ruff check` が通る。
- [x] 5ターン real runtime smoke で 2ターン目以降に dflash `prefix cache hit` が出る。
- [x] first audio latency を `_docs/latency.md` に追記する。

## Phase S15: Gemma E2B semantic early-start observation

意味飽和判定を軽量 LLM に寄せ、STT final より前に main LLM 開始 OK を出せるか観測する。
現行 Apple Speech sidecar は final-only なので、まずは say 音声の prefix window を疑似 partial として
replay し、実 streaming partial が同じ時刻で得られた場合の lead time を推定する。

### 実装手順

- [x] semantic saturation 専用の OpenAI compatible backend を追加する。
- [x] Gemma E2B 向けに `SATURATION=<number>` の compact few-shot prompt を使う。
- [x] E2B endpoint を `TOMOKO_V2_SEMANTIC_LLM_URL` / `TOMOKO_V2_SEMANTIC_LLM_MODEL` で指定できるようにする。
- [x] `say` 音声の prefix window を疑似 partial として Apple Speech に通す smoke を追加する。
- [x] partial offset、partial text、E2B saturation latency、would_start_llm、full final STT との差分を JSON artifact に残す。
- [x] `_docs/latency.md` に early-start smoke の結果を追記する。

### 完了条件

- [x] `pytest -m unit` の focused test が通る。
- [x] `ruff check` が通る。
- [x] Gemma E2B 実 endpoint で saturation 判定が実行できる。
- [x] full final STT より前に `would_start_llm=true` が出るか、出ないならその offset と理由が artifact で分かる。

## Phase S16: v1 Apple Speech pseudo streaming partial for v2

S15 の prefix-window 推定を、実 `/ws` audio path の partial event として確認する。
v1 の Apple Speech backend は Swift sidecar の true partial ではなく、Python 側で音声を累積し、
一定間隔で Apple Speech final transcription を再実行して partial 扱いにしていた。
v2 でも同じ `streaming` / `stream_interval_ms` / `stream_min_audio_ms` /
`_last_stream_text` 抑制を移植し、VAD segment 完了前に `PartialTranscriptObservation` を流す。

### 実装手順

- [x] v2 `AppleSpeechStreamingBackend` に pseudo streaming partial を移植する。
- [x] `process_audio_samples()` で VAD final segment が返る前に partial STT を処理する。
- [x] partial saturation が閾値未満なら speech-order を出さない gate を追加する。
- [x] focused unit test で partial emit / duplicate suppression / hot-path partial speech-order を固定する。
- [x] 実 `/ws` smoke で final transcript 前の partial speech-order を artifact に残す。
- [x] partial STT / E2B / LLM / TTS を WebSocket audio receive loop から非同期に逃がす。
- [x] partial 由来 speech-order 後の final STT で重複発話しないように reconcile する。

### 完了条件

- [x] `pytest -m unit` の focused test が通る。
- [x] `ruff check` が通る。
- [x] 実 E2B endpoint で partial 由来の scheduler `replace_current` が final transcript 前に出る。
- [ ] first audio がユーザー発話終了前、または終了直後の目標範囲に入る。

## 2026-06-18 セッション21 進捗追記

partial STT / E2B / LLM / TTS と final STT を WebSocket receive loop から background lane に逃がした。
partial lane は queued audio chunks を coalesce し、final lane は partial lane が idle になるまで短く待ってから
final Apple Speech を始める。partial 返答は concise prompt にして、VOICEVOX full WAV 待ちを短くした。

clean smoke では `logs/say-latency-20260618-160201.json` で voice-end to first audio 860.5ms、
final 確認込みでは `logs/say-latency-20260618-160314.json` で 1515.6ms。
前回の 4058〜4492ms より改善したが、目標範囲としてはまだ不安定なので完了条件は未チェックのまま残す。

## 2026-06-18 セッション22 進捗追記

partial 由来 speech-order 後に同一 utterance の final STT が来た場合、final は durable user utterance
として保存しつつ speech-order / prompt は出さず、scheduler decision は
`final reconciled with active partial reply` で suppress するようにした。
Apple Speech partial が付けることがある `その` や wake word 差分は normalize して比較する。

実 `/ws` smoke artifact `logs/say-latency-20260618-161305.json` では、
partial `その今日の予定を教えて` が speech-order を 1 件出し、その後の final
`智子今日の予定を教えてそれだけで大丈夫です` は reconcile suppress された。
`speech_order` / `tts_result` / `binary_audio` は各 1 件で、重複音声は出ていない。

`scripts/v2_say_latency_smoke.py --input-wav` も追加した。
QuickTime などで録音した音声ファイルを 16kHz mono PCM WAV に変換して `/ws` に流せる。
clean hot-path で `_reference/test.m4a` を流した artifact `logs/say-latency-20260618-161626.json` では、
final transcript `こんにちは今の気分を教えてくださいませ`、voice-end to first audio 5864.3ms。
この録音では partial saturation が閾値未満で、早期発話ではなく final 起点になった。

## 2026-06-18 セッション23 進捗追記

partial 開始 gate を saturation 単独から、saturation と総合 score の併用に変更した。
`semantic_saturation < 0.75` でも `score >= 0.75` なら request-like partial として開始を許す。
これにより artifact `logs/say-latency-20260618-161626.json` の
`こんにちは今の気分を教えて下さい` 相当の partial は次回 smoke で早期 speech-order 候補になる。

## 2026-06-18 セッション24 進捗追記

`make run` / `tmux-runtime` に Gemma E2B semantic endpoint を追加した。
`semantic-e2b` window で `mlx_lm.server --model mlx-community/gemma-4-e2b-it-OptiQ-4bit --port 8083`
を起動し、hot-path 起動前の readiness で `8083/v1/models` も待つ。
hot-path window には `TOMOKO_V2_SEMANTIC_LLM=1` と E2B URL/model を渡す。

## Phase S17: MaAI fixed backchannel hot-path lane

MaAI を VAP/VAD 制御ではなく、hot-path の相槌専用センサーとして使う。
本文返答、semantic saturation、VAD final endpointing には直接混ぜない。
MaAI の react/emo score が閾値を超えた時だけ、事前生成済みの固定 WAV asset を短く返す。

相槌候補は `うん` / `へえ` / `ほう` の3種に限定する。
音声は `assets/backchannels/` 配下の WAV を使い、相槌のために main LLM や VOICEVOX を呼ばない。

### 実装手順

- [x] `assets/backchannels/` に `un.wav` / `hee.wav` / `hou.wav` を置く。
- [x] hot-path 用の MaAI backchannel detector を追加し、MaAI 未導入/無効時は no-op にする。
- [x] detector は `TOMOKO_V2_MAAI_BACKCHANNEL=1` の時だけ有効にする。
- [x] MaAI result の react/emo score が閾値以上、cooldown 中でない、Tomoko 音声出力中でない時だけ相槌を返す。
- [x] `/ws` の audio receive loop で user audio chunk を detector に渡し、相槌 WAV は result queue 経由で binary audio として返す。
- [x] unit test で閾値、cooldown、asset cycling、無効時 no-op、WAV chunk 送信を固定する。

### 完了条件

- [x] `pytest -m unit` の focused test が通る。
- [x] `ruff check` が通る。
- [x] `TOMOKO_V2_MAAI_BACKCHANNEL=0` では既存 `/ws` 音声経路が変わらない。
- [x] `TOMOKO_V2_MAAI_BACKCHANNEL=1` かつ MaAI suggestion が閾値を超えた時、固定相槌 WAV が main LLM/TTS を呼ばずに返る。
