# MEMORY.md

セッションをまたいで有効な判断・気づき・未解決疑問を記録する。
LOG.md が時系列なのに対して、こちらはトピックごとに整理する。

---

## 確定した判断

### 確定した判断: 低音量でも成立した短文は transcript filter で落とさない
2026-05-31 の実ログでは、ambient 中に STT が `大変良いと思いますよ私は` を返していたが、
`TranscriptFilter` が `low_audio_short_text` として drop し、UI LOG の `transcript_final` まで進まなかった。

原因は `LOW_AUDIO_SHORT_MAX_CHARS = 20` が日本語には広すぎ、低音量の短い誤認識だけでなく
成立した一文まで巻き込んでいたことだった。
低音量 blanket filter の対象は 6 文字以下に狭める。
`いいと思います` のような短いが成立した文は accept し、
`たぶんね` のような 6 文字以下の低音量 fragment は引き続き `low_audio_short_text` で drop する。

### MaAI / VAP 系の相槌予測は gesture sensor として hot path から分岐する
2026-05-30 時点では、MaAI / VAP 系の backchannel prediction を
VAD / STT / reply hot path の前段や中継には置かない。
MaAI は別軸の gesture sensor として扱い、人間マイク音声と Tomoko 出力音声を
optional audio tap へ複製する。

`TomoroSession.process_audio_chunk()` は従来どおり user mic float32 chunk を受けて
VAD / STT へ流す source of truth の入口であり、tap 失敗時も通常経路を止めない。
Tomoko 音声も `_send_audio_chunk()` で browser send と別に tap へ複製する。
初期実装では Tomoko ch2 の時刻は server send 時刻を `observed_at` とし、
browser playback telemetry による精密な再生時刻補正は別 Phase に残す。

MaAI 側からの判断は命令ではなく `BackchannelSuggestion` / `backchannel_suggested`
event として `TomoroSession.post_event()` に戻す。
これは non-authoritative emission であり、相槌を実際に鳴らすか、
hold / discard するかの最終判断は `TomoroSession` が持つ。
通常の conversation log や long-term memory へ相槌をそのまま混ぜる設計にはしない。

MaAI tap の動作確認は、実ブラウザや実マイクを必須にしない。
`_tools/smoke_maai_tap_session.py` は `TomoroSession` を実サーバーなしで生成し、
macOS `say` 由来の WAV を `_flush_tts_text()` / `_send_audio_chunk()` 経由で tap に流す。
任意の user dummy sine を `process_audio_chunk()` に入れることで user 側 tap も確認する。
`make smoke-maai-tap` の実 smoke では Tomoko send bytes と tap bytes が一致することを確認する。

MaAI 本体は optional dependency とし、通常 runtime では無効にする。
`TOMOKO_MAAI_BACKCHANNEL_ENABLED=1` の時だけ gateway が `MaaiBackchannelTap` を作り、
MaAI 未インストール時は通常会話中に黙って壊れるのではなく明示エラーにする。
MaAI 0.1.16 の `bc_2type` model は `frame_rate=10` のような integer filename (`10hz`) を期待するため、
default は `10.0` ではなく `10` にする。
`get_result()` は blocking queue wait なので、runtime adapter では MaAI の `result_dict_queue.get(timeout=0.2)`
を使える時だけ timeout poll にし、stop 時に threadpool task が残らないようにする。
`make smoke-maai-real` は `maai_enabled=true` で Tomoko `say` WAV と user dummy sine を MaAI 本体へ流し、
短い smoke では suggestion が空でも MaAI 本体経由で process が終了することを確認する。

MaAI raw score の診断は threshold 後の suggestion だけで見ない。
`make smoke-maai-dialogue` は `say` で user / Tomoko の合成二者会話を作り、
16kHz mono timeline の ch1 / ch2 として MaAI へ 10ms frame 投入する。
JSON summary には `raw_scores[].p_bc_react` / `raw_scores[].p_bc_emo` を全件保存する。
MaAI raw result には `x1` / `x2` の音声配列が含まれるため、診断 JSON では score / metadata だけを
`raw` に残し、音声配列 key は `raw_omitted_keys` へ記録する。
2026-05-30 の合成 dialogue smoke では raw score が 200 件返り、
`max_p_bc_react=0.7259804606437683`、`max_p_bc_emo=0.19943645596504211` だった。
この結果を受けて react threshold は 0.68 に下げる。

MaAI react suggestion は LLM 本返答ではなく LLM-less gesture audio として release する。
release 対象は `kind="react"` かつ `score >= 0.68` だけにする。
`kind="emo"` は現時点では release せず、TomoroSession では `unsupported_kind` として skip する。
release 条件は、user が speech segment 中 (`state="listening"`)、Tomoko playback が idle、
同一 user speech segment でまだ相槌していない、global cooldown 2000ms を満たすこと。
文言は `うん` / `なるほど` / `そっか` の固定 pool から選ぶ。
これは会話ログや長期記憶に入る発話ではなく、`reply_done` に `control="backchannel"` を付ける短い gesture audio として扱う。

`make smoke-maai-dialogue` は MaAI raw score / suggestion だけでなく、smoke 専用 TomoroSession harness に
suggestion を流した `session_releases[]` も JSON に出す。
各 entry には timeline 上の `user_speaking` / `tomoko_speaking`、TomoroSession emission、
TTS input、audio chunk/bytes、`reply_done_controls` を含める。
2026-05-30 の実 smoke では `p_bc_react=0.7259804606437683` の react suggestion が
`observed_sec=8.521801` で発生し、user speaking / Tomoko idle のため
`backchannel_released`、`reply_done_controls=["backchannel"]`、`audio_chunks=1` が確認できた。

`_tools/materials/maai.wav` のような stereo material WAV は `make smoke-maai-material` で確認する。
default は ch1=user / ch2=tomoko、先頭 30 秒、等速投入である。
長い素材は `MAAI_MATERIAL_START_SEC` / `MAAI_MATERIAL_DURATION_SEC` で窓をずらし、
ch1/ch2 が逆に見える場合は `MAAI_MATERIAL_SWAP_CHANNELS=--swap-channels` を指定する。
2026-05-30 に MaAI 公開サンプル由来の `_tools/materials/maai.wav` 全体を 30 秒窓で scan したところ、
MaAI suggestion は複数出たが、ほぼ `kind=emo` であり TomoroSession の現行 gate では `unsupported_kind` skip になった。
react suggestion も最大 0.512 程度で、現行 release 条件 `p_bc_react >= 0.68` では `backchannel_released` は出なかった。

### MemoryGate で Retrieve と Use を分ける
2026-05-30 時点では、context snapshot で取得できた記憶をそのまま prompt に渡さない。
`TomoroSession` は `MemoryGate` を通して、deep memory / calendar memory を読むかどうかと、
取得済み候補を `ThinkingInput.long_term_memory` へ expose するかどうかを分ける。

最初の実体は `RuleBasedMemoryGate` とし、差し替え境界は `MemoryGate` protocol にする。
log は `LoggingMemoryGate` decorator が出し、`retrieved` / `exposed` / `suppressed` /
`reason` / `source_counts` / `top_suppressed` を観測できるようにする。
LLM reranker は hot path へ追加しない。

初期 rule では `recall_request` は会話記憶を使い、`calendar_request` は calendar memory だけを使う。
`self_statement` / `chitchat` / `unclear` は会話記憶を prompt へ出さない。
特に「普通に覚えております」のような自己陳述は `self_statement` として扱い、
`123` のような session summary が retrieval / prompt exposure / carryover に漏れないようにする。

memory grain は混ぜて扱わず、log 上でも `calendar` / `session_summary` / `turn_snippet` /
`persona` / `short_memory` / `other` に分けて数える。
現時点で gate が直接裁くのは `MemoryHit` 化された long-term / calendar 候補であり、
persona slice と short memory notes は既存の別 route を維持する。
これらも gate 対象にする場合は、`MemoryGate` の DTO を source slice 単位へ拡張する。

### arrival_candidates は thinker 側で 7 日より古い期限切れ行を物理削除する
2026-05-30 時点では、`arrival_candidates` は入室・接続時の一時候補であり、
会話原本・記憶・日記材料ではない。
`valid_until` によって fetch 対象外にはなるが、DELETE しない限り live tuple として増え続けるため、
arrival precompute のインターバルで `valid_until < now - 7 days` の行を物理削除する。

この cleanup は `TomoroSession` や online `/ws` path へ持ち込まない。
`CandidateStore.delete_expired_arrival_candidates(older_than=...)` を thinker が precompute 前に一発呼ぶ。
cleanup 失敗時は `ArrivalPrecomputeResult.error_count` に加算して log し、
arrival precompute 自体は継続する。
PostgreSQL の autovacuum は DELETE 後の dead tuple 回収に任せるが、
期限切れ行を削除対象にする判断はアプリ側で行う。

### persona updater は 31B 専用 lane を使う
2026-05-30 時点では、persona updater は `session_summary` role を兼用しない。
会話 hot path ではなく background worker なので、速度より JSON 生成品質を優先し、
`persona_update_backend` / `persona_update_fallback` を config に持たせる。

central realtime では `persona_update_backend = "lmstudio_gemma4_31b"` とし、
fallback は `local_gemma4_e2b_mlx` にする。
session summarizer は引き続き `session_summary_backend` を使い、conversation / memory extraction /
diary / candidate_gen の lane は変更しない。

### persona updater は structured output で JSON schema を要求する
2026-05-30 の `make persona-updater-once` では、31B に切り替えた後も prompt-only JSON 生成だったため、
`json.decoder.JSONDecodeError` で落ちた。

persona updater は通常の `chat_stream` を使わず、LM Studio backend の
`chat_stream_structured` / `response_format=json_schema` を使う。
backend が structured output を持たない場合は persona update を実行しない。
persona update は background worker なので、`PERSONA_UPDATE_MAX_TOKENS = 1600` として
truncation の可能性を下げる。

### Calendar cue は long-term context への入口として扱う
2026-05-30 時点では、予定・スケジュール・今日・明日・何時などの発話は
過去会話 memory cue ではなく calendar cue として扱う。
calendar cue は `ContextSnapshotBuilder` を `deep` depth で起動して `calendar_events` DB を読むための入口であり、
`should_use_deep_memory()` の意味は過去会話 retrieval の cue として維持する。

calendar cue で読まれた `TomokoContextSnapshot.calendar_events` は、
同一会話 session 内の follow-up で使えるように `MemoryHit` へ変換して
既存の `RetrievedContextCarryoverState` に載せる。
ただし calendar source of truth は常に `calendar_events` DB であり、
carryover は prompt 用の短命な参照情報として session close 時に消える。
prompt 上では calendar 由来 hit を `参照情報` として表示し、過去会話とあわせて
`長期コンテキスト` ブロックに入れる。

### アーキテクチャ
- WebSocket は 1 本のエントリーポイント
- PostgreSQL が唯一の真実、ノード間通信は DB を介した読み書きのみ
- pub/sub なし
- プロセス: edge / gateway / thinker / journalist の分離
- 音声データはエッジの外に出ない

### VAD
- 無音閾値: 「ともこ、聞こえますか？」といった読点のポーズで音声が分割されないよう、800ms を基準にする。
  （以前は400msだったが、ウェイクワード後に途切れる問題があったため変更）
- ホットループ内はプリミティブ（np.ndarray）のまま処理
- 発話終了時のみ SpeechSegment に包む

### LLM バックエンド
- M1フェーズ: Ollama（qwen2.5:7b）で動かす
- M1完了後: MLX（mlx-community/Qwen2.5-7B-Instruct-4bit）に切り替えて実測比較
- エッジの軽量LLM: gemma3:2b（MLX）

### TTS バックエンド
- M1フェーズ: macOS say コマンド（Kyoko）
- M1完了後: kokoro-mlx（jf_alpha / jf_beta）に切り替えて日本語品質を確認
- NG なら VOICEVOX に切り替え（TTSBackend 抽象で差し替え可能）
- 2026-05-25 時点では、起動済み `voicevox.app` / VOICEVOX Engine を外部 HTTP TTS として使う。
  Tomoko 側は `VoicevoxBackend` で `http://127.0.0.1:50021` の `/audio_query` / `/synthesis` を叩くだけにし、
  VOICEVOX 本体・音声ライブラリは repo に同梱しない。
  default は春日部つむぎ speaker id `8`、`config/central_realtime.toml` / `config/edge_kitchen.toml` は
  `tts_backend = "voicevox_tsumugi"` とする。
  実 smoke では `うん、わかった。少し待ってね。` が first chunk 364.7ms / total 364.9ms だった。

### VOICEVOX stream backend
2026-05-25 時点では `voicevox.app` / VOICEVOX Engine 0.25.2 の OpenAPI に `/cancellable_synthesis` がある。
ただし実行中 Engine では experimental feature が default 無効で、実 AudioQuery に対する
`/cancellable_synthesis` は 404 を返した。
Tomoko のブラウザは WebSocket の binary message ごとに `decodeAudioData()` するため、現状は部分 WAV byte を
そのまま流せない。
このため default TTS は `voicevox_tsumugi_stream` にするが、backend は `/cancellable_synthesis` を優先し、
404 の場合だけ `/synthesis` に fallback して完全な WAV chunk を返す。
実 smoke では fallback 経由で first chunk 347.5ms / total 347.6ms、出力は
`logs/voicevox-tsumugi-stream-smoke.wav`。

### VOICEVOX の試用設定
上の「default TTS は `voicevox_tsumugi_stream`」という判断は、2026-05-26 の cancellable synthesis 実測により否定する。

`--enable_cancellable_synthesis` 付き Engine では `/cancellable_synthesis` が HTTP chunk を複数返すが、
warm run の first byte は通常 `/synthesis` とほぼ同等で、初回 worker ではむしろ遅かった。
Kokoro MLX の first binary は過去実測で約 88ms、VOICEVOX は通常 `/synthesis` で約 315-365ms なので、
first audio だけなら Kokoro の方が明確に速い。

ただし音質・体感確認のため、central / edge の default TTS は通常の `voicevox_tsumugi` にする。
stream/cancellable 由来の初回 worker 遅延を混ぜないため、試用中は `voicevox_tsumugi_stream` ではなく
`voicevox_tsumugi` を使う。

### 感情表現プロトコル
- 自前プロトコル採用（Phase 6a）
  ```
  EMOTION:happy
  本文テキスト
  ```
- partial JSON parser 方式（Phase 6c）は品質が不安定な場合のみ移行

### Persona overlay
2026-05-30 時点では、Tomoko の基本人格を直接置き換えず、
`prompts/base_persona.md` の sibling `prompts/persona_overlay.md` が存在する場合だけ
会話 LLM の system prompt に薄い人格 overlay として差し込む。

overlay は base persona の直後、現在時刻・calendar・short memory・long-term memory より前に置く。
これにより「Tomoko の芯」と「文脈情報」を壊さず、話し方の傾向だけを試せる。

一色いろは風の実験では、原作キャラクター名、原作台詞、固有設定は prompt に入れない。
小悪魔的、人なつっこい後輩、軽い茶目っ気、断る時の逃げ道、作業中なら実用へ戻る、
という反応パターンだけを overlay に記述する。
`ThinkFastMode` の default overlay path は `persona_path.with_name("persona_overlay.md")` とし、
tmp persona を使う unit test や比較実験へ repo overlay が漏れないようにする。

### 気づき: 未定義 emotion 禁止は overlay 側にも必要
2026-05-30 の実推論確認では、`prompts/base_persona.md` の emotion 列挙直下に
「プログラム側で未定義の emotion は出力しないこと」「playful は使わないこと」と明記しても、
overlay 有りの `gemma-4-26b-a4b-it-mlx` は `EMOTION:playful` を2/3ケースで出した。

これは base persona の禁止文が効かないというより、overlay の「小悪魔的」「茶目っ気」「遊び心」の意味づけが
emotion label 生成へ強く引っ張られている可能性が高い。
次の最小修正では `prompts/persona_overlay.md` 側にも、
`emotion は neutral / happy / surprised / sad / thinking / gentle / excited だけを使う。
playful は本文の雰囲気で表し、ラベルには使わない` と明記する。

runtime guard として、未定義 `EMOTION:*` 行を本文に漏らさず fallback emotion へ丸める案もあるが、
まずは prompt-only 修正で再測定する。

### 確定した判断: 未定義 emotion label は runtime で neutral に丸める
2026-05-30 の実推論で `EMOTION:playful` が繰り返し出たため、
会話 prompt だけで emotion protocol を守らせる方針は補助的なものとして扱う。

`ThinkFastMode` の emotion parser は、`EMOTION:` で始まる行や inline header の label が
`neutral / happy / surprised / sad / thinking / gentle / excited` に含まれない場合、
その行を本文として流さず `neutral` emotion に丸める。
これにより TTS / reply_text へ `EMOTION:playful` のような protocol 行が漏れない。

structured output への移行は、conversation streaming / TTS first audio への影響を測る別 Phase とする。
現時点では streaming text protocol を維持し、受信側の deterministic guard で破綻を止める。

### Git 運用
- コミットは自由、origin への push は人間のみ
- テストが通る単位でコミット

### 現在の構造固定: `server/session.py` monolith baseline を維持する
2026-05-29 時点では、`server/session.py` の `TomoroSession` 一枚構成を
runtime の固定構造として扱う。
直近の復旧後 baseline では `server/session.py` は約 2200 行であり、
`server/session/` package split や dispatcher / effects / event_runner / maps、
OutputDemand / Watcher の復活は行わない。

この固定は、`TomoroSession` が巨大でよいという判断ではない。
現時点で実ブラウザ会話が通っている closed-loop の所有境界を壊さず、
次の分割を 1 Phase 1 責務で進めるための凍結である。

`TomoroSession` は引き続き stateful control core / final owner として、
attention mode、VAD / playback state、conversation session lifecycle、
candidate / arrival / turn-taking / barge-in / stop-intent の final gate、
reply task、TTS queue、stale result discard、DB write ordering、
WebSocket JSON / binary send ordering を所有する。

外へ出してよいのは dedicated helper / small state holder に限る。
現在許可済みの外部 helper は `server/session_latency.py`、
`server/session_carryover.py`、`server/session_payloads.py`、
`server/session_candidate_policy_helpers.py`、`server/session_key_helpers.py`、
`server/session_memory_helpers.py` である。
これらは latency probe、retrieved context carryover、payload coercion、
candidate policy の副作用なし判定、request id formatting、memory formatting だけを扱い、
final gate、DB write、reply orchestration、ContextSnapshotBuilder policy は持たない。

次に構造を動かす場合は、PLAN.md に専用 Phase を立て、
characterization test で現状挙動を固定してから、対象を 1 責務だけに絞る。
method の大規模 reorder、package split、DB write SessionCommand 化、
audio hot path / TTS queue / LLM-TTS ordering / playback timing の再設計は、
明示 Phase なしに進めない。

### short memory extraction は remember_items 抽出に限定する
2026-05-29 の short memory 実験では、E2B extraction に自然文要約まで任せると
`CD E 5つ覚えた` のような誤った正規化が起きた。

今後の short memory extraction LLM の責務は、`remember_items` 配列として
「次の数ターンで覚えるべき対象」だけを抽出することに限定する。
明示的な「ABCを覚えて」のような発話は `mode="verbatim"` として対象文字列だけを返させる。
言い換え、個数補完、意味づけ、Tomoko 返答からの創作は行わせない。

重複除去、TTL / max 件数、prompt 展開文の生成は Tomoko 側の deterministic code が担当する。
会話 prompt では `verbatim` note を `Remember verbatim: ...` として展開し、
LLM extraction 側には文章化の責務を持たせない。

### Google Calendar RRULE は未対応 frequency を daily fallback しない
2026-05-30 の実データ確認で、`make gcal` の ICS importer が `FREQ=YEARLY` の記念日・誕生日を
毎日予定として展開していた。

原因は `_advance_time()` が未対応 frequency を daily fallback していたことと、
context overlap 判定が `event_end >= window_start` で前日終日予定を境界一致で含めていたこと。

Google Calendar 予定の recurrence は、対応済みの `DAILY` / `WEEKLY` / `MONTHLY` / `YEARLY` だけを展開する。
未対応 frequency は daily に落とさず base event だけにする。
予定区間は半開区間として扱い、`event.start < window_end` かつ `event.end > window_start` の時だけ overlap とする。

---

## 未解決の疑問（人間への確認待ち）

※ 実装中に生じた疑問をここに積む。確認が取れたら「確定した判断」に移す。

### [2026-05-23] irodori-tts の導入形態
M1 Phase 0 の項目に「irodori-tts をローカルで起動確認」があるが、Homebrew と PyPI にはパッケージが無く、ローカルサービスも起動していなかった。
公式 GitHub は `Aratako/Irodori-TTS` で、uv ベースの別リポジトリとして提供されている。
この Tomoko リポジトリにサブモジュール/外部ディレクトリとして導入するのか、M1 では `say` のみを正式対象にして irodori は後続扱いにするのか確認が必要。

---

## 気づき

※ 実装中の重要な発見をここに積む。

### 確定した判断: Phase 10.12 TomoroSession は package facade として維持する
2026-05-28 に Phase 10.12 として `server/session.py` を `server/session/` package へ移した。
外部 import 契約は `from server.session import TomoroSession` のまま維持し、
production 外部から new する public object も引き続き `TomoroSession` だけにする。

package 内では、`core.py` を facade / state holder とし、`carryover.py` に
`RetrievedContextCarryover`、`reducer.py` に `TomoroSessionReducer`、`effects.py` に
`TomoroSessionEffects`、`reply_orchestrator.py` に `ReplyOrchestrator` を分けた。
ただし会話 session lifecycle、candidate hard gate、stop / playback の final ownership は
TomoroSession 側に残す。

今回の分離は外部 `/ws` event、`ThinkingInput`、`TTSInput`、DB store 契約を変えない内部整理である。
reducer は `await` せず DB / LLM / TTS / WebSocket send を触らない。
effects は `SessionCommand` の実行だけを扱い、判断を追加しない。
reply orchestrator は LLM/TTS 実行順序と latency log を維持し、stop / stale / playback gate の
最終判断は持たない。

### 確定した判断: Phase 10.13 runtime state は TomoroSessionState に集約する
2026-05-28 に、Phase 10.12 後も `core.py` に runtime field が残りすぎていたため、
`server/session/state.py` の `TomoroSessionState` に状態置き場を集約した。

これは helper が自由に state を mutate する設計ではない。
production 外部の public entry は引き続き `TomoroSession` だけで、
`TomoroSessionState` は package-internal な runtime container として扱う。
TomoroSession は互換 property/proxy を通じて既存内部コードの挙動を保ちつつ、
状態の置き場を 1 箇所にする。

あわせて `session_started` / `initiative_candidate_loaded` / `arrival_candidate_loaded` の
event-driven 判断を `TomoroSessionReducer` に移し、candidate / arrival の final gate は
TomoroSession が所有する state と reducer decision の組み合わせとして読める形にした。
この時点で `server/session/core.py` は 1836 行から 1572 行に縮小した。

### 確定した判断: Phase 10.14 dispatcher は operation plan を組み立てる
2026-05-28 に、session 内部 operation を read-only / write-only / both の性質で分けるため、
`server/session/operation_plan.py` に `OperationPlan` / `OperationContext` / `OperationResult` を追加した。

`OperationPlan` は判断体ではなく、phase 順序と並列実行だけを扱う。
`.parallel([...])` は同一 phase 内の step を `asyncio.gather` で実行し、
`.do(step)` は単一 step phase、`.then()` は読みやすさのための separator として扱う。
read-only step は `OperationContext.values` や state snapshot を読むだけにし、
write step は TomoroSession の event drain / dispatcher 順序内で state を変更する。
both step は atomic transition が必要な場合だけに限定し、増やしすぎない。

既存外部契約は変えず、最初の実利用として playback telemetry event を
`TomoroSessionReducer` 内で `OperationPlan` 経由にした。
これにより、次に `TranscriptFlow` / `LifecycleFlow` を切り出す時も、
巨大な単一「判断体」ではなく、plan が read / write operation を組み立てる形へ広げられる。

### 確定した判断: Phase 10.15 gateway/session 境界は既存 DTO を signal として扱う
2026-05-28 に Phase 10.15 として、gateway / session の境界を audio path と signal path に分けた。

`SessionInputSignal` は新しい wrapper を必ず作るのではなく、既存の意味 DTO である
`Transcript` / `PlaybackTelemetry` / `SessionEvent` の type alias として扱う。
これは `ExternalTranscriptInput` のような二重包装で読みやすさが落ちるのを避けるためである。

audio binary は引き続き `process_audio_chunk(bytes)` / `send_audio(bytes)` の hot path に残し、
`SessionInputSignal` / `SessionOutputSignal` には包まない。
gateway は `server/gateway/ports.py` の分類どおり、audio input/output と signal input/output を運ぶ adapter とし、
participation / turn-taking / candidate gate / conversation lifecycle の最終判断は session package 内に残す。

`TomoroSession.accept_signal()` を signal 系入口として追加し、`post_event()` /
`process_transcript()` / `handle_playback_telemetry()` は互換 sugar として残す。
package 内では `SessionSignalDispatcher` が `Transcript` / `PlaybackTelemetry` / `SessionEvent` を見て
`TranscriptFlow`、playback reducer、既存 event reducer へ振り分ける。

`SessionOutputSignal` は client に出る JSON 的 event の薄い wrapper とし、既存 WebSocket JSON contract は壊さない。
`SessionCommand` は DB / LLM / TTS / worker / candidate store への副作用命令として signal output と分離したまま維持する。

### MLX Whisper large turbo q4 の実会話改善
`local_whisper_mlx_large_turbo_q4` へ切り替えた実ブラウザ確認では、STT 品質が劇的に改善し、
会話として成り立ち始めている体感があった。

一方で「ココココ」のような hallucination っぽい出力が出る時、GPU が完全にフルに使われる様子が見えた。
無音・ノイズ・短い断片に対して large 系 decoder が粘っている可能性があるため、今後は transcript filter だけでなく
STT 入力前の無音/低信頼 segment 抑制、`no_speech_threshold` / `hallucination_silence_threshold` 相当の設定、
または短すぎる segment の large STT 投入回避を検討する。

### STT ノイズ評価 artifact は work/ に隔離する
実環境ノイズや読み上げ評価の録音は、公開 repo の source of truth ではなく実験 artifact として扱う。
ルート直下の `work/` を git 管理外にし、`work/audio-recordings/` に `/ws` 経由で録音した WAV と JSON metadata を保存する。

録音 debug は REST endpoint を増やさず、既存 `/ws` の `debug_recording_start` / `debug_recording_stop` event で制御する。
録音中の audio chunk は通常の会話 STT/VAD へ流さず、debug recorder にだけ保存する。
読み上げ評価では、保存した同じ audio を configured STT にかけ、expected text / transcript / STT elapsed を metadata に残す。

### AudioWorkletNode の処理維持
M1 Phase 1 のブラウザ実装では、`MediaStreamSource -> AudioWorkletNode` だけで止めると
ブラウザによっては音声グラフが pull されず `process()` が継続しない可能性がある。
入力音をスピーカーへ出さないため、`AudioWorkletNode -> GainNode(gain=0) -> destination`
で無音接続して処理を維持する。

### VAD 無音閾値の検出粒度
M1 Phase 2 の AudioWorklet は 512 samples / 16kHz の 32ms チャンクで処理するため、
無音閾値はチャンク境界に丸められる。
合成 scorer の実測では 300ms -> 320ms、400ms -> 416ms、500ms -> 512ms で `processing` に遷移した。

### Phase 3 STT の同期処理
faster-whisper の `transcribe` は同期処理なので、WebSocket のイベントループを塞がないよう
`FasterWhisperSTT.transcribe()` 内で `asyncio.to_thread` に逃がす。
発話終了後の処理は `SpeechSegment -> Transcript -> ParticipationDecision` の DTO 境界を守る。

### Phase 4 Ollama cold start レイテンシー
`qwen2.5:7b` の Ollama 経路は Phase 4 のテキストストリーミングとしては機能するが、
cold start 込みの初回 `reply_text` delta が 17931.7ms だった。
M1 の 800ms E2E 目標には、このままでは届かない。
Phase 5 以降で常駐 warm-up、MLX 切り替え、または事前生成との組み合わせを検討する。

---

## 既知の制約・注意事項

- Safari での AudioWorklet 動作に制限がある可能性。M1 は Chrome 専用で割り切る
- faster-whisper small で日本語精度が不十分な場合は medium に切り替え
  （レイテンシーへの影響を計測してから判断）
- kokoro-mlx の日本語ボイス品質は実測するまで不明

---

## 2026-05-23 追記

### 確定した判断: irodori-tts の Phase 0 導入形態
上の「未解決の疑問」にある irodori-tts 導入形態は、Phase 0 では Tomoko リポジトリ内に
サブモジュールや vendored code を追加しない方針で解決した。

公式の `Aratako/Irodori-TTS-Server` を Tomoko の隣接ディレクトリ
`../Irodori-TTS-Server` に外部サービスとして clone し、`uv sync` と
`GET /health` の 200 応答まで確認済み。
モデル preload は無効で、音声合成モデル本体のロードと実推論は M1 完了後または
TTSBackend 実装時に扱う。

### 確定した判断: Phase 3 以降はマイク入力をエコーバックしない
Phase 1 の配線確認用エコーバックは、Phase 2/3 の実音声テストでは自分の声が返ってきて確認を妨げる。
Phase 3 以降の `/ws` は float32 入力を受けて VAD/STT/参加判断に使うが、同じバイナリは返さない。
クライアントは無音 GainNode 接続で AudioWorklet の処理維持だけを行い、サーバーからの JSON イベントを表示する。

### 確定した判断: Phase 5 SayBackend の音声チャンク単位
macOS `say` はファイル生成型で、M1 Phase 5 では真の PCM 逐次ストリーミングにはしない。
LLM の `text_delta` を句点・感嘆符・疑問符まで蓄積し、文単位で AIFF を生成して `/ws` のバイナリとして送る。
クライアントは受信した AIFF を `decodeAudioData` で `AudioBuffer` にし、次の再生時刻へキューイングする。

実測では synthetic VAD/STT/LLM + real `say -v Kyoko` の VAD 終了から最初の音声チャンクまで 664.1ms。
Ollama cold start を含む実音声 E2E は Phase 4 の既知課題の影響を受けるため、M1 800ms 目標の最終確認は
LLM warm-up / MLX 切り替え後に再測定する。

### 確定した判断: Phase 5 SayBackend のブラウザ向け音声形式
上の「AIFF を生成して `/ws` のバイナリとして送る」という判断は Chrome の手動確認結果により否定する。
M1 の `SayBackend` は Chrome `decodeAudioData` 互換性を優先し、16kHz/16bit の RIFF/WAVE を送る。

`say` は拡張子 `.wav` だけでは `Opening output file failed: fmt?` になるが、
`--data-format=LEI16@16000 -o speech.wav` を付けると WAV を生成できる。

### 確定した判断: Phase 6a emotion 行の分離位置
`EMOTION:<value>` 行の分離は `ThinkFastMode` で行い、`ThinkingEvent(type="emotion")` として
`TomoroSession` に渡す。

`TomoroSession` は emotion イベントをそのまま WebSocket JSON として DOM に送り、TTS には
`text_delta` だけを流す。TTS style は直近の emotion を使う。

### 気づき: LLM が emotion 行の改行を落とす場合がある
Chrome 手動確認で `EMOTION:happy 今日は...` のように、LLM がプロンプトで指定した改行を入れずに
emotion と本文を同じ行で返すケースがあった。

`ThinkFastMode` は `EMOTION:<value>\n本文` だけでなく、許可済み emotion の直後に空白を挟んで本文が続く
`EMOTION:<value> 本文` も分離対象にする。

### 確定した判断: Phase 6b の静止画切り替え
Phase 6b の静止画は `assets/images/tomoko-<emotion>.svg` に配置し、
`TomoroSession` が emotion イベントへ `image` フィールドを追加する。

クライアントは emotion から画像を推測せず、WebSocket で届いた `image` を表示するだけにする。
これにより状態判定と emotion-to-asset の対応はサーバー側に集約する。

### 確定した判断: Phase 6b の声色
M1 の TTS は引き続き `SayBackend` なので、声色は `TTSInput.style` に emotion を入れ、
`say -r` の rate で簡易表現する。

`neutral/happy/surprised/sad/thinking/gentle/excited` はすべて rate にマッピング済み。
irodori-tts の `voice_style` への実マッピングは、irodori backend を Tomoko リポジトリ内に
正式実装するタイミングで同じ `TTSInput.style` から行う。

### 確定した判断: Phase 7 の前に AttentionMode を実装する
上の計画で Phase 6b の次に M2 Phase 7「短期記憶」へ進む流れは否定する。
短期記憶へ進む前に、Phase 6.5 として `TomoroSession` に `attention_mode` を追加する。

`attention_mode` は `ambient` / `engaged` / `cooldown` / `withdrawn` とし、
wake word 後の会話継続、自然な ambient 復帰、明示的に引く状態を表す。
これは将来の wake word 外参加を「誤反応」ではなく、状態に基づく自然な参加にするための前提である。

「あ、聞いてなかった」は、音声処理失敗ではなく `recorded=true` かつ `attended=false` の人格表現として扱う。
常時 STT で `ambient_logs` に残っていても、Tomoko が会話として注意を向けていなかった発話は
直近会話文脈や `conversation_logs` に入れない。

`ambient_logs` には `attention_mode` / `attended` / `participation_mode` を保存し、
`conversation_logs` は `attended=true` の会話ターンだけを保存する。

### 確定した判断: Phase 6.5 AttentionMode の初期実装
`TomoroSession.attention_mode` は `ambient` 初期値で、wake word により `engaged` へ遷移する。
`engaged` / `cooldown` 中の wake word なし発話は、M1.5 では関連度 LLM 判定をまだ入れず、
発話が空でなければ `ParticipationDecision(mode="invited")` として返答対象にする。

無発話による遷移は AudioWorklet の 512 samples / 16kHz チャンクを基準に積算し、
初期値は `engaged -> cooldown` 8秒、`cooldown -> ambient` 8秒。
これは後続の Chrome 手動確認で短すぎる/長すぎる場合だけ変更する。

「静かにして」「今は入らないで」系は `withdrawn` に遷移し、withdrawn 中は
「トモコ、戻って」「トモコ、話して」などの明示的な呼び戻しがあるまで `withdraw` 扱いにする。

### 確定した判断: Phase 6.6.0 は TurnTaking / BargeInDetector として実装する
Tomoko 発話中のマイク入力は止めない。
旧 Unity 実装の `isAITalking` 中に録音処理を止める方針は否定する。

Tomoko は原則として発話中の文を言い切る。
ただし、発話中に「ちょっと待って」「違う違う」「待って待って」「ストップ」などの割り込みが STT で検出された場合は、
相槌・回り込み・新しい質問・緊急割り込みに分類し、次の文を送らない、または仕切り直す。

この判定は VAD に似た独立層として扱う。
VAD が audio chunk を speech/silence に分類するのに対し、BargeInDetector は Tomoko 発話中の transcript を
`echo` / `backchannel` / `soft_interrupt` / `hard_interrupt` / `new_question` に分類する。

speaker echo 判定では semantic embedding を主判定にしない。
会話は原理的に相手の発話と意味的に関連するため、embedding 類似度を主判定にすると自然な返答を echo と誤判定しやすい。
最初は AEC、TTS 再生中の時間窓、文字列/音素寄り類似度、割り込みキーワード、ヒステリシスで判定する。

### 確定した判断: AEC だけでは MacBook 回り込み対策として不十分
Chrome の `echoCancellation` / `noiseSuppression` を有効化しても、MacBook スピーカーから出た Tomoko 音声が
内蔵マイクに回り込み、STT/参加判定に入るケースが残った。

Phase 6.6.0 の初期実装では、クライアント側 AEC に加えてサーバー側で TTS 再生時間窓を推定し、
その窓内の transcript を `BargeInDetector` に通す。
直近 Tomoko 発話と文字列的に近い transcript は `echo` として `observer` 相当に扱い、
「違う違う」「待って待って」「ストップ」などの hard interrupt は通常の参加判定へ進める。

### 確定した判断: Phase 6.6.1 AudioPlaybackControl
TTS バックエンドが `say` / kokoro / irodori のどれでも、実際の再生停止はクライアント側の
`AudioBufferSourceNode` を止める必要がある。
そのため、サーバー主導の `audio_start` / `audio_end` / `audio_control stop` を WebSocket JSON イベントとして追加する。

単一 WebSocket 上ではメッセージ順序が保証されるため、TCP 的な sequence 並べ替えは実装しない。
binary audio chunk は直前の `audio_start.turn_id` に属すると扱う。
`turn_id` は並べ替えではなく、stop / cancel / stale chunk discard のために使う。

### 確定した判断: playback telemetry は事実だけをクライアントから返す
回り込み判定の恒久対応として、サーバー推定だけに頼らず、クライアントから
`playback_started` / `playback_ended` を同じ `/ws` に JSON で返す。

### 確定した判断: 再生中 active chunk は speaker echo 保護区間として扱う
`playback_ended` 後の猶予だけでは、次 chunk 再生中の回り込みが `new_question` として
`attention_engaged_followup` に流れるケースが残った。

サーバーは `playback_started` で `(turn_id, chunk_id)` を active playback chunk として登録し、
対応する `playback_ended` で解除する。
active chunk が存在する間の transcript は、hard interrupt 以外 `echo` / `continue_speaking` として扱い、
通常の参加判定へ流さない。

`playback_ended` 後の speaker echo 猶予は 1200ms では短かったため、2000ms に変更する。

この telemetry はクライアント側判断ではなく、実際に `AudioBufferSourceNode` の再生を予約・終了したという事実だけを送る。
サーバー側でこの情報をどう speaker echo 窓や barge-in 判定に使うかは、実測ログを見てから決める。

### 確定した判断: playback telemetry の chunk_id と回り込み猶予
`playback_started` / `playback_ended` は turn 単位ではなく audio chunk 単位で送る。
payload には `turn_id` / `chunk_id` / `scheduled_audio_time` / `sent_audio_time` /
`audio_context_time` / `performance_now_ms` を含める。

サーバー側では `playback_ended` 受信後 1200ms を speaker echo grace とし、この窓内の transcript は
hard interrupt 以外 `echo` / `continue_speaking` 相当に倒して通常の参加判定へ流さない。
これは MacBook スピーカーから内蔵マイクへの回り込みが、Web Audio の source 終了直後にも遅れて入るため。

### 確定した判断: playback_ended grace 1200ms 判断の否定
上の「`playback_ended` 受信後 1200ms」という判断は、実ログで猶予を少し超えた自己会話候補が
`attention_engaged_followup` に流れたため否定する。

現時点の speaker echo grace は 2000ms とする。
また、`playback_ended` 後だけではなく `playback_started` から対応する `playback_ended` までの
active playback chunk 区間も speaker echo 保護区間として扱う。

### 確定した判断: playback_ended grace 2000ms 判断の否定
上の「speaker echo grace は 2000ms」という判断は、active playback chunk 対応後の実ログを見て否定する。

劇的な改善の主因は `playback_active_chunk` であり、`playback_ended_grace` は終了直後の補助としてだけ発火していた。
確認できた `playback_ended_grace` は秒精度ログ上では終了後およそ1秒以内だったため、猶予は 1200ms に戻す。

今後はログ timestamp をミリ秒単位にして、`playback_ended` から `playback_ended_grace` までの実測差分を確認する。

### 確定した判断: Phase 6.6.1.2 Follow-up 誤起動抑制
回り込みは `playback_active_chunk` / `playback_ended_grace` で実用上問題ない水準まで改善した。
残った問題は回り込みではなく、`engaged` / `cooldown` 中の小さな物音や Whisper hallucination が
`attention_engaged_followup` / `attention_cooldown_followup` として会話継続することだった。

`WakeWordJudge` は follow-up 判定時に、空文字、1〜2文字、低音量の短文、Whisper が無音・ノイズで出しがちな
「ご視聴ありがとうございました」「字幕をご視聴」「お疲れ様です」系を `low_confidence_followup` として
`observer` に倒す。

また、低信頼 observer 発話では attention idle を延長しない。
attention の無音 decay は `TomoroSession.state == "idle"` の無音 chunk だけで積算し、発話中や VAD の無音待ちを
ambient 復帰カウントに混ぜない。

### 確定した判断: STT backend は faster-whisper と MLX Whisper を設定で切り替える
STT は `config/central_realtime.toml` の `inference.stt_backend` で切り替える。
`local_whisper_small` は従来の faster-whisper small、`local_whisper_mlx_small` は
`mlx-community/whisper-small-mlx` を使う MLX Whisper backend とする。

MLX backend は `streaming=true` の場合、VAD が `listening` の間に一定間隔で accumulated audio を transcribe し、
`transcript_partial` を送る。発話終了時の最終 transcript は従来通り `SpeechSegment -> Transcript` の境界で処理する。

2026-05-23 の `make bench-stt` 実測では、同じ `say` 生成音声に対して faster-whisper small が `measured_ms=977.5`、
MLX Whisper small が warm 後 `measured_ms=102.1`。ただし MLX 初回はモデル取得/cache込みで `warm_ms=13404.8`。
実運用では起動直後の warm-up が必要。

### 確定した判断: 起動時 warm-up は FastAPI lifespan に集約する
サーバー起動時の初期化処理は FastAPI lifespan に集約し、WebSocket 接続前に実行する。

STT backend は `warm_up()` を持てる。
`FasterWhisperSTT` はインスタンス生成時点で model load が済むため `warm_up()` は no-op、
`MlxWhisperSTT` は短い無音 `SpeechSegment` を一度 transcribe して、初回のモデル解決/cache/compile コストを払う。

2026-05-23 のキャッシュ済み `_warm_up_app()` 実測では `elapsed_ms=2015.5`。
この時間はサーバー startup に乗るが、初回発話の STT レイテンシーに乗せないための意図的な前払いとする。

### 確定した判断: STT hallucination は参加判定前に filter する
Phase 6.6.1.2 の `WakeWordJudge` 側 low confidence follow-up だけでは、partial 表示や `ambient_logs` へ
Whisper hallucination が流れる問題は残る。

Phase 6.6.2 では `TranscriptFilter` を `TomoroSession` の STT transcript 入口に置き、
partial は `suppress_partial` なら UI に送らず、final は `drop` なら participation 判定にも `ambient_logs` にも進めない。

drop 済み transcript を reason 付きで保存する案もあったが、将来の記憶土台を汚さないことを優先し、現時点では保存しない。
デバッグ用途には `server.session` の `TomoroSession transcript filter ... action=... reason=...` ログを使う。

### 確定した判断: Phase 6.6.3 は最小 hardening に留める
kokoro / irodori TTS へ進む前に、`TomoroSession` の audio turn / playback telemetry 周辺だけを
`asyncio.Lock` で保護する。

現時点の `/ws` 受信ループは `process_audio_chunk()` を await する直列構造なので、真の並行 barge-in にはまだなっていない。
そのため Phase 6.6.3 では actor/queue 化や reply/TTS task 分離までは行わず、
`audio_start` / `audio_end` / `audio_control stop` の二重送信防止、`_audio_sequence` 採番、
`_active_playback_chunks` 更新の保護だけを先に固定する。

ロック内では状態確定だけを行い、`send_event` / `send_audio` / DB / LLM / TTS のような外部 I/O はロック外で実行する。

### 確定した判断: Phase 6.6.4 TomoroSession 責務分割
`TomoroSession` は会話状態機械のオーケストレーターとして残し、audio turn / playback telemetry の細部は
`AudioTurnController` に切り出す。

`AudioTurnController` は `turn_id`、audio sequence、active playback chunk、speaker echo grace を所有するが、
参加判断、attention 遷移、WebSocket I/O は持たない。

`ReplyAudioPipeline` は `ThinkingEvent` を emotion / reply text / TTS flush command に変換するだけの helper とし、
TTS 実行、WebSocket 送信、conversation log 書き込みは `TomoroSession` 側で行う。

この分割後も authoritative な会話 state / attention state は `TomoroSession` が所有し、
WebSocket エンドポイント追加やクライアント側判断への移動はしない。

### 確定した判断: reply 境界は session -> reply -> audio/emotion/image
上の `ReplyAudioPipeline` という境界名と配置は、画像や emotion を扱うには音声寄りに見えすぎるため整理した。

`TomoroSession` は `ReplyPipeline` だけを知り、`ReplyPipeline` が内部で
`ReplyAudioPlanner` / `ReplyEmotionState` / `EmotionImageMapper` を使う。

画像 asset 対応は TTS 変換ではないが reply 表示 event の一部なので、`TomoroSession` 直置きではなく
reply 配下の `EmotionImageMapper` に閉じ込める。

### 確定した判断: reply 境界は session -> reply -> audio/display に改める
上の `audio/emotion/image` 三分割は、表示対象が今後 image だけでなく pose / animation / mouth shape などへ
広がる可能性を考えると細かく切りすぎである。

以後の reply 配下は `audio` と `display` の二系統で考える。
`ReplyDisplayPlanner` が emotion 状態と表示 asset 解決をまとめて所有し、`ReplyPipeline` は
`ReplyAudioPlanner` / `ReplyDisplayPlanner` を束ねて `TomoroSession` に command を返す。

`TomoroSession` は引き続き `ReplyPipeline` だけを知り、image path や将来の表示媒体ごとの対応表を直接持たない。

### 確定した判断: Kokoro MLX TTS は generate_stream を AudioChunkOut に包む
`kokoro-mlx` は `KokoroTTS.from_pretrained()` と
`generate_stream(text, voice, speed, sample_rate)` を使う。

Tomoko 側の `/ws` binary はブラウザの `decodeAudioData` 互換性を維持するため、
Kokoro が返す numpy audio chunk を chunk ごとに RIFF/WAVE に包んで `AudioChunkOut` として送る。
raw PCM をクライアントに解釈させる実装にはしない。

`config/central_realtime.toml` の TTS backend は `kokoro_mlx` に切り替えた。
`say` backend は回帰テストと fallback 実装として残す。

### 確定した判断: Reply/TTS 生成は background task 化する
上の「現時点の `/ws` 受信ループは `process_audio_chunk()` を await する直列構造」という判断は、
kokoro streaming / barge-in 対応に進むために否定する。

参加判断後の reply 生成は background task として起動し、`process_audio_chunk()` はマイク入力処理へ戻る。
`ReplyPipeline` から sentence flush された `tts_text` は TTS queue に即投入し、
TTS worker が順次 `TTSBackend.synthesize()` を streaming 消費して、audio chunk が出るたび `/ws` に送る。

hard interrupt では reply task / TTS worker を cancel し、既存の `audio_control stop` を送って
生成中 TTS とクライアント再生の両方を止める。

### 確定した判断: Kokoro MLX 日本語G2Pは pyopenjtalk 経路を使う
`misaki[ja]` は `unidic` パッケージを依存として入れるが、辞書本体の `mecabrc` は別途存在しない場合がある。
実ログでは kokoro TTS が `fugashi` / `unidic` 初期化に失敗し、音声 chunk を送れなかった。

Tomoko の `KokoroMLXBackend` は、日本語 voice (`jf_` / `jm_`) では kokoro に `language="ja"` を明示し、
kokoro 内部の日本語 phonemizer を `misaki.ja.JAG2P(version="pyopenjtalk")` に差し替える。
これにより `unidic` 辞書本体に依存せず、日本語テキストから RIFF/WAVE chunk を生成できる。

バージョンアップ、壊れた時は
```
uv lock --upgrade-package kokoro-mlx
uv sync
mise exec -- uv run pytest -m unit
mise exec -- uv run pytest -m integration
mise exec -- uv run pytest -m perf --tb=short
make server-debug
```
してあとは動作確認で多分直せる。

### 確定した判断: Kokoro MLX voice は存在確認して fallback する
`mlx-community/Kokoro-82M-bf16` の手元 snapshot には `jf_beta.safetensors` が存在しない。
一方、初期の emotion mapping では `sad` / `thinking` / `gentle` を `jf_beta` に割り当てていたため、
それらの emotion になった返答だけ `Voice file not found` でTTS生成が落ち、音声が出なかった。

`KokoroMLXBackend` は `list_voices()` で利用可能 voice を確認し、選ばれた voice が無ければ
`config/central_realtime.toml` の既定 voice（現時点では `jf_alpha`）へフォールバックする。
emotion による表現は当面 speed mapping で維持する。

### 気づき: 英語・中国語混入はまずSTT hallucinationとして扱う
実ログでは `因为`、`washed`、`TTTT...`、`cave cave cave` のような英語・中国語混入が
`TomoroSession transcript` / `partial transcript` として出ていた。
これは少なくとも入力側の MLX Whisper hallucination であり、返答本文が多言語化している証拠ではない。

ただし混入 transcript が `attention_engaged_followup` に流れると、LLM が入力言語に引っ張られる可能性がある。
低音量のASCII-only transcriptは `TranscriptFilter` で `low_audio_ascii_text` として落とす。
また `prompts/base_persona.md` では本文を必ず日本語だけで返すよう明示する。

### 確定した判断: reply 境界で日本語以外の出力を除去する
上の「英語・中国語混入はまずSTT hallucinationとして扱う」という判断は、返答本文側の英語混入には不足していた。

2026-05-24 の `logs/server-debug.log` では、`gallery gallery...` や `llllllll...` は STT filter で drop/suppress されていたが、
`hear you`、`TAXONOMY`、`Goes from remembering to evaluating.` は `TomoroSession reply_text delta` として出ていた。
つまり少なくとも一部は LLM 返答本文由来である。

プロンプト指示だけでは守り切れないため、`ReplyPipeline` に `ReplyTextSanitizer` を置き、
表示用 `reply_text` と TTS 用 `tts_text` の両方へ流す前に ASCII 英字などの日本語外文字を除去する。
これによりクライアントへ判断ロジックを移さず、単一 `/ws` のままサーバー側 reply 境界で出力契約を守る。

### 確定した判断: Irodori は mlx-audio 版 v3 を Tomoko の TTSBackend として使う
`Irodori-TTS-Server` は OpenAI 互換 HTTP で使えるが、README 上も内部 streaming は未実装で、
実体も MLX ではなく PyTorch/MPS 経路だった。
このため Tomoko の `irodori_mlx` backend は HTTP server wrapper ではなく、
GitHub 最新の `mlx-audio` から `mlx_audio.tts.utils.load_model()` を使って
`mlx-community/Irodori-TTS-500M-v3-8bit` を直接ロードする。

Irodori v3 の `stream=True` は現時点の mlx-audio 実装では `NotImplementedError` なので、
Tomoko 側では既存の sentence flush / TTS queue により文単位で逐次生成する。
生成結果は `/ws` binary 互換を維持するため RIFF/WAVE に包んで `AudioChunkOut` として送る。

実測ではキャッシュ済み短文 `こんにちは。` が 2959.1ms、1 chunk、126,764 bytes。
Kokoro より遅いが、日本語専用品質確認のため `config/central_realtime.toml` の default TTS backend は
`irodori_mlx` に切り替える。

### 確定した判断: Irodori v2 へは streaming 目的では切り替えない
GitHub 最新 `mlx-audio` の Irodori 実装では、`Model.generate(..., stream=True)` が
v2/v3 共通の `irodori_tts.py` 内で `NotImplementedError("Irodori-TTS streaming is not yet implemented.")`
を投げる。

つまり v2 に切り替えても真の streaming は得られない。
v3 は自動 duration prediction と sway sampling があり、v3 README でも recommended とされているため、
レイテンシー目的で v2 へ下げる判断は現時点ではしない。

### 確定した判断: 起動時に TTS も warm-up する
上の Irodori MLX backend は起動後初回生成でモデルロードと最初の短文生成コストが乗る。
このため FastAPI lifespan の `_warm_up_app()` で、STT に続いて設定済み TTS backend の `warm_up()` も実行する。

`_create_default_tts_backend()` は `app.state._default_tts_backend` に backend instance を保持し、
warm-up 済み backend を `/ws` session でも再利用する。

2026-05-24 の cached warm-up 実測では STT 1262.1ms、Irodori MLX TTS 2831.9ms。

### 確定した判断: レイテンシー優先時は Irodori MLX を短い単位に分割して streaming する
mlx-audio の Irodori v2/v3 は `stream=True` が未実装なので、モデル内部から生成途中の音声を受け取る
真の streaming は現時点では使えない。

ただし v3 は `seconds` を明示すると duration predictor をスキップできる。
warm-up 済みの `mlx-community/Irodori-TTS-500M-v3-8bit` では、`num_steps=6` と sway sampling にすると
短い発話単位の生成が 100ms 前後まで下がる。

このため、通常会話の default TTS は `irodori_mlx_stream` とする。
これは text を日本語句読点と最大文字数で短く分割し、各単位を Irodori MLX v3 で逐次生成して
`AudioChunkOut` として出た順に `/ws` へ流す backend である。

既存の `irodori_mlx` は品質寄りの単発 backend として残す。
`irodori_mlx_stream` は内部 diffusion streaming ではないが、Tomoko の TTS backend としては
先頭音声を早く返せる実用的な streaming とみなす。

### 確定した判断: Qwen3-TTS MLX は比較候補として残す
Qwen3-TTS は `mlx-audio` の `stream=True` が使えるため、Irodori より素直な streaming backend にできる。
Tomoko では `qwen3_mlx` backend として実装し、`lang_code="Japanese"`、emotion style は `instruct` と
`speed` に変換する。

比較対象として次の2つを設定に残す。

- `qwen3_tts_mlx_small`: `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit`
- `qwen3_tts_mlx_large`: `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`

キャッシュ済みベンチでは、`うん、わかった。少し待ってね。` に対して
small は first chunk 142.6ms / total 545.3ms、large は first chunk 216.7ms / total 820.5ms。
Irodori stream は first chunk 96.6ms / total 192.7ms で最速だった。

自然さは自動判定できないため、`artifacts/tts-bench-cached/` の WAV を聞いて判断する。
default TTS はレイテンシー優先で `irodori_mlx_stream` のままにする。

### 確定した判断: TTS用のLLM日本語化は Gemma 4 E2B MLX で実験可能
LLM 返答や STT 由来の文に英語が混じる前提で、TTS に渡す直前だけ軽量 LLM で読み上げ用日本語へ正規化する実験を追加した。

`mlx_lm` では複数の Gemma 4 E2B 変換が重み不一致でロードできなかったため、
現時点では `mlx-vlm` 経由で `mlx-community/gemma-4-e2b-it-4bit` を使う。
これは MLX 上で動き、短い TTS 正規化では warm 後 first text 162.7〜243.6ms、total 166.5〜247.3ms だった。

例:
`トモコ、today の meeting は 3pm からだから、schedule を確認して。`
→ `トモコ今日の会議は午後三時からだからスケジュールを確認して`

詳細は `logs/tts-text-normalizer/gemma4-e2b/summary.md` に保存した。

### 確定した判断: TTS直前だけGemmaで日本語化する
Irodori stream は warm-up 済み x1 の短文で first chunk 約96.6ms、total 約192.7msだった。
Gemma 4 E2B の TTS 用日本語化は、起動時 warm-up 後の実測で約163.2msだった。

このため default TTS は `irodori_mlx_stream` x1 のまま維持し、TTS 直前にだけ
`ReplySpeechNormalizer` を挟む。
表示用 `reply_text` は従来どおり `ReplyTextSanitizer` で日本語契約を守るが、
TTS 用 buffer には生の text delta を保持し、英字・時刻・日本語以外の文字体系を検出した時だけ
`mlx-vlm` + `mlx-community/gemma-4-e2b-it-4bit` で読み上げ用日本語へ変換する。

混入なしの日本語は Gemma を通さないので Irodori x1 のレイテンシーを維持する。
混入ありは warm-up 後の Gemma 約163ms + Irodori stream 約193ms で、おおむね360ms台を見込む。
初回ロード 3秒台は FastAPI startup warm-up で前払いする。

### 確定した判断: Irodori stream は品質寄りに x1.5 へ調整する
x1 と x1.2 の `irodori_mlx_stream` + asuka 参照音声は、Gemma 正規化後の長め文で品質が厳しかった。
末尾切れや詰まり感を避けるため、stream unit の `seconds` 推定に品質用スケール x1.5 を掛ける。

2026-05-24 のサンプルでは、同じ正規化文が x1.2 の4.68秒から x1.5 の5.88秒に伸びた。
TTS 実測は first chunk 463.0ms / total 1264.6ms。
レイテンシーは悪化するが、会話品質確認を優先して default は x1.5 とする。

### 気づき: Kokoro MLX もGemma正規化で英語混じり音声長が短くなる
英語混じり文をそのまま `KokoroMLXBackend` に渡すと、`jf_alpha` で 13.25秒の音声になった。
同じ文を Gemma 4 E2B で TTS 用日本語へ正規化してから渡すと、7.175秒になった。

Kokoro 側の実測は raw mixed が 293.9ms、Gemma 正規化後が 142.9ms。
どちらも1 chunkで返っており、この長さでは `generate_stream()` が体感上の複数chunk streaming にはなっていない。
英語混じり対策としては、Kokoro に戻す場合でも Gemma 正規化を前段に置く価値がある。

### 気づき: Kokoro向けGemma正規化は句読点を保持する必要がある
2026-05-24 の Kokoro 再確認では、全文を一度に Gemma 正規化すると
`トモコ今日の会議は午後三時からだからスケジュールを確認して終わったらすぐに教えて`
のように句読点が消え、TTS 用の文章としては一続きになった。

実際の Tomoko 経路に近く、句点ごとに flush してから正規化すると、
`トモコ今日の会議は午後三時からだからスケジュールを確認して` と
`終わったらすぐに教えて` の2つに分かれ、Kokoro は2 chunkで返った。
Kokoroの音質は Irodori stream asuka より良いため、今後Kokoroを戻すなら、
Gemma正規化プロンプトに句読点保持・補完を明示し、必要なら読点や文字数でもflushする方針が有力。

### 確定した判断: default TTS は Kokoro + Gemma句読点保持正規化に戻す
Irodori stream + asuka は duration を x1.5 まで伸ばしても音声品質が厳しく、
Kokoro の方が出力音声クオリティは明確に良かった。
問題は Kokoro ではなく、Gemma 正規化が句読点を落として読み上げ文を一続きにしていたことだった。

このため default TTS は `kokoro_mlx` に戻し、`ReplySpeechNormalizer` のプロンプトは
「文数と順序を保つ」「句読点を保持・補完する」「文末句読点を付ける」を明示する。
また、モデルが句点を落とした場合はサーバー側で文末句読点を補完する。

採用後サンプルでは、英語混じり2文が次のように正規化された。

- `トモコ、今日の会議は午後三時からだから、スケジュールを確認して。`
- `終わったらすぐに教えて。`

Kokoro 実測は first chunk 112.8ms / TTS total 166.6ms / 2 chunks。
Gemma 正規化込みの初回音声は、おおむね 211.1ms + 112.8ms = 323.9ms。

### 確定した判断: Kokoro文節flushでは短すぎる呼びかけを単独出力しない
Kokoro + Gemma の文節ごとサンプルでは、`トモコ、` だけを単独TTSに渡すと末尾に謎の「イ」っぽい音が付いた。
これは短すぎる呼びかけ断片を単独の `generate_stream()` 入力にしたことが原因と判断する。

`ReplyAudioPlanner` は日本語読点も soft flush 対象にするが、10文字未満の断片ではflushしない。
これにより `トモコ、` は次の文節と結合され、
`トモコ、today の meeting は 3pm からだから、` が最初のTTS単位になる。

採用後サンプルでは first chunk 75.6ms / TTS total 191.6ms / 3 chunks。
短すぎる単独呼びかけを避けつつ、文単位より細かいレイテンシーを狙える。

### 気づき: 会話LLMプロンプトだけではTTS用日本語化を任せきれない
2026-05-24 に `qwen2.5:7b` で、現行プロンプト・TTS読み上げ用追加プロンプト・few-shot付き追加プロンプトを比較した。
英語混じり8入力に対して、Gemma正規化なしでTTSに渡せる出力は baseline 2/8、TTS-ready 0/8、few-shot 2/8 だった。

追加プロンプトを強くしても `Zoom`、`GitHub Actions`、`LLM`、`TTS`、`grocery list` などを入力からコピーする傾向が残る。
また、中国語や英字が再発するケースもあり、プロンプトだけで Gemma 層を外す判断はできない。

結論として、上流LLMにはTTS向け日本語を強く要求してよいが、サーバー側の混入検出と
`ReplySpeechNormalizer` の fallback は残す。

### 気づき: Gemma 4 E2B は会話LLMとしてTTS用日本語追従が高い
2026-05-24 に `mlx-community/gemma-4-e2b-it-4bit` を `mlx-vlm` 経由で会話LLM相当としてベンチした。
起動時warm-up後の8入力では、現行base personaだけで TTS ready 8/8、Gemma正規化必要 0/8、
平均 first body 204.7ms だった。

TTS-ready追加プロンプトは 7/8、few-shot付きは 8/8 だったが、追加プロンプトほど出力が長くなり
first body は 312.1ms / 385.2ms に悪化した。
現時点では、Gemmaを会話LLMにするなら、まずは現行personaに近い短いプロンプトで試すのが良い。

混入あり入力では、`qwen2.5:7b` + Gemma正規化より Gemma単体の方が構成が単純で、
初回音声も `約205ms + Kokoro 75〜115ms` 程度を期待できる。
一方、混入なしの純日本語では Qwen が正規化を飛ばせるため、Qwen + Kokoro の方が速い可能性がある。

### 確定した判断: メイン会話推論は Gemma 4 E2B MLX にする
ユーザー判断により、メイン会話推論は `mlx-community/gemma-4-e2b-it-4bit` を `mlx-vlm` 経由で使う。
Kokoro は `kokoro_mlx` のまま使い、TTS直前のGemma正規化は無効化する。
英語混入は許容し、構成の単純さとGemmaのプロンプト追従を優先する。

`ReplyAudioPlanner` は読点による文節flushをやめ、`。！？` の sentence flush だけに戻す。
これにより `トモコ、` のような短すぎる断片がKokoroへ渡らず、文節分割由来の音声破綻を避ける。

実 warm-up では STT 1356.5ms、Kokoro 277.8ms、Gemma conversation 3751.6ms。
起動時に `InferenceRouter` をキャッシュして、warm-up済み Gemma backend をWebSocketセッションでも再利用する。

注意点として、`mlx-vlm.stream_generate()` は `asyncio.to_thread` 上では MLX の thread-local stream エラーになった。
現時点の Gemma会話backendは同じスレッドで同期消費する。
reply生成は background task だが、MLX生成中にイベントループをどの程度塞ぐかは実セッションで確認する。

### 確定した判断: メイン会話推論は LM Studio の Gemma 4 E2B MLX に切り替える
内蔵 `mlx-vlm` backend は動作するが、MLX の thread-local stream 制約により生成を同じスレッドで同期消費する必要があった。
このため Python サーバー側のイベントループを塞ぐ懸念が残る。

LM Studio の OpenAI互換 streaming API は `http://192.168.11.66:1234/v1/chat/completions` で `stream:true` が動作確認済み。
モデルは `gemma-4-e2b-it-mlx`。
LM Studio 側が MLX runtime を保持し、Tomoko サーバーは SSE chunk を読むだけになるため、thread 問題とイベントループ占有のリスクを減らせる。

2026-05-24 の起動時 warm-up 実測では、STT 1854.2ms、Kokoro 279.8ms、LM Studio conversation 243.0ms。
依存同期後の再実行でも LM Studio conversation は 257.1ms で通った。
直前の内蔵 `GemmaMLXBackend` warm-up 3751.6ms と比べて会話 backend の起動時負荷が大幅に下がった。

default `conversation_backend` は `lmstudio_gemma4_e2b` にする。
fallback は Ollama ではなく `local_gemma4_e2b_mlx` にして、LM Studio が遅い/落ちた場合もローカルGemma系に留める。

### 確定した判断: Phase 7 の短期記憶は conversation_logs の role 行を直近文脈に使う
M2 Phase 7 の短期記憶は、既存の `conversation_logs` テーブルに保存済みの role 行
（`role=user` / `role=tomoko`）をそのまま直近会話文脈として使う。

`PostgresConversationLogWriter.read_recent_turns()` は `recorded_at DESC LIMIT N` で取得して時系列順へ戻し、
`ThinkFastMode` は `ConversationTurn(speaker="tomoko")` を OpenAI 互換 messages の `assistant` role に変換する。

`TomoroSession` は参加判定後に user turn を先に保存するため、reply 生成時に読んだ直近文脈の末尾が
現在の user transcript と同一なら除外する。これにより current user message は `ThinkingInput.text` として一度だけ渡る。

短期文脈の上限は当面 `RECENT_CONTEXT_TURN_LIMIT = 12` とする。
hard interrupt や cancel で `reply_done` に到達しなかった Tomoko 返答は `conversation_logs` に保存しない。

### 確定した判断: 止められた Tomoko 返答は interrupted として conversation_logs に残す
上の「hard interrupt や cancel で `reply_done` に到達しなかった Tomoko 返答は保存しない」という判断は否定する。

人間判断により、止められた Tomoko 返答は `conversation_logs.status='interrupted'` として保存する。
`conversation_logs` は role 形式のまま維持し、`status TEXT NOT NULL DEFAULT 'completed'` を追加する。

`ConversationLogStatus` は `completed` / `interrupted` / `cancelled` / `error` とする。
短期記憶の直近文脈には当面 `completed` だけを使う。
`interrupted` は M3 の日記や「言えなかったこと」の材料として使えるように残す。

### 確定した判断: Phase 8 の長期記憶は conversation_embeddings に分離する
M2 Phase 8 のエピソード記憶は、`conversation_logs` を原本として維持し、embedding だけを
`conversation_embeddings` に分離して保存する。

`conversation_embeddings.conversation_log_id` は `conversation_logs.id` を参照し、
`embedding vector(384)`、`model`、`embedded_at` を持つ。
検索は pgvector の cosine 距離で行い、HNSW index を張る。

embedding backend は `intfloat/multilingual-e5-small` を `sentence-transformers` 経由でローカル実行する。
E5 の推奨に従い、検索 query は `query: ...`、保存 passage は `passage: ...` を付けて正規化 embedding を作る。

`TomoroSession` は現在の user transcript を先に `conversation_logs` へ保存するため、
deep memory 検索結果に現在発話そのものが混ざった場合は除外する。

### 確定した判断: Phase 8 の deep/fast 選択
短い相槌や通常の即応は `ThinkFastMode` のままにする。
一方、「覚えてる」「この前」「前回」「先週」「あの時」「話してた」「続き」「その後」などの記憶 cue がある発話、
または長めの相談文は `ThinkDeepMode` に切り替える。

`ThinkDeepMode` は FastMode の emotion/text streaming 契約を変えず、`ThinkingInput.long_term_memory` の
`MemoryHit` を system prompt に追加するだけに留める。
これにより WebSocket protocol やクライアント側ロジックは増やさない。

### 気づき: multilingual-e5-small の startup warm-up
`intfloat/multilingual-e5-small` はキャッシュ済みでも別プロセス初回 warm-up が 8085.2ms だった。
warm 後の query embedding は 33.0ms、pgvector search は 14.4ms。
このため、起動時 warm-up でモデルロードを前払いする判断は妥当。

### 確定した判断: 会話セッション要約は conversation_sessions に集約する
M2 Phase 8.5/8.6 では、会話のまとまりを `conversation_sessions` として DB に明示する。
`conversation_logs` は会話原本の role 行として維持し、`conversation_session_id` で session に紐づける。

`conversation_sessions` は session metadata だけでなく、`summary_text` と `summary_embedding vector(384)` も同じ行に持つ。
要約 embedding 用の別テーブルは当面作らない。
複数 embedding モデル、複数種類の要約、履歴管理、運用上のテーブル分離が必要になった時だけ分離を検討する。

`TomoroSession` は session の開始・終了だけを担当し、会話終了時に `summary_status='pending'` にする。
要約生成と summary embedding はオンライン経路では実行せず、`session_summarizer` または `journalist` の前段 worker が
pending session を追いかけて処理する。

原本は常に `conversation_logs`、`conversation_sessions.summary_text` / `summary_embedding` は検索と文脈復元のための
再生成可能な索引として扱う。

### 確定した判断: 用語集と人格状態は versioned JSONB snapshot として保存する
セッション要約では落ちやすい印象的フレーズ、用語、訂正、関係性マーカー、話し方の癖は、
`persona_lexicon_versions` と `persona_state_versions` に versioned JSONB snapshot として保存する。

各レコードはその時点の全体 snapshot（`lexicon_json` / `state_json`）と、前 version からの変化（`diff_json`）を持つ。
PostgreSQL 側では `jsonb` / jsonpath / GIN index により外部分析しやすくし、アプリケーション側では
`server/shared/models.py` の schema version 付きモデルクラスへ変換して扱う。

これらは原本ではなく、`conversation_logs` / `conversation_sessions` から再生成可能な解釈ログである。
人格変化の正は DB の versioned JSONB snapshot とし、`prompts/persona_history/` は人間向け export として扱う。

### 確定した判断: LLM 文脈取得は ContextSnapshotBuilder に集約する
記憶、session summary、用語集、人格スナップショットが増えてもメイン対話推論時の計算負荷を一定に保つため、
LLM に渡す文脈は `ContextSnapshotBuilder` で組み立てる。

`TomoroSession` は状態遷移と active session ID を決め、`ContextSnapshotBuilder` は読み取り専用で
`TomokoContextSnapshot` DTO を返す。
`ThinkingMode` は DB や JSONB の詳細を知らず、snapshot を prompt に変換する。

depth は `fast` / `normal` / `deep` / `reflective` とし、online 会話では `fast` / `normal` / 必要時の `deep` に留める。
`reflective` は日記や人格更新などの background worker 用とする。

初段の perf 目標は `fast` 20ms、`normal` 50ms、`deep` 100ms 以内。
この snapshot build latency を固定して測ることで、記憶や人格情報が増えた時のレイテンシー悪化を早期に検出する。

### 確定した判断: ContextSnapshotBuilder は時間予算つき best-effort runtime とする
`ContextSnapshotBuilder` は全記憶を成功するまで読む処理ではなく、`ContextBuildPolicy.max_build_ms` に従う
時間予算つき best-effort runtime とする。

応答前 context は latency budget / token budget / depth に従って固定時間内に構築する。
timeout は応答失敗ではなく degraded context として扱う。
同一 conversation session の recent turns を baseline とし、長期記憶・用語集・人格 slice は optional enrichment とする。

PC の性能が上がった場合は、設計を変えずに `ContextBuildPolicy` の `max_build_ms` / top-K /
token budget / enabled source を広げる。

### 確定した判断: context build は parallel DB I/O として扱う
PostgreSQL が唯一の真実であり、context 生成は DB 以外の権威ある状態を読まない。
その前提では、context source を直列に読むより、時間制限付きで複数 DB read を同時に走らせる。

deadline に到達したら未完了 source は cancel / skipped とし、返ってきた候補だけで snapshot を assemble する。
返却順で prompt に詰めるのではなく、same session、attended/completed、relevance、recency、salience、
token budget、deduplication の順で再評価する。

### 確定した判断: ContextBuildTrace を必ず残す
context build の結果には `ContextBuildTrace` を含める。
trace は DB 永続化必須ではないが、debug log / latency log へ出せるようにする。

記録する項目は `budget_ms` / `elapsed_ms` / `timed_out` / `depth` / `included_counts` /
`skipped_sources` / `stage_timings_ms` / `cache_hits` / `source_errors`。

context build timeout は failure ではなく degraded response とする。
最低限 same session recent turns が取れていれば応答は継続する。

### 確定した判断: 単一サーバー運用では Redis ではなく process-local TTL cache を優先する
現時点ではサーバーインスタンスが 1 つなので、Redis を導入せず、
`ContextSnapshotBuilder` 内部の process-local TTL cache で高速化する余地を残す。

cache は source of truth ではなく、DB read の speed-up に限定する。
latest persona state、latest lexicon snapshot、recent completed turns、same session turns、
session summary search result、query embedding result は短い TTL で cache してよい。

一方で、`conversation_logs` への書き込み、active session の authoritative state、`attention_mode`、
playback / barge-in の現在状態、hard interrupt 判定は cache しない。

cache hit / miss / age_ms / ttl_ms は `ContextBuildTrace` に含める。
将来サーバーインスタンスが複数になったり、realtime node と background worker の間で共有 cache が必要になった時点で
Redis 等を検討する。

### 確定した判断: TomoroSession は当面リアルタイム人格ランタイムの管制塔とする
Tomoko のオンライン経路は non-blocking I/O、parallel retrieval、状態機械が同時に動く。
非同期処理は順序を崩すが、状態遷移は順序に依存する。

そのため、状態更新の入口は `TomoroSession` に寄せる。
現時点では `TomoroSession` に複雑さを集約し、実際の依存関係を観察した上で、
安定した境界から小さな component へ切り出す。

状態を持つものと、判定だけを行うものは分ける。
authoritative な会話 state / attention state は引き続き `TomoroSession` が所有する。

### 気づき: Tomoko は non-blocking + parallel + state の総合格闘技である
Tomoko は単なる LLM アプリではなく、リアルタイム音声処理、WebSocket、AudioWorklet、
VAD/STT/TTS、playback telemetry、barge-in、attention mode、conversation session、
長期記憶、pgvector、context budget、LLM 推論制御が同時に干渉するシステムである。

特に難しいのは、非同期処理が順序を崩す一方で、状態機械は順序に依存すること。
状態更新の入口を `TomoroSession` に集約し、重い処理は command 化し、結果を event として戻す。
`session_id` / `turn_id` / `chunk_id` / `context_build_id` で stale result を捨てる。

### 確定した判断: TomoroSession に状態と制御判断を集約する

Tomoko のオンライン経路は non-blocking / parallel / state machine が同時に動く。
音声入力、VAD、STT、playback telemetry、barge-in、attention、conversation session、
context build、LLM、TTS、WebSocket 出力が並行して進むため、メイン層に判断が残ると
状態機械が二重化して見通しが悪くなる。

そのため、メイン層から participation / playback / session lifecycle の判断を剥がし、
`TomoroSession` に状態と制御判断を集約する。

メイン層の責務:

- WebSocket / timer / backend result を `SessionEvent` に変換する
- `TomoroSession` から返された `StateEmission` を WebSocket / log / metrics に流す
- `TomoroSession` から返された `SessionCommand` を実行する
- command の結果を再び `SessionEvent` として `TomoroSession` に戻す

`TomoroSession` の責務:

- `TomoroRuntimeState` を所有する
- 状態変更の入口を `post_event(event)` に寄せる
- event と現在 state から制御判断する
- `TransitionResult(new_state, emissions, commands)` を返す
- 直交状態や優先順位の解決を一箇所に閉じ込める

外部は `get_now_state()` で現在状態を snapshot として読むことはできるが、
state を直接変更しない。

### 確定した判断: event-shaped session runtime を段階導入する

本格的な event-driven architecture はまだ導入しない。
外部 EventBus、pub/sub、状態機械ライブラリ、event sourcing は現時点ではやりすぎ。

代わりに、まず `TomoroSession` 内部だけを event-shaped にする。

- `SessionEvent`
- `TomoroRuntimeState`
- `StateEmission`
- `SessionCommand`
- `TransitionResult`
- `post_event()`
- `_reduce()`

M2 では playback telemetry と transcript finalized の判断集約を優先し、
event queue / drain loop や個別 event dataclass は M3 の競合が増えた段階で厚くする。

timer や background worker は polling してよい。
ただし、状態を変える場合は直接 state を変更せず、必ず `SessionEvent` として
`TomoroSession` に渡す。

### 確定した判断: state と制御ロジックを分ける

`TomoroRuntimeState` は「今どうなっているか」を表すだけにする。
制御ロジックは state 自体ではなく、`TomoroSession` の reducer / resolver に置く。

state の例:

- `attention_mode`
- `vad_state`
- `playback_state`
- `active_session_id`
- `active_turn_id`
- `speaking_turn_id`
- `context_build_id`

制御ロジックの例:

- withdrawn 中の transcript をどう扱うか
- active playback 中の transcript を echo と見るか
- hard interrupt を playback echo より優先するか
- Tomoko turn を `interrupted` として保存するか
- audio stop command を出すか
- reply generation を開始するか

これらは `TomoroSession` の `_resolve_transcript_event()` などに閉じ込める。

### 確定した判断: メイン層には判断済みの command / emission だけを返す

メイン層に低レベルな判断材料を返さない。

悪い例:

```python
{
    "attention_mode": "engaged",
    "playback_active": True,
    "should_stop_audio": maybe,
}
```

これはメイン層に再判断を発生させるため避ける。

良い例:

```python
TransitionResult(
    state=new_state,
    emissions=[
        StateEmission(type="attention_changed", ...)
    ],
    commands=[
        SessionCommand(type="send_audio_control_stop", ...),
        SessionCommand(type="save_tomoko_turn", ...),
        SessionCommand(type="start_reply_generation", ...),
    ],
)
```

メイン層は command を実行するだけにする。
command の結果は event として `TomoroSession` に戻す。

### 確定した判断: await をまたいで中途半端な state を残さない

`TomoroSession` の `_reduce()` は可能な限り同期的・短時間・副作用なしに寄せる。
DB、context build、LLM、TTS、WebSocket send など `await` が必要な処理は
`SessionCommand` として外に出す。

`SessionCommand` の実行結果は、再び `SessionEvent` として `TomoroSession` に戻す。
これにより、非同期処理の結果も必ず `TomoroSession` の state transition を通る。

### 確定した判断: stale result を state 側で捨てる

非同期処理では、古い LLM delta、TTS chunk、context build result、playback telemetry が
遅れて戻ることがある。

そのため、event / command には必要に応じて次の ID を持たせる。

- `session_id`
- `turn_id`
- `chunk_id`
- `context_build_id`

`TomoroSession` は現在の `TomoroRuntimeState` と照合し、現在 state と一致しない結果は
stale として捨てる。

### 気づき: 早すぎる分割より、まず TomoroSession に複雑さを集約する

現時点でいきなり `AttentionStateMachine`、`PlaybackTracker`、`TurnLifecycleManager` などに
完全分割すると、抽象の切り方を間違える可能性が高い。

まず `TomoroSession` に状態と制御判断を集約し、現実の依存関係を観察する。
そのうえで、安定した境界から順に component へ切り出す。

切り出し後も、メイン層との契約は `SessionEvent` / `StateEmission` / `SessionCommand` に保つ。
これにより内部実装を作り変えても、メイン層を薄い adapter のまま維持できる。

### 外部LLMとの会話原文
[会話原文](_reference/2026-05-24-1200_設計評価と改善提案.md)

## 2026-05-24 追記

### 確定した判断: TTSベンチWAVは logs 配下のローカル生成物にする
過去の「TTS聞き比べ用 WAV を `artifacts/tts-bench-cached/` に保存する」という運用は否定する。

`artifacts/` はプログラムが生成する成果物置き場であり、リポジトリのルートに置いて git 管理する対象ではない。
`_tools/bench_tts_backends.py` の出力先は `logs/tts-bench/` とし、WAV は git 管理外のローカル生成物として扱う。

### 確定した判断: 会話体験品質は人間評価と機械メトリクスの対応で最適化する
自然な会話相手らしさは、単一の数式や latency だけで評価しない。

将来の最適化フェーズでは、人間がターン単位またはセッション単位で体験品質を評価し、
同じ単位で latency / VAD / STT / LLM / TTS / attention / barge-in / memory / context build の観測値を残す。
人間評価をゴールドとして、相関・回帰・特徴量重要度から各部品の寄与とトレードオフを分析する。

評価設計の初期案は `_docs/evaluation.md` に置く。

### 確定した判断: Phase 8.5 の会話セッション境界実装
`conversation_sessions` を会話のまとまりとして追加し、`conversation_logs.conversation_session_id` で role 行を紐づける。
既存ログは過去 session を推定せず NULL のまま維持する。

オンライン経路の `TomoroSession` は session の開始・終了だけを担当する。
最初の参加発話で active session を作り、follow-up 中は再利用する。
`cooldown -> ambient` では `end_reason='attention_timeout'`、`withdrawn` では `end_reason='withdrawn'` として閉じ、
閉じた session は `summary_status='pending'` にする。

短期文脈は active session の completed turn を優先し、足りない場合だけ最近の completed turn で補う。
`ThinkingInput.context` の DTO 契約と WebSocket protocol は変更しない。

### 確定した判断: Phase 8.6 のセッション要約索引実装
閉じた会話セッションの要約生成と summary embedding 生成は、online `TomoroSession` 経路ではなく
background `SessionSummarizer` が担当する。

`TomoroSession` は session close 時に `summary_status='pending'` へ進めるだけにする。
`SessionSummarizer` は pending session を `processing` として claim し、session 内の completed turn を時系列で読み、
`InferenceRouter.select("session_summary", "privacy")` で要約を生成する。

要約と embedding は `conversation_sessions.summary_text` / `summary_embedding` に保存し、別テーブルは作らない。
`conversation_sessions.summary_embedding` には HNSW cosine index を張り、deep memory の粗い会話単位検索に使う。
既存 `conversation_embeddings` は turn-level の細かい検索用として残す。

失敗時は `summary_status='error'` と `summary_error` を残し、原本 `conversation_logs` は変更しない。
再実行したい場合は人間または運用スクリプトが status を `pending` に戻す。

### 確定した判断: Phase 8.7 の用語集ログと人格スナップショット実装
`persona_lexicon_versions` / `persona_state_versions` は、Phase 8.7 で DB とプログラム側 DTO の
両方を実装した。

保存形式は versioned JSONB snapshot のまま維持する。
`lexicon_json` / `state_json` はその時点の全体 snapshot、`diff_json` は前 version からの変化だけを持つ。
PostgreSQL では JSONB GIN index で外部分析できるようにし、アプリケーションコードでは
`PersonaLexiconSnapshot` / `PersonaStateSnapshot` / `PersonaVersionDiff` に変換して扱う。

background 側の入口は `background-process/update_persona_snapshots.py` と
`make persona-updater` / `make persona-updater-once`。
online `TomoroSession` 経路では lexicon / persona update を実行しない。

応答生成に使う場合は JSONB snapshot 全量を prompt に直接入れず、
`LexiconTerm` / `PersonaPromptSlice` のような subset DTO に落としてから渡す。

### 確定した判断: Phase 8.8 ContextSnapshotBuilder 初段実装
`ContextSnapshotBuilder` は `server/gateway/context.py` に置き、online 返答前の文脈取得を一箇所へ寄せた。

`TomoroSession` は transcript と active conversation session ID から depth を選び、
builder が返す `TomokoContextSnapshot` から `ThinkingInput.context` / `long_term_memory` /
`context_snapshot` を組み立てる。

初段の depth 運用は、短い通常発話を `fast`、記憶 cue / 長め相談を `deep` とする。
`normal` は policy と builder の読み取り契約を実装済みだが、online default にはまだしていない。
人格・用語集は `PostgresPersonaSnapshotStore` から読み、snapshot 全量ではなく
`LexiconTerm` / `PersonaPromptSlice` として `ThinkingMode` へ渡す。

timeout は failure ではなく degraded context として扱う。
未完了 source は `ContextBuildTrace.skipped_sources` に残し、返せる source だけで応答を継続する。

process-local TTL cache は初段では no-op とし、`ContextBuildTrace.cache_hits` の境界だけを用意した。
実 cache と age / ttl trace は Phase 8.8.1 の運用 hardening で扱う。

### 人間
[アイデア] short/normal/deepなどは応答速度を元に動的に切り替えても良いかも

### 確定した判断: Phase 8.8.1 ContextSnapshotBuilder 運用 hardening
`ContextSnapshotBuilder` の process-local TTL cache は、DB read の speed-up に限定して使う。
cache 対象は `same_session_turns` / `recent_turns` / `session_summaries` / `memory_hits` /
`lexicon_terms` / `persona_slice` とし、active session / attention / playback / barge-in のような
authoritative state は cache しない。

`ContextBuildTrace` には従来の `cache_hits` に加えて、source ごとの `hit` / `age_ms` / `ttl_ms` を
`cache_entries` として残す。cache miss と DB timeout は trace 上で区別し、deadline 超過した source は
cancel して prompt へ入れない。

1 response あたりの context source 実行並列数は `ContextBuildPolicy.max_parallel_sources` で制限する。
現時点では Redis や外部 queue は導入せず、単一サーバー運用の範囲で process-local cache と policy による
parallelism 制御に留める。

### 確定した判断: Phase 8.8.5 TomoroSession 状態管理の最小足場
`TomoroSession` は本格 EventBus / event sourcing / 外部 pub-sub ではなく、まず内部だけを
event-shaped runtime にする。

`TomoroRuntimeState` / `SessionEvent` / `StateEmission` / `SessionCommand` / `TransitionResult` を DTO として追加し、
`get_now_state()` と `post_event()` / `_reduce()` の入口を作った。

初段では playback telemetry を `post_event()` 経由に寄せ、`playback_started` / `playback_ended` で
active playback chunk と echo grace を更新する。
transcript finalized は reducer 入口を用意し、active playback 中の echo と hard interrupt が
`TransitionResult` の emissions / commands として観測できるところまでに留める。

既存の実会話処理はまだ全面 command runner 化しない。
DB write / context build / LLM / TTS / WebSocket send の実行分離は、M3 の自発発話や arrival で競合が増えてから
event queue / drain loop と一緒に厚くする。

### 確定した判断: Phase 9.0 candidate schema / DTO / store
M3 Phase 9.0 は、thinker / arrival precompute が使う候補プールの DB 契約と DTO/store 境界だけを固定した。
LLM evaluator、deterministic source、常駐 loop、online `/ws` 経路や `TomoroSession` からの消費は Phase 9.1 以降に送る。

`utterance_candidates` は `created_at` / `expires_at` / `spoken_at` / `dismissed_at` / `maturity` で lifecycle を表す。
active fetch は `spoken_at IS NULL`、`dismissed_at IS NULL`、`expires_at > now` だけを返し、
priority 降順、created_at 昇順で安定順序にする。

`arrival_candidates` は `computed_at` / `valid_until` / `used_at` で lifecycle を表す。
fresh fetch は未使用かつ `valid_until > now` の最新候補だけを返す。
device filter のため `device_id` は列としても持つが、`ArrivalContextSnapshot` の JSONB snapshot にも含める。

application 層では DB row や JSONB の生 `dict` を持ち回らず、
`UtteranceCandidate` / `ArrivalCandidate` / `ArrivalContextSnapshot` に変換して扱う。

### 確定した判断: Phase 9.1 deterministic source と dedupe
Phase 9.1 の deterministic source は、外部 API / LLM / DB read に依存しない seed 生成として実装する。
初段は `TimeBasedSource` のみで、`ThinkerSourceContext.observed_at` から朝 / 昼 / 夜 / 深夜 bucket を決め、
同じ時刻入力では同じ `CandidateSeed` と `dedupe_key` を返す。

dedupe は専用カラムをまだ増やさず、`utterance_candidates.context_tags` に `dedupe:<dedupe_key>` を保存する。
active candidate に同じ dedupe tag が存在する場合、`insert_seed_candidate_once()` は新規 insert せず `None` を返す。
`spoken_at` / `dismissed_at` 済み candidate は active ではないため、同じ seed を後で再生成してよい。
この方針は Phase 9.1 の最小足場であり、dedupe の検索圧や DB 一意性が必要になった時だけ専用列 / index を検討する。

候補選択の初段は `HighestPriority` とし、priority 降順、urgent 優先、expires_at 昇順、created_at 昇順で安定選択する。

### 確定した判断: Phase 9.2 LLM evaluator
Phase 9.2 の LLM evaluator は、background thinker 内で seed を text-ready candidate へ進めるための部品として実装する。
online `/ws` 経路や `TomoroSession` からはまだ消費しない。

`LLMUtteranceEvaluator` は `InferenceRouter.select("candidate_gen", "privacy")` を使い、
会話原文ではなく `ThinkerEvaluationContext` の要約・用語・人格 subset だけを prompt に渡す。
返答は `should_keep` / `generated_text` / `priority` / `urgent` / `reason` の JSON object に固定する。

backend selection failure、runtime failure、malformed JSON、`should_keep=false` は候補プールへ保存しない。
失敗は background worker 内で閉じ、online 会話を止めない。

保存時は `CandidateStore.insert_evaluated_utterance_once()` で `maturity=1` とし、
dedupe は Phase 9.1 と同じ `context_tags` の `dedupe:<dedupe_key>` を使う。

### 確定した判断: Phase 9.3 arrival precompute
Phase 9.3 の arrival precompute は、入室時の初手を 3 分以内に使える fresh candidate として
`arrival_candidates` に保存する background 部品として実装する。
online `/ws` 経路や `TomoroSession` からの消費は Phase 10 以降に送る。

`ArrivalContextSnapshot` は schema version 付き DTO とし、`computed_at` / `device_id` / `local_time` /
`time_since_last_session_sec` / `session_count_today` / `urgent_candidate_count` / `top_urgent_seeds` /
`persona_hint` を持つ。
urgent seed は active `utterance_candidates` から読み、DB row や JSONB の生 `dict` は application 層に持ち込まない。

arrival prompt の出力 schema は `behavior` / `utterance_text` / `reason` に固定する。
`behavior` は `speak_first` / `wait_silent` / `subtle_react` のみ。
LLM failure、malformed JSON、`speak_first` なのに発話文がない場合は、例外を外へ漏らさず
`behavior="wait_silent"` / `utterance_text=None` / `valid_until=now+3分` の fallback candidate として保存する。

### 確定した判断: Phase 9.4 thinker process loop
Phase 9.4 の thinker は、local background process として `server/thinker/main.py` に集約する。
`background-process/run_thinker.py` は CLI entrypoint だけを担当し、実処理は `ThinkerProcess` に置く。

candidate generation は source → seed 保存 → evaluator → text-ready 保存の順に進める。
source / evaluator / store の失敗は error count と log に閉じ、online `/ws` 経路や background loop 全体を止めない。
arrival precompute も同じ process から定期実行するが、`TomoroSession` からの消費は Phase 10 以降に送る。

`make thinker-once` は一度だけ candidate / arrival を保存し、`make thinker` は
`candidate_generation_loop` と `arrival_precompute_loop` を並行実行する。
Redis / pub-sub / EventBus は導入せず、PostgreSQL の候補プールだけを共有境界にする。

docker-compose への thinker service 追加は、現時点では行わない。
Tomoko アプリ用 Docker image / Dockerfile がまだなく、Apple Silicon / MLX / LM Studio 前提の runtime を
Linux container service として半端に定義すると運用実体とずれる。
M4 のインフラ安定化で app image 方針を決めた後に追加する。

### 確定した判断: Phase 9 全体の完了判定
Phase 9 全体の完了判定では、docker-compose への thinker service 追加を不足として扱わない。
現行の compose は PostgreSQL service のみで、Tomoko アプリ用 Docker image / Dockerfile が未定のため、
service 化は M4 のインフラ安定化で app image 方針を決めてから行う。

Phase 9 は、候補プール schema / DTO / store、deterministic source、LLM evaluator、arrival precompute、
local thinker process loop が動き、`make thinker-once` と Phase 9 の unit / integration / perf 検証が通ることで完了とみなす。

### 気づき: Phase 9 integration test は既存候補データから隔離する
`utterance_candidates` / `arrival_candidates` は実運用データが残る共有テーブルなので、integration test は
fetch 結果全体の先頭だけを前提にしない。
テスト自身が挿入した ID / device_id / context_tags で絞り、開始時と終了時にテスト用 row を削除して隔離する。

### 確定した判断: Phase 10 candidate consumption 分解
Phase 10 は、Phase 9 で作った `utterance_candidates` / `arrival_candidates` を online `/ws` 経路から
`TomoroSession` が消費するための境界を固定する Phase とする。

最初に `session_started` / `idle_timer_elapsed` / `initiative_candidate_loaded` /
`arrival_candidate_loaded` と、`fetch_initiative_candidate` / `fetch_arrival_candidate` /
`start_initiative_reply` / `start_arrival_reply` / `mark_*` 系 command の契約を固定する。

DB read / DB write は `TomoroSession._reduce()` 内で await せず、`SessionCommand` として外へ出す。
adapter は timer / WebSocket / DB result を `SessionEvent` に変換し、最終判断は `TomoroSession` に閉じる。

Phase 10 では Phase 10.5 の event queue / drain loop / 個別 event dataclass へはまだ進まない。
自発発話や arrival と人間発話の競合が実際に読みにくくなった段階で runtime hardening を行う。

### 確定した判断: Phase 10 online candidate consumption 初段
Phase 10 の初段では、`CandidateCommandRunner` を `/ws` adapter 側に置き、
`TomoroSession` は候補 fetch / mark の DB I/O を直接実行しない。

WebSocket 接続時に `session_started` event を投げて fresh arrival candidate を取得し、
45秒ごとの adapter timer で `idle_timer_elapsed` event を投げて initiative candidate 取得可否を判断する。
これは自発発話そのものの固定間隔ではなく、自発発話候補を見に行く判断間隔である。
timer は state の source of truth ではなく、最終判断は `TomoroSession._reduce()` が
`attention_mode == ambient` / `vad_state == idle` / `playback_state == idle` を見て行う。

text-ready candidate は `TomoroSession.start_precomputed_reply()` で既存の reply/audio 出力経路に流す。
arrival / initiative による発話だけでは conversation session を開始しない。
Tomoko の自発発話や入室時の初手に人間が返事した時、通常の参加判断経路で conversation session を開始する。
ただし、follow-up を受けられるよう attention は `engaged` にする。

Phase 10 の人間判断:
- 入室時初手は明示的なスタートボタンを残置し、現状の WebSocket 接続時 `session_started` を維持する
- initiative / arrival の発話可能条件は現状維持する
- candidate の `spoken_at` は reply 開始 command を出した時点でよい
- `maturity=0` / `generated_text is None` の candidate は online で捨てる
- expired cleanup は update 文一発で軽いため online fetch 前に実行してよい
- `wait_silent` / `subtle_react` は現状通り `used_at` を立てる
- `subtle_react` の演出、initiative / arrival の emotion / image は未来へ先送りする
- Phase 10.5 runtime hardening は今は実施しない
- Phase 10 は unit 実装済みで完了扱いとしてよい

### 確定した判断: Phase 11.3 の cached audio 消費
`utterance_candidates.generated_audio` / `arrival_candidates.utterance_audio` は、online 経路では
TTS 推論を省略するための再生成可能 cache として扱う。

`CandidateCommandRunner` は `maturity >= 2` かつ `generated_audio` がある initiative candidate を優先して選び、
`start_initiative_reply` / `start_arrival_reply` payload に cached audio を渡す。
`TomoroSession.start_precomputed_reply()` は cached audio があれば `TTSBackend` を呼ばず、
`audio_start` の後に binary chunk をそのまま送る。

現行の既存 TTS 経路に合わせ、precomputed reply のイベント順序は
`reply_text` → `audio_start` → binary audio → `reply_done` → `audio_end` とする。
`audio_end` と `reply_done` の順序を変更する場合は、既存 TTS 経路の互換性をまとめて確認してから行う。

### 確定した判断: Phase 11 pregenerator 初段
Phase 11 の初段では、`generated_audio` を「即再生できる最初の RIFF/WAVE chunk cache」として扱う。
複数 audio chunk を完全に事前生成して順序付きに保存する設計は、単一 `generated_audio` カラムでは表現が弱いため、
必要になった時に別テーブルまたは JSONB manifest を検討する。

`UtterancePregenerator` は background thinker 側でだけ動かし、online `/ws` 経路からは呼ばない。
対象は active `maturity=1` かつ `priority >= 0.8` かつ `generated_text` ありの candidate とし、
TTS 失敗時は candidate を壊さず warning log と `error_count` に閉じる。

`ThinkerProcess.run_once()` は candidate generation → pregeneration → arrival precompute の順に実行する。
`candidate_generation_loop` でも candidate generation の直後に pregeneration を実行するが、
外部 queue / pub-sub は導入しない。

### 確定した判断: Phase 11.3 audio_end / reply_done 順序の補正
上の「precomputed reply のイベント順序は `reply_text` → `audio_start` → binary audio →
`reply_done` → `audio_end` とする」という判断は否定する。

Phase 11.3 以降は、既存 TTS 経路も含めて PLAN の順序を正とし、
`reply_text` → `audio_start` → binary audio → `audio_end` → `reply_done` の順に送る。
`reply_done` はテキスト応答完了だけでなく、その返答の audio turn 終了後に届く完了イベントとして扱う。

### 確定した判断: Phase 11 multi-chunk 事前生成の保存先
`UtteranceCandidate.generated_audio` は引き続き「即再生できる最初の RIFF/WAVE chunk cache」として扱う。
これは TTS backend 出力から得られる再生成可能 cache であり、音声原本ではない。

複数 audio chunk を完全に事前生成して保存する場合は、`generated_audio` カラムや JSONB manifest に詰めず、
`pregenerated_audio_chunks` 別テーブルに分離する。
chunk は `utterance_candidate_id` / `chunk_index` / `audio_data` / `audio_format` / `is_last` で表し、
読み出し時は `chunk_index` 昇順を正とする。

`maturity=2` は `generated_text` と `generated_audio` の両方がある candidate と定義する。
multi-chunk 保存は将来の完全事前生成用の足場であり、現時点の gateway は first chunk cache を
`generated_audio` から送る。

### 確定した判断: Phase 12 diary entry 初段
Phase 12.0 では `diary_entries` を派生テキストとして追加した。
原本は引き続き `conversation_logs` / `ambient_logs` / `conversation_sessions` / candidate lifecycle であり、
日記はそれらから再生成可能な解釈ログとして扱う。

`DiaryEntry` は `diary_date` / `body_text` / `source_session_ids` / `source_candidate_ids` /
`mood` / `schema_version` を持つ。
同じ日付の日記を再生成する時に overwrite するか version を積むかはまだ確定していないため、
writer 実装前に Phase 12.0 の残項目として判断する。

### 確定した判断: Phase 12 journalist 実装
上の「同じ日付の日記を再生成する時に overwrite するか version を積むかはまだ確定していない」
という状態は、2026-05-24 の Phase 12 実装で解決した。

同日 diary 再生成は overwrite ではなく version 方式にする。
`diary_entries.diary_version` を持たせ、同じ `diary_date` に対する追加生成は `1, 2, 3...` と版を積む。
日記は原本ではなく、`conversation_logs` / `ambient_logs` / `conversation_sessions` /
`utterance_candidates` から再生成可能な解釈ログであるため、古い版を上書きしない。

`JournalistInputBuilder` は日付範囲内の completed session summary、completed / interrupted turn、
ambient の count と短い抜粋、dismissed / unspoken candidate を `JournalistInputSnapshot` DTO にまとめる。
prompt 層へ DB row や JSONB の生 dict は渡さない。
ambient は raw 全量を渡さず、件数と短い抜粋に絞る。

`DiaryWriter` は background journalist 側でのみ動かし、online `/ws` 経路には入れない。
`InferenceRouter.select("diary", "privacy")` を使い、empty / malformed 相当の空出力は error log に閉じ、
原本データを変更しない。

`DiarySource` は昨日または直近 diary から短い `CandidateSeed` を作る。
dedupe key は `diary:<diary_id>` とし、`CandidateSeed` 側で `dedupe:diary:<diary_id>` tag に変換される。
diary 本文全量ではなく、最初の短い文だけを話しかけ候補にする。

docker-compose の journalist service 追加は現時点では行わない。
Tomoko アプリ用 Docker image / Apple Silicon MLX / LM Studio runtime 方針が M4 で決まるまで、
Phase 12 は `background-process/run_journalist.py` と `make journalist-once` / `make journalist` までを完了範囲とする。

### 確定した判断: Phase 13 monitor 初段
`InferenceRouter.select()` は online 経路で重い probe を実行しない。
実測は `BackendHealthMonitor` が background 的に行い、`inference_metrics` に
backend name / task type / latency / error / measured_at を保存する。

probe failure は例外を外へ投げず、`InferenceMetricSample.error` として記録する。
router は latest metric を読むだけで、`latency_ms is None` の error sample は fallback 判断対象にする。
`priority="privacy"` の場合は引き続き `privacy_allowed=False` backend へ fallback しない。

### 確定した判断: Phase 14 edge split 初段
Phase 14 は一度に完全な edge / gateway process split へ進めず、まず presence と duplicate 判定の
DB / DTO / 純粋判定器を固定する。

ブラウザは引き続き dumb client のままにする。
Phase 14 の edge は Python server 側の責務分離であり、ブラウザへ VAD / STT / duplicate / resolver 判断を移さない。

`presence_reports` / `edge_status` は edge の観測状態を保存するが、音声 bytes は保存しない。
保存するのは `device_id` / `observed_at` / `audio_level_db` / `transcript_id` / optional transcript text /
edge status metadata までとする。

`DirectSpeakerResolver` は同一時間窓の `PresenceReport` から正規発話元 edge を選ぶ純粋判定器であり、
DB write を持たない。
初段は `audio_level_db` 最大、同値なら recency、さらに同値なら `device_id` で deterministic に決める。

`DuplicateSpeechFilter` は時間窓、device 差、文字列類似度で二重 STT / 回り込みを duplicate として抑制する。
会話は意味的に近い発話が自然に続くため、embedding 類似度は主判定にしない。
hard interrupt keyword は duplicate より優先し、別 edge が同じ語を拾っていても捨てない。

`config/edge_kitchen.toml` は `node.role="edge"` / `device_id="kitchen"` として追加した。
`make edge-kitchen` / `make gateway` は local multi-process smoke 用の足場であり、
docker-compose service 化は app image / Apple Silicon MLX / LM Studio runtime 方針が M4 で決まるまで行わない。

### 確定した判断: Phase 14.3 edge / gateway protocol
Phase 14.3 では edge / gateway 間を plain WebSocket 1 本の JSON text event protocol として実装した。
HTTP API / gRPC / Redis / durable queue は導入しない。

edge -> gateway は `hello` / `presence` / `speech` / `playback_started` / `playback_ended` を送る。
`speech` は `device_id` / `event_id` / `transcript_id` / `transcript` / `audio_level_db` /
`observed_at` / `sent_at` を持つが、音声 bytes は持たない。

gateway -> edge は既存 TomoroSession の JSON event（`reply_text` / `emotion` / `reply_done` など）を返す。
gateway 側 TomoroSession は remote edge 経路では TTS を持たず、edge が local TTS で audio chunk を作ってブラウザへ送る。
中央サーバーの既存 `/ws` と `/` client 配信は維持し、中央 PC 単体でもブラウザ client として使える。

`GatewayEdgeProtocolHandler` は presence report、primary edge 判定、duplicate 判定、stale / duplicate event discard を担当する。
fresh な `speech` だけを `TomoroSession.process_transcript()` へ渡す。

未実装の運用 hardening は reconnect backoff / heartbeat / connection health UI / 長時間 soak test。
ack / retry / durable queue は、リアルタイム観測として古い発話を捨てる方針に反するため現時点では入れない。

### 確定した判断: 別プロセス起動用 Makefile entry
2026-05-25 時点では、local process として起動する `session-summarizer` / `persona-updater` /
`thinker` / `journalist` はすべて `Makefile` から起動する。
各 target は `--config $(CENTRAL_CONFIG)` を明示し、暗黙 default config に頼らない。

`gateway` / `edge-kitchen` は `CENTRAL_CONFIG` / `EDGE_KITCHEN_CONFIG` を使う。
各 background process は `logs/session-summarizer.log` / `logs/persona-updater.log` /
`logs/thinker.log` / `logs/journalist.log` に分けて出す。

`background-once` は once target を直列実行する一括入口として使う。
watch 系 process は常駐するため、`background-watch` は別ターミナルで起動すべき target 名を表示するだけにする。
常駐 process の集合を Makefile dependency として直列実行しない。

### 確定した判断: Phase 10.5 TomoroSession runtime hardening
Phase 10.5 は、外部 EventBus / Redis / event sourcing へ広げず、`TomoroSession.post_event()` の内側だけを
event queue / drain loop 化する。

複数の adapter / timer / command result が同時に `post_event()` しても、`TomoroSession` 内で enqueue 順に
`_process_event()` を通す。これにより、arrival / initiative / playback telemetry / transcript event の処理順を
メイン層ではなく `TomoroSession` に閉じ込める。

Phase 10 の candidate fetch には `request_id` を持たせる。
`CandidateCommandRunner` は DB read 結果を `initiative_candidate_loaded` / `arrival_candidate_loaded` event として
戻す時に request id を引き継ぎ、古い result は `stale_result` として捨てる。

個別 `SessionEvent` dataclass 化は今回は見送る。
既存の文字列 event 契約でも、queue / drain と request id で現在の競合はテスト可能になったため、
payload contract がさらに読みにくくなった段階で分ける。

### 確定した判断: LFM 2.5 1.2B JP MLX 会話 backend
`lfm2.5-1.2b-jp-mlx` は、Phase 10.5 の `TomoroSession` runtime hardening と直交するため、
`TomoroSession` / command runner には触れず、`InferenceRouter` 配下の `mlx_lm` backend として追加した。

既存の `gemma_mlx` backend は `mlx-vlm` 経由で Gemma 4 E2B を扱う専用実装として残す。
LFM は causal LM 系の MLX model として `mlx_lm.load()` / `mlx_lm.stream_generate()` を使う汎用
`MLXLMBackend` に載せる。

`config/central_realtime.toml` には `local_lfm25_12b_jp_mlx` を定義したが、10.5 作業中の実行構成を
変えないため、現時点では default `conversation_backend` は `lmstudio_gemma4_e2b` のまま維持する。
切り替える場合は `conversation_backend = "local_lfm25_12b_jp_mlx"` に変更してから warm-up と実測を行う。

### 確定した判断: Phase 10.5 の開始理由と priority policy
Phase 10.5 の開始理由は、runtime state / command payload では
`wake_word` / `followup` / `initiative` / `arrival` / `resume_unspoken` に正規化する。

`ParticipationDecision.mode` の `called` / `invited` は ambient log や user turn の参加モードとして残すが、
`TomoroRuntimeState.last_start_reason` と `conversation_sessions.start_reason` では
`wake_word` / `followup` を使う。

`resume_unspoken` は現時点では予約語であり、interrupted turn や diary 由来 candidate を実際に再提示する経路は
別 Phase で実装する。

priority policy は `TomoroSession` に閉じ込める。
hard interrupt は active playback echo より優先し、withdrawn は follow-up / initiative を抑制し、
human transcript 後に遅れて届く initiative / arrival result は `not_speakable` または `stale_result` に倒す。

### 確定した判断: LFM 2.5 1.2B JP MLX の実 repo id と採用
上の「`lfm2.5-1.2b-jp-mlx` を使う」という表記は、人間向けの短い呼び名としては維持できるが、
Hugging Face / `mlx_lm.load()` に渡す repo id としては誤りだったため否定する。

一度 `LiquidAI/LFM2.5-1.2B-JP-MLX-bf16` で実測したが、ユーザー指定により
`lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit` を正式採用する。
Hugging Face の model card でも MLX 4-bit 版として案内されており、`mlx_lm.load()` で利用できる。

`config/central_realtime.toml` の `local_lfm25_12b_jp_mlx.model` は
`lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit` とし、`conversation_backend` は
`local_lfm25_12b_jp_mlx` のまま維持する。

キャッシュ済み実測では、プロセス内 cold load + generate の first delta は 19022.9ms、total は 19041.4ms。
同一 backend instance の warm 生成は first delta 20.6ms、total 39.0ms 平均だった。
FastAPI startup warm-up 経路では LFM conversation warm-up が 3478.3ms で、WebSocket 接続前に前払いできる。

### 確定した判断: CoreML STT / Kokoro CoreML backend 初段
Whisper CoreML は `WhisperCoreMLSTT` として追加した。
`whisper.cpp` の `whisper-cli` と WhisperKit の `whisperkit-cli` の両方を、設定の `command` で差し替える。
既存 STT streaming interface に合わせて rolling buffer partial は返せるが、現状の CLI 呼び出しは毎回プロセスを起動するため、
実用的な online 経路にするには WhisperKit `serve` などの常駐プロセス化が必要。

Kokoro CoreML は `kokoro say` CLI / optional Python object を包む `KokoroCoreMLBackend` として追加した。
`generate_stream` を持つ Python object や IPA 不要 voice では streaming を試す。
ただし Homebrew `kokoro` 0.11.0 では Japanese voice が生テキストで落ち、`misaki[ja]` で IPA 化すると動く一方、
`--ipa` と `--stream` が同時利用できない。
そのため日本語 Kokoro CoreML は現時点ではファイル生成型 fallback とし、default TTS は `kokoro_mlx` のまま維持する。

実測では、Whisper MLX small は warm 後 103.0ms、WhisperKit CLI CoreML small は 4755.6ms。
Kokoro MLX は first 87.9ms / total 88.0ms、Kokoro CoreML は first 4816.4ms / total 4816.5ms。
Kokoro 聞き比べ WAV は `logs/kokoro-mlx-coreml-bench/kokoro_mlx.wav` と
`logs/kokoro-mlx-coreml-bench/kokoro_coreml.wav` に保存した。

### 確定した判断: WhisperKit serve backend 初段
上の「実用的な online 経路にするには WhisperKit `serve` などの常駐プロセス化が必要」という判断を受け、
`WhisperKitServeSTT` を追加した。

`whisperkit_serve` backend は `url` の `/health` を確認し、起動済みならそのまま
`/v1/audio/transcriptions` に multipart `file` を送る。
未起動なら backend instance が `whisperkit-cli serve --model <model> --language ja --prompt ともこ`
を起動し、healthy になるまで待ってから同じ HTTP 経路で transcribe する。
Tomoko の `/ws` endpoint や `TomoroSession` には触れず、STT backend 境界内に閉じる。

実測では、同じ `say` 合成音声で MLX Whisper small が warm 後 103.8ms、
WhisperKit serve small が auto-start warm 4791.6ms / 常駐後 214.3ms だった。
CLI 毎回起動の 4755.6ms より大幅に改善したが、現時点では MLX Whisper の方が約2倍速い。
default STT は `local_whisper_mlx_small` のまま維持する。

### 気づき: CoreML STT は同時実行時の余白として評価する
単体 STT latency では MLX Whisper small が約103ms、WhisperKit serve small が約214-218ms で、
MLX が約2倍速い。

ただし `_tools/bench_stt_backends.py` に concurrent workload 測定を追加して実測したところ、
`kokoro_mlx` TTS を同時に走らせた場合は MLX Whisper が平均203.5msまで伸び、
WhisperKit serve は平均215.9msでほぼ横ばいだった。
この条件では単体測定ほどの差はなく、CoreML/ANE 側へ STT を逃がす意味はある。

一方で短い LFM MLX conversation load では MLX Whisper は平均112.8msで、CoreML 側との差はまだ大きかった。
CoreML STT の採用判断は単体 latency ではなく、実会話に近い TTS/LLM 同時負荷時の tail latency で見る。

30 runs の tail latency 実測では、MLX Whisper は idle p95 106.3ms、`kokoro_mlx` TTS 同時 p95 165.2ms、
`kokoro_mlx` + LFM MLX 同時 p95 165.7ms だった。
WhisperKit serve CoreML は同条件で p95 222.0ms / 216.9ms / 216.0ms と安定していた。
この測定では、CoreML は固定 200ms レーンとして設計しやすい一方、速度面の default はまだ MLX STT が優勢。

### 気づき: Supertonic-3 CoreML TTS smoke
Supertonic-3 CoreML (`FluidInference/supertonic-3-coreml`) は日本語 `ja` で smoke 成功した。
`こんにちは、トモコです。今日は少しだけ話してみます。` から約4.35秒の音声を生成し、
warm 合成5回は平均102.4ms、min 98.9ms、max 112.8ms だった。
モデルロードは約5.5秒なので startup warm-up 前提なら、TTS 候補としてかなり有望。

注意点として、Hugging Face cache 内の `.mlpackage` は symlink になっており、
`coremltools` の prediction 時に CoreML compiler が `weight.bin` を見失って落ちた。
実ファイルとして通常ディレクトリへコピーすると動くため、`_tools/bench_supertonic_coreml_tts.py` は
`shutil.copytree(..., symlinks=False)` で `logs/supertonic-coreml-smoke/model` に展開してから実行する。

FluidInference の CoreML repo には `M1` しか voice style が無かった。
女性声の評価用に `Reza2kn/supertonic-3-coreml` の `F1`-`F5` style JSON を使うと、既存 CoreML package と互換で動いた。
日本語 smoke では F1 112.6ms、F2 111.3ms、F3 148.9ms、F4 108.1ms、F5 113.9ms。
音質評価用 WAV は `logs/supertonic-coreml-smoke/female/F*/ja-F*-run1.wav` に保存した。

### 確定した判断: embedding backend は BGE-M3 へ切り替える
`intfloat/multilingual-e5-small` 384次元 backend は、ライセンス面では大きな問題はないが、
会話ログ・セッション要約・日本語/英語混在の長期記憶検索の基盤としては、より評判が良く 1024次元の
`BAAI/bge-m3` を採用する。

BGE-M3 は Hugging Face 上で MIT license、1024次元、100以上の言語、dense / sparse / multi-vector を
扱えるモデルとして公開されている。Tomoko では当面 sentence-transformers 経由の dense embedding だけを使う。

embedding 空間が変わるため、旧 e5 embedding と BGE-M3 embedding は混ぜない。
`docker/postgres/init/006_bge_m3_embeddings.sql` で `conversation_embeddings.embedding` と
`conversation_sessions.summary_embedding` を `vector(1024)` に変更し、既存 e5 embedding は削除して再 backfill する。

実測では、初回 HF download + first embed が 36990.5ms、cache 済み fresh process の first embed が 7838.9ms、
同一 process warm query が 32.8ms。online 経路では startup warm-up 済み backend を使う前提にする。

### 確定した判断: 任意ダウンロード系モデルの扱い
Tomoko 本体コードは MIT のまま維持するが、モデル重みは repo に同梱しない。
MIT / Apache-2.0 など permissive な default / evaluation model は `make download-models` で取得する。

LFM2.5 (`lfm1.0`) や Supertonic-3 CoreML (OpenRAIL family) は custom / OpenRAIL 系として扱い、
`make download-optional-models` で明示的に取得する。README には license と optional download の扱いを記録する。

### 気づき: psycopg LGPL-3.0-only の扱い
`psycopg[binary]` は LGPL-3.0-only dependency だが、Tomoko が通常の Python dependency として import して
使う範囲では、Tomoko 本体コードを LGPL に変更する必要はない。

注意すべきなのは、psycopg 自体を改変して再配布する場合、または wheel / binary をアプリ配布物に同梱する場合。
その場合は LGPL / GPL ライセンス文を添付し、psycopg が使われていることを明示し、ユーザーが該当ライブラリを
差し替えられる配布形態を保つ必要がある。現状の開発リポジトリでは PyPI 依存として取得するため、
README の third-party note に留める。

### 確定した判断: default TTS は Supertonic-3 CoreML F1 に切り替える
人間の音質評価により、Supertonic-3 CoreML の女性 voice style では F1 が圧倒的に品質が良く、
Kokoro MLX / Irodori MLX より Tomoko の声として採用しやすいと判断した。

`SupertonicCoreMLBackend` を `TTSBackend` として追加し、`config/central_realtime.toml` /
`config/edge_kitchen.toml` の default `tts_backend` は `supertonic_coreml_f1` にする。
voice style は `F1`、language は `ja`、compute units は `CPU_AND_NE`、モデル展開先は
`models/supertonic-3-coreml` とする。

Supertonic は CoreML 内部生成の逐次 chunk streaming ではなく、Tomoko 側の sentence flush ごとに
1つの WAV chunk を返す backend として扱う。実測では startup TTS warm-up 7963.4ms、warm 合成は
4.35秒音声に対して 104.7ms。起動時 warm-up 前提なら会話レイテンシー面でも十分採用可能。

ライセンスは OpenRAIL family なので、モデル重みは repo に同梱しない。
`make download-optional-models` か初回起動時の明示的な取得で扱う。

## 2026-05-25 追記

### 確定した判断: 自発発話は desire / speakability / policy に分ける
Phase 10 の 45 秒 idle timer + highest priority candidate 消費は、候補消費の足場としては維持する。
ただし、それを「45 秒ごとに機械的に話す」仕組みとして育てるのは否定する。

次段階では、Tomoko 側の「話したい欲」と、状況側の「今話してよい度合い」を分けて扱う。

- `TomokoDesireState`: Tomoko が話したい内圧
- `SpeakabilityState`: presence / activity / focus / rejection などの状況 signal
- `PersonalityDynamics`: 話したがり / 黙りたがり / 好奇心 / 遠慮 / 感受性 / 遊び心
- `CandidateSpeakPolicy`: desire、speakability、personality、candidate metadata から決定的に採点する純粋判定器

`TomoroSession` は引き続き最終 gate を担当する。
`withdrawn`、VAD listening / processing、playback 中、stale result、hard interrupt 直後などは、
desire や personality が高くても破れない hard gate とする。

### 確定した判断: 状態管理から推論の余地を減らす
自発発話の面白さは LLM を state machine に混ぜることではなく、
LLM や thinker が構造化した候補と理由を作り、状態側がそれを決定的に消費することで出す。

LLM の役割:
- 候補文を作る
- なぜ話したいかを説明する
- `urgency` / `intrusion_risk` / `emotional_need` / `reason` を付ける
- score が境界帯の時だけ、今出すのが自然かを JSON で判断する

状態 / policy の役割:
- 今は `ambient` / `idle` / playback idle かを見る
- rejection / acceptance / focus / presence の load average を見る
- desire が threshold を超えたかを見る
- candidate が期限内で text/audio ready かを見る
- stale result を捨てる

これにより、なぜ話したか・なぜ話さなかったかを unit test と log で説明できる。

### 確定した判断: 話したい欲は load average 的にモデル化する
Tomoko の desire は単発フラグではなく、OS の 1分 / 5分 / 30分 load average のような
指数移動平均として扱う。

短期 desire は候補や presence に素早く反応し、長期 desire はゆっくり溜まる。
発話後、無反応、拒否発話、深夜、長時間 presence 不明などで decay / penalty をかける。

`ambient_logs` がないことは「人がいない」と断定しない。
`ambient_logs` は STT まで到達した発話ログであり、無言で PC の前にいる状態や集中状態とは区別できない。
presence 判定には `presence_reports`、audio level、VAD activity、last human speech age を合わせる。

### 確定した判断: 性格は発話内容だけでなく desire の増減に効かせる
Tomoko の personality は、プロンプト上の話し方だけでなく、desire gain / decay / threshold に影響させる。

- `talkativeness`: 話したい欲の溜まりやすさ
- `restraint`: 発話 threshold と遠慮
- `curiosity`: observation / question 候補への反応
- `attachment`: presence や人間への構いたさ
- `sensitivity`: 拒否後の引き方
- `playfulness`: 軽い茶々や短い候補の出やすさ

ランダム性は毎回の乱数ではなく、1時間程度でゆっくり drift する mood として扱う。
これにより「今日は少し話したがり」「さっき静かにしてと言われたので控えめ」のような変動を、
状態遷移の非決定性ではなくスコア補正として表現する。

### 確定した判断: LLM judge は境界ケースだけに使う
オンライン自発発話で LLM を常時発話可否判定器にしない。
`CandidateSpeakPolicy` が明確に `speak` / `wait` を決められる場合は LLM を呼ばない。
score が中間帯の時だけ、candidate text / reason / recent feedback / presence signal / desire level を渡し、
`speak_now` / `wait` / `defer` の JSON を返させる。

LLM judge result も直接 state を変更せず、`SessionEvent` として `TomoroSession` に戻す。
到着時点で人間発話や attention change と競合していれば stale / not_speakable として捨てる。

### 確定した判断: TomoroSession は接続状況を抽象 state として持つ
複数クライアント同時対応では、`TomoroSession` が「今どこへ音声を出せるか」を知る必要がある。
ただし WebSocket object や接続一覧そのものを `TomoroSession` に持たせるのは否定する。

接続管理は adapter / gateway 側の `ClientConnectionRegistry` が担当し、
`TomoroSession` は `ConnectedOutputState` snapshot だけを持つ。

- `ClientConnection`: connection id / device id / role / audio-display capability / last seen
- `ConnectedOutputState`: active device / audio target availability / display target availability / connected counts / playback state by device

`audio_target_available=False` の時は、initiative / arrival の candidate があっても online 発話を開始しない。
これは desire や candidate priority とは別の hard gate として扱う。
候補生成は background thinker が続けてよいが、出力先がない状態で runtime が話し始めてはいけない。

### 確定した判断: 現時点では long-lived central session へは進めない
接続状態 DTO と registry は、将来の long-lived central `TomoroSession` に向けた足場である。
今回の実装では既存の「WebSocket 接続ごとに Session を作る」構造を維持する。

このため、接続がない時に `/ws` 側の idle loop が動かない現状はそのまま残る。
ただし Session runtime 自体は、接続がない output state では自発発話を始めない契約になった。
central に 1 つの長寿命 Session を置き、複数 client / edge を registry 経由でぶら下げる変更は別 Phase とする。

### 確定した判断: Phase 10.6 policy は candidate runner 側で組み立てる
`TomoroSession` に desire / speakability 用の DB read や LLM judge 実行を持たせる案は否定する。
Phase 10.6 では、`CandidateCommandRunner` が active candidate fetch 後に
`TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` / candidate metadata の snapshot を組み立て、
`CandidateSpeakPolicy` の結果だけを `TomoroSession` に `SessionEvent` として戻す。

`TomoroSession` は引き続き final gate と stale request check の所有者であり、
`wait` / `needs_llm_judge` / `speak` の decision を現在 state と照合して消費する。
LLM judge は境界 score の時だけ command として外へ出し、未設定・失敗・malformed result は安全側に `wait` へ倒す。

### 確定した判断: initiative feedback は source / topic / emotional_need scope で残す
Phase 10.6 の feedback は、自発発話全体を一律に上げ下げしない。
`initiative_feedback_signals` に `source` / `topic` / `emotional_need` / `feedback_kind` / `score` を保存し、
候補取得時に `CandidateCommandRunner` が同じ scope の recent feedback を summary して
`CandidateSpeakPolicy` へ渡す。

`TomoroSession` は feedback の分類と保存の入口だけを持つが、集計や DB read は持たない。
自発発話後の「静かにして」「それ今じゃない」は rejection/defer として scoped penalty にし、
「うん、なに？」「言って」系は scoped boost として扱う。

境界 score の LLM judge は `InitiativeLLMJudge` が runner 側で実行し、JSON result を
`initiative_candidate_loaded` event として戻す。これによりオンライン LLM 失敗は state machine を壊さず、
malformed / failure は `wait` に倒れる。

### 確定した判断: candidate runtime hard gate は TomoroSession だけが所有する
Phase 10.7 では、Phase 10.6 で `CandidateSpeakPolicy` が `TomoroRuntimeState` を見ていた判断を否定した。

`CandidateSpeakPolicy` は desire / speakability / personality / candidate metadata / now だけを入力にする。
ここで扱ってよいのは `text_ready`、`expires_at`、feedback、urgency、intrusion risk など candidate と soft score の条件だけである。

`CandidateCommandRunner` は active candidate fetch、policy snapshot 作成、LLM judge 実行、`SessionEvent` 変換だけを担当し、
`session.get_now_state()` を読んで発話可否を決めない。

`attention_mode`、VAD state、playback state、audio target availability、stale request は `TomoroSession` の final gate が再判定する。
gate reason は emission payload と log に残し、policy が `speak` でも Session が止めた理由を
`attention_not_ambient` / `vad_not_idle` / `playback_not_idle` / `audio_target_unavailable` / `stale_result`
として説明できるようにした。

### 確定した判断: AudioTurnController は public API で進む制御対象に限定する
Phase 6.6.4 で互換のために残した `TomoroSession` の audio turn thin delegate は否定する。

`TomoroSession` は、いつ話し始めるか、いつ止めるか、barge-in / interrupt をどう扱うか、
WebSocket event / audio をどの順序で送るかを持つ。
`AudioTurnController` は、`turn_id`、`audio_start` / `audio_end` / `audio_control stop` の idempotent reservation、
audio chunk sequence、playback telemetry 由来の playback state / echo grace、recent Tomoko text / speaking elapsed の
read-only snapshot だけを持つ。

`AudioTurnController` は WebSocket send、DB write、TTS 実行、reply 生成、会話参加判断、
candidate 発話判断を行わない。
`TomoroSession` は `AudioTurnController` の private method や内部 field を読まず、
`reserve_start_event()` / `reserve_audio_chunk()` / `reserve_end_event()` / `reserve_stop_event()` と
public property だけを使う。

### 確定した判断: stop-intent classifier は PostgreSQL queue の shadow signal として扱う
Phase 10.9 では、明示的な `BargeInDetector` hard interrupt ルールは即停止として維持し、
自然発話の stop / wait / withdraw 表現は online background worker で shadow 分類する。

`TomoroSession` の hot path は `stop_intent_observations` への observation insert だけを行い、
embedding / LLM classifier は `/ws` event drain や session lock の外で動かす。
worker は `FOR UPDATE SKIP LOCKED` で pending observation を1件ずつ取り、LLM classifier は
process 内 `asyncio.Semaphore(1)` で最大1同時に制限する。

classifier result は `SessionEvent(type="stop_intent_classified")` として `TomoroSession` に戻し、
`turn_id` / `transcript_id` / `observation_id` で stale check する。
高信頼 `hard_stop` / `soft_stop` / `withdraw` だけが制御に採用され、遅れた result や低信頼 result は
observation / shadow signal として保存するだけにする。

固定 WAV「はい、止めます」は control response であり、通常の Tomoko 返答として
`conversation_logs` に保存しない。
採用時は current reply / TTS を cancel し、`audio_control stop` の後に
`assets/audio/stop_ack.wav` を専用 audio turn として送る。

### 確定した判断: stop_ack.wav は Supertonic-3 CoreML F1 で声を揃える
Phase 10.9 初期実装時の `say -v Kyoko` 生成は否定する。
固定 WAV「はい、止めます」は Tomoko の default TTS と同じ `supertonic_coreml_f1`
（Supertonic-3 CoreML / voice style F1）で生成したものを `assets/audio/stop_ack.wav` とする。

生成元は `logs/stop-ack-supertonic-f1/supertonic_coreml_f1.wav`。
出力は RIFF/WAVE PCM 16-bit mono 44.1kHz、138,430 bytes、音声長 1569.0ms。

### 確定した判断: stop_ack.wav は明瞭性優先で Kyoko + tail silence にする
上の「Supertonic F1 で声を揃える」判断は、短い固定応答では末尾「す」が弱く、
「はい、とめま」のように聞こえるため否定する。

`local_whisper_mlx_small` では、Supertonic F1 版 `assets/audio/stop_ack.wav` は `四四四` と誤認識された。
`はい、止めます。`、`はい、止めまーす。`、F2-F5 などの Supertonic 候補も安定しなかった。
一方、macOS `say -v Kyoko` 版は `はい、止めます` と認識された。

固定 WAV は通常会話ではなく control response なので、Tomoko default voice との一致より停止意図の明瞭性を優先する。
`assets/audio/stop_ack.wav` は `say -v Kyoko --data-format=LEI16@16000` で生成し、
`sox ... pad 0 0.30` で末尾 300ms の無音を足した RIFF/WAVE PCM 16-bit mono 16kHz とする。

### 確定した判断: stop_ack.wav は選定済み Supertonic F1「はい、止めますね」を採用する
上の「Kyoko + tail silence にする」判断は、人間の聞き取りでより自然な Supertonic F1 候補が見つかったため否定する。

`local_whisper_mlx_small` は短い Supertonic F1 制御音声の文字起こしが不安定だった。
このため、固定 WAV の最終採用判断は STT 文字列ではなく人間の聞き取りを優先する。

採用する固定 WAV は `logs/stop-ack-supertonic-retry/phrase_tomemasu_ne.wav` を
`assets/audio/stop_ack.wav` にコピーしたものとする。
発話文は `はい、止めますね。`、control response text は `はい、止めますね`。
出力は RIFF/WAVE PCM 16-bit mono 44.1kHz、154,996 bytes、音声長 1756.8ms。

### 確定した判断: Phase 18 外部観測は raw artifact と interpretation を分離する
外部情報取得は Tomoko 本体の sensor ではなく、不安定な外周 operator workflow として扱う。
Perplexity / Codex Computer Use で得た Markdown は `informations/work` に raw artifact として置き、
validator / LLM normalizer / DB schema validation を通してから `world_observation_items` に変換する。

raw Markdown は source of truth ではなく、Tomoko が信じる事実でもない。
DB に保存する正規の派生情報は checksum 付き `world_observation_documents`、
normalized item、Tomoko の persona / lexicon version を参照した interpretation に分ける。

thinker / journalist は raw Markdown を直接 prompt に入れない。
thinker は `world_observation:<interpretation_id>` candidate を作り、
document / item / interpretation trace は `context_tags` と `utterance_candidates.metadata_json` に残す。
journalist は interpretation の短い summary / reason だけを日記素材にする。

online `/ws` / `TomoroSession` / `ContextSnapshotBuilder` には外部情報取得や normalize / interpretation を入れない。
Phase 18 の external observation は background / local job のみで動き、
既存の scoped feedback は `source` / `topic` tag 経由で world observation candidate にも効く。

### 確定した判断: 外部観測 interpreter には Tomoko profile を明示する
Phase 18 の structured output は JSON 形状を固定するが、`Tomoko` が何者かという意味の grounding は固定しない。

外部観測の normalizer は raw Markdown を item に整理するだけなので、Tomoko の人格詳細は入れない。
一方、interpreter は `tomoko_interest` / `relevance_to_user` / `speakability_hint` を採点するため、
短い Tomoko profile を system prompt に含める。

profile には、Tomoko が一人のユーザーと暮らすローカル推論ベースの日本語音声対話システムであること、
ユーザーが Tomoko を開発・運用している相手であること、ローカル推論 / Apple Silicon / MLX / 音声モデル /
開発者体験 / 生活実感への関心、ニュース解説者ではなく会話や日記の種として静かに見る基準を含める。

### 確定した判断: base persona と persona snapshot は別レイヤとして常に prompt に渡す
`prompts/base_persona.md` は固定の core persona として扱い、プログラムで自動書き換えしない。
会話から育つ persona / lexicon は `persona_state_versions` / `persona_lexicon_versions` の versioned snapshot として扱う。

LLM prompt では、base persona の後に snapshot の扱いルールを明記し、snapshot は base persona を上書きしない
派生状態だと伝える。snapshot が 0 件の場合も prompt から消さず、空の JSON を fallback として渡す。
これは「まだ学習済みの人格・用語がない」という状態を明示し、LLM が一般的なアシスタント像で補完しすぎるのを避けるためである。

会話経路では `ContextSnapshotBuilder` が返す persona slice / lexicon terms を serialized JSON として渡す。
外部観測 interpreter では latest persona snapshot 全体、または空 fallback JSON を system prompt に渡す。

### 確定した判断: 外部観測 interpretation は人格根拠と話題距離を schema で強制する
Phase 18 の外部観測 interpretation は、自由な `reason_json` と自然文 `speakability_hint` だけでは
一般要約に寄り、Tomoko の人格やユーザーとの距離感が DB から読み取りにくかった。

このため `speakability_hint` は `short_now` / `later` / `diary` / `avoid` の enum とする。
`reason_json` には `persona_basis` / `user_basis` / `speakability_basis` / `avoid_overclaim` を必須で持たせる。
`interpretation_text` は一般要約ではなく、Tomoko の内側からの短い受け取り方として生成させる。

初期状態でも persona snapshot が 0 件にならないよう、`_tools/seed_initial_persona_snapshot.py` で
`initial_core_persona` の `persona_state_versions` / `persona_lexicon_versions` を seed する。
この seed は base persona を DB snapshot に投影した初期値であり、会話から育つ後続 snapshot に上書きされる。

### 確定した判断: 外部観測には内心反応と発話候補の種を別フィールドで持たせる
外部観測の `interpretation_text` は Tomoko の受け取り方を表すが、thinker / journalist が使うにはまだ一般要約に寄りやすい。
そのため `world_observation_interpretations` に `tomoko_private_reaction` と `candidate_seed_text` を追加する。

`tomoko_private_reaction` は Tomoko の内側の短い反応であり、事実要約ではなく興味・ためらい・覚えておきたい感じを残す。
`candidate_seed_text` は将来の自発発話候補へ渡すための短い自然文であり、thinker の world observation source はこれを優先する。

この2つは raw Markdown の事実性を増やすものではなく、persona interpretation の派生メモである。
過剰断定を避ける根拠は引き続き `reason_json.avoid_overclaim` に持たせる。

### 確定した判断: 発話再開時は未出力 reply を stale としてキャンセルする
VAD の無音閾値 400ms は、実会話でユーザーの小休止を発話終了として切りやすかった。
`config/central_realtime.toml` の `vad_silence_ms` は 1000ms とし、発話途中の分割を減らす。

さらに、単に reply 開始前へ sleep を挟む小手先対応は採用しない。
`TomoroSession` は、reply text / emotion / audio がまだ外へ出ていない段階で新しい `listening` に入った場合、
その reply を古い断片に対する stale result とみなしてキャンセルし、続きの人間発話を優先する。
すでに表示や音声出力が始まった reply は、この stale cancel では止めず、既存の barge-in / stop-intent 制御へ委ねる。

STT backend は ANE/CoreML 側の実挙動確認のため、central runtime では `local_whisperkit_serve_small` を使う。
`local_whisperkit_serve_small` の focused perf smoke では warm 7003.5ms、measured 211.9ms だった。

### 確定した判断: VAD 1000ms は体感遅延が強いので 800ms 比較へ戻す
セッション36で発話途中の返答開始を避けるため `vad_silence_ms = 1000` にした判断は、
実ブラウザ体感で返答開始がやや遅く感じられたため一旦否定する。
セッション37では基準値に近い `vad_silence_ms = 800` へ戻し、未出力 reply の stale cancel と併用して比較する。

### 確定した判断: audio_start はクライアント再生キューの turn 境界として扱う
サーバーから新しい `audio_start.turn_id` が来た時、前 turn の `AudioBufferSourceNode` 予約と
`nextPlaybackTime` が残ると、サーバー側 `first_audio_chunk` は速くてもブラウザ再生開始が数秒遅れることがある。
同じ turn の重複 `audio_start` では触らず、turn が変わった時だけクライアントの古い再生キューを止めて
`nextPlaybackTime` を現在時刻へ戻す。

### 確定した判断: WhisperKit large は small と別 port で serve する
会話が噛み合わない原因の一つとして small STT の誤認識が目立ったため、
central runtime の active STT backend は `local_whisperkit_serve_large` に切り替える。

WhisperKit serve は起動時の `--model` が重要であり、既存 small server が同じ port で健康応答すると
large へ切り替わったか確認しにくい。
そのため small は `127.0.0.1:50060` のまま残し、large は `127.0.0.1:50061` で起動する。
large model は Argmax が multilingual accuracy 用に示している `large-v3-v20240930_626MB` を使う。

### 確定した判断: base persona は音声会話の不確実性を明示的に扱う
短い返答指示だけでは、STT が崩れた入力に対して LLM がそれらしい返答を作ってしまう。
`prompts/base_persona.md` には、聞き取りが怪しい時は断定せず確認すること、
Tomoko の動作・遅延・モデルについての発話には開発中のTomokoとして一緒に確認すること、
相槌だけで会話を終わらせず必要なら短い確認質問を一つ添えることを明記する。

### 確定した判断: 応答推論 LLM の入力 prompt はログへ出す
会話が噛み合わない時に、LLM 自体の弱さ・STT 誤認識・persona prompt・会話履歴のどこが原因かを切り分けるため、
`ThinkFastMode` は `backend.chat_stream()` の直前に応答推論へ渡す入力を INFO ログへ出す。

ログ行は `ThinkFastMode llm_prompt backend=... payload=...` とし、payload は JSON で
`system_prompt` / `messages` / `device_id` / `speaker` を含める。
これにより `logs/server-debug.log` 上で transcript filter / participation / context build / LLM prompt / reply_text を
同じ時系列で追えるようにする。

### 確定した判断: 応答推論 prompt は日時つきで専用 JSONL にも追記する
上の `ThinkFastMode llm_prompt` INFO ログを残す判断は否定しない。
追加で、会話 LLM に渡す system prompt には現在ローカル日時と曜日を `CURRENT LOCAL TIME` として含める。
これは「今日」「明日」「昨日」などの相対表現を Tomoko が解釈する基準を明示するためである。

また、通常の server debug log とは別に、応答推論へ渡した prompt payload だけを
`logs/conversation-prompts.jsonl` へ append する。
1 行 1 JSON payload とし、`system_prompt` / `messages` / `backend` / `device_id` / `speaker` を含める。
通常ログと prompt 専用ログを分けることで、会話品質調整時に prompt だけを時系列で追えるようにする。

### 確定した判断: Google Calendar は background import 後に deep context で読む
private iCal URL を `prompts/`、config tracked file、または会話 hot path に直書きする方針は否定する。
Google Calendar は `config/gcal_urls.txt` のような git 管理外ファイルから `make gcal` で取得し、
PostgreSQL の `calendar_events` に保存する。

online 会話では外部 URL を叩かず、`ContextSnapshotBuilder` が `deep` / `reflective` policy の時だけ
`calendar_events` から近い予定を読み取り、`TomokoContextSnapshot.calendar_events` として渡す。
`ThinkFastMode` / `ThinkDeepMode` は snapshot 内の予定を `CALENDAR CONTEXT` として system prompt に入れる。
この calendar context はユーザー発話ではなく、予定の有無や時刻を答える時だけ参照する補助情報として扱う。

### 確定した判断: stop-intent LLM classifier は optional degraded signal として扱う
stop-intent worker では、rule / embedding の shadow signal を先に保存し、LLM classifier の失敗だけで
observation 全体を `error` にしない。

LM Studio 500 など LLM 側の一時失敗が起きた場合は、`method="llm"` / `predicted_kind="none"` /
`confidence=0.0` / `raw_reason_json.degraded=true` の signal を保存し、observation は `completed` にする。
これにより deterministic な stop / withdraw 候補や embedding signal が、補助 LLM の失敗に巻き込まれない。

### 確定した判断: 低信号 segment は spectral filter より STT 前 reject を優先する
実録音 `work/audio-recordings/20260525T122454Z-read_aloud.wav` は 5秒音声だが、
`rms_db=-47.4` / active frame ratio 20.4% と低信号で、MLX Whisper large-v3-turbo-q4 は raw でも
frame gate 後でも `ご視聴ありがとうございました` と誤認識した。
spectral gate 後は `反反反...` の反復幻聴になり、今回の素材では改善しなかった。

CPU コストは frame gate が約 0.014ms/audio sec、spectral gate が約 2.06ms/audio sec と十分軽い。
ただし noise profile `20260525T122451Z-noise.wav` は `rms_db=-120.0` / `peak_db=-120.0` のほぼデジタル無音で、
spectral filter の評価素材としては弱い。

このケースでは「波形を加工して Whisper に渡す」より、`SpeechSegment` の duration / rms_db /
active frame ratio / peak_db を見て STT 投入前に低品質 segment を reject する方を先に実装する。
spectral filter は、空調・ファン音など実ノイズが入った startup noise profile を録り直してから再評価する。

### 確定した判断: STT signal gate は central と edge の両方で Whisper の直前に置く
DAW の gate と同じく、低信号 segment を落とす処理は CPU 的にはほぼ無視できる。
GPU / MLX Whisper を無駄に叩く方が高コストで、低信号入力は `ご視聴ありがとうございました` や反復文字列の
hallucination を誘発しやすい。

そのため `server/edge/pipeline/stt_gate.py` の `SttSignalGate` を、`TomoroSession._handle_finished_speech()` と
`EdgeRemoteAudioSession.process_audio_chunk()` の transcriber 呼び出し直前に置く。
reject 時は transcript を生成せず、VAD と streaming transcriber を reset して idle に戻す。
partial STT も、明らかに弱い chunk では `process_stream_chunk()` を呼ばない。

初期閾値は、実録音 artifact で見えた `rms_db=-47.4` / active frame ratio 20.4% を落とすため、
`rms_db < -45` かつ `active_frame_ratio < 0.25` を sparse low signal として reject する。
短すぎ判定は unit test と短い実発話を潰しすぎないよう 80ms にする。
この閾値は `logs/server-debug.log` の `stt_gate_action` / `stt_gate_reason` / `rms_db` / `peak_db` /
`active_frame_ratio` を見ながら実ブラウザ録音で調整する。

### 確定した判断: STT 前処理は ON/OFF 可能な audio frontend filter chain として育てる
STT 前処理は、単一の gate 関数ではなく `SttAudioFrontend` の filter chain として扱う。
`enabled_filters=()` なら完全に素通りし、`("signal_gate",)` なら低信号 reject、
`("short_segment_merge", "signal_gate")` なら短い segment を pending にして次 segment と merge、
`("spectral_subtraction", ...)` なら noise profile がある場合のみ spectral subtraction を適用する。

startup noise profile は `NoiseProfile` として capture できるようにし、profile がない場合の spectral subtraction は素通りする。
これにより「比較対象がある、かつ FILTER-ON なら通す。なければ通さない」を runtime 構造で表せる。

STT 後段の `TranscriptFilter` は hallucination phrase / repetition loop の semantic filter として残し、
audio frontend とは別レイヤにする。
今後の比較では raw / signal_gate / short_segment_merge / spectral_subtraction の組み合わせを切り替え、
録音 artifact と `bench_stt_backends.py` で transcript と latency を見る。

### 確定した判断: speech bandpass は runtime 常時ON、2kHz low-pass は常用しない
DAW 的な直感通り、100Hz high-pass / high-frequency roll-off は STT 前処理として十分安い。
`speech_bandpass()` は 100Hz high-pass と 7.2kHz low-pass を行う。
central / edge runtime の `SttAudioFrontend` は `("speech_bandpass", "signal_gate")` を常時ONにする。

一方、2kHz low-pass は Whisper の入力としては強すぎる。
実録音 `20260525T125851Z-read_aloud.wav` では、通常 bandpass 後の Whisper は
`うんそうだよ ともこ 短い声をすてずに 続きの` だったが、
100Hz-2kHz bandpass では `うんそうだよともこ短い声をすけずに続きの` となり、
`捨てずに` の子音が崩れた。

5秒音声に対する `short_segment_merge` / `speech_bandpass` / `spectral_subtraction` / `signal_gate` の
frontend 処理は約 9.2ms、約 1.84ms/audio sec だった。
noise profile capture は約 5.1ms で一回だけの処理。

### 確定した判断: RNNoise は実験用 filter として残し、常時ONにはしない
現環境では Python RNNoise binding は未導入だが、`ffmpeg` の `arnndn` filter が利用可能だった。
arnndn model は `work/rnnoise-models/std.rnnn` に保存し、`SttAudioFrontend` では `rnnoise` filter として
明示的にONにした時だけ使う。model file がない場合は素通りする。

5秒の良い録音では RNNoise 処理が約65.1ms、約13.0ms/audio sec で、
Whisper 結果は `うんそうだよ ともこ 短い声をすてずに 続きの` だった。
5秒の低信号録音では約60.1ms、約12.0ms/audio sec で、
Whisper 結果は `おやすみなさい` になった。

つまり RNNoise は現行の `speech_bandpass` / `signal_gate` より一桁重く、
低信号 hallucination を完全には防がない。
Tomoko の常時ON処理は `speech_bandpass` / `signal_gate` を優先し、
RNNoise は実ノイズが強い録音素材を取った時の比較用に残す。

### 確定した判断: RNNoise はOFF、会話LLMは Gemma 4 E2B MLX、TTS は Kokoro MLX
RNNoise は効果に対して常時ONのコストが厳しいため、実ランタイムの `SttAudioFrontend` では引き続きOFFにする。
`rnnoise` filter と bench tool は比較実験用に残すが、default filter chain は `speech_bandpass` / `signal_gate` を使う。

central realtime の会話 backend は `local_gemma4_e2b_mlx` を主系にする。
前主系だった `local_lfm25_12b_jp_mlx` は fallback として残し、Gemma 側の失敗時に戻れるようにする。

TTS は central / edge ともに `kokoro_mlx` を使う。
Kokoro MLX は 24kHz default だが、config 上も `sample_rate = 24000` を明示し、factory が設定値を読むようにする。

### 確定した判断: 会話LLMは LM Studio の Gemma 4 E4B MLX に切り替える
上の「会話LLMは Gemma 4 E2B MLX」という判断は、LM Studio で `gemma-4-e4b-it-mlx` を使う方針により否定する。

central realtime の active `conversation_backend` は `lmstudio_gemma4_e4b` とする。
LM Studio の OpenAI 互換 API は既存の `http://192.168.11.66:1234` を使い、
model は `gemma-4-e4b-it-mlx` とする。

fallback は内蔵 `mlx-vlm` の `local_gemma4_e2b_mlx` にし、LM Studio が落ちた/遅い場合も
ローカル Gemma 系へ留める。
既存の `lmstudio_gemma4_e2b` と `local_lfm25_12b_jp_mlx` は、session summary / candidate / diary や比較用 backend として残す。

短文 smoke では first delta 313.5ms / total 314.6ms で、出力は `はい。` だった。

### 確定した判断: backend call debug trace は JSONL に分離する
会話体験が落ちた時に、LM Studio の queue 待ち、first delta 遅延、TTS first chunk 遅延、
STT/embedding/local MLX の同時負荷を切り分けるため、backend call trace は人間向け server log とは別に
`logs/backend-trace.jsonl` へ 1 行 1 JSON で出す。

各行は `trace="tomoko_backend_call"` を持ち、`event` / `kind` / `role` / `backend` / `model` /
`request_id` / `queue_key` / `wait_ms` / `elapsed_ms` / `total_ms` / `chunk_count` / `error` を用途に応じて持つ。
LM Studio は URL 単位の process-local semaphore を使い、`queue_acquired.wait_ms` で Tomoko 側の同時投入待ちを見える化する。
STT は `audio_ms` / `text_len`、embedding は `text_len` / `dimensions` を出し、GPU/CPU 側の負荷推測に使う。

この JSONL は debug artifact であり、Tomoko の制御判断や source of truth には使わない。

### 確定した判断: WhisperKit large turbo 632MB CPU+ANE を STT 比較 lane にする
上の「WhisperKit large は small と別 port で serve する」判断は否定しない。
ただし、そこで使った `large-v3-v20240930_626MB` は画像で示された
`openai_whisper-large-v3-v20240930_turbo_632MB + .cpuAndNeuralEngine` そのものではなかった。

WhisperKit CLI の `serve` は `--audio-encoder-compute-units` / `--text-decoder-compute-units` を持ち、
help 上の default は `cpuAndNeuralEngine` だが、Tomoko 側では実験条件を明示するため config から渡す。

central runtime の active `stt_backend` は `local_whisperkit_serve_large_turbo_632m_cpu_ne` とする。
model は `large-v3-v20240930_turbo_632MB`、port は `127.0.0.1:50062`、compute units は
`cpuAndNeuralEngine`。

既存の `local_whisper_mlx_large_turbo_q4` は、STT 品質が良かった MLX fallback / 比較候補として残す。
実比較では `logs/backend-trace.jsonl` の STT `total_ms` と実ブラウザ transcript、GPU/ANE 使用状況を見る。

### 気づき: Gemma 4 26B A4B は deep/background 候補、31B は hot path には重い
2026-05-27 に LM Studio の OpenAI 互換 API で `gemma-4-e4b-it-mlx` / `gemma-4-26b-a4b-it-mlx` /
`gemma-4-31b-it-mlx` を同一 prompt で比較した。

Tomoko base persona 相当の短い音声返答では、E4B は first content 252〜331ms / total 455〜671ms、
26B A4B はロード直後の本番 prompt で first content 281〜331ms / total 481〜634ms、
31B は first content 905〜1369ms / total 2376〜2942ms だった。

短い返答の意味性は、26B A4B が E4B より少しだけ文脈利用が良く、31B は速度低下に見合う明確な差が出なかった。
一方、会話後処理 worker 相当の知見抽出 prompt では、26B / 31B の方が
「意味ある会話=新しい知見」「朝はコードレビュー、夜は設計」「即応の口と後で考える頭の分離」を
記憶候補として具体化できた。

現時点の判断は、default conversation hot path の全面 31B 化ではなく、deep / session_summary / persona_update /
reflection のような background role で `gemma-4-26b-a4b-it-mlx` を試すのが良い。
ただし LM Studio は同一 URL semaphore で直列化されるため、background 大型モデルが conversation E4B を塞がない
process / URL 分離を検討する。

### 気づき: LM Studio Gemma 4 31B はログ上 no-think の空 thought prefix で動いていた
2026-05-27 に `lms log stream --source model --json --stats` で `gemma-4-31b-it-mlx` の formatted input / output を確認した。

`"think": false`、`think` 省略、`"think": true` のいずれでも formatted input の assistant prefix は
`<|channel>thought\n<channel|>` だった。
これは Gemma 4 model card の「thinking disabled 時も空 thought block tag を出す」挙動に近い。

OpenAI 互換 streaming response では `reasoning` / `reasoning_content` / `thinking` field は観測されず、
LM Studio の output log も本文だけだった。
system prompt 先頭に `<|think|>` を明示しても、実 output は `17 × 23 = 391` のような本文だけで、
thought 本文は出なかった。

したがって、今回の 31B の first content 0.9〜1.4s は、hidden thinking 生成ではなく
モデルサイズ / MLX decode 速度 / prompt 処理の影響と見る。
ただし LM Studio の `think` parameter はこの Gemma 4 MLX 経路では formatted input を変えていないため、
thinking を本当に有効化したい場合は LM Studio 側の model template / reasoning parser 設定を別途確認する。

### 気づき: 会話中 state simulation でも 26B A4B が意味性と速度のバランスで最良
2026-05-27 に、Tomoko と数ターン会話済みの dummy messages と補助文脈を system prompt に入れ、
`gemma-4-e4b-it-mlx` / `gemma-4-26b-a4b-it-mlx` / `gemma-4-31b-it-mlx` を比較した。

シナリオは、意味のある会話への踏み込み、朝レビュー・夜設計のリズム活用、
即応の口と後で考える頭の分離だった。
E4B は速いが、踏み込み要求では「情報が足りない」に逃げやすかった。
26B A4B は「作業の中での会話」と「振り返りのための外の会話」が混ざっているという盲点を返せた。
31B は悪くないが、初回ロード/切替約16s、ロード後も first content 約1.3〜1.5s / total 約2.7〜3.5s で、
26B A4B より明確に意味性が高いとは言えなかった。

会話 hot path を育てる場合でも、まずは 26B A4B を deep conversation / reflection lane に使う方が現実的。

### 確定した判断: 明日の実機比較では会話LLMを Gemma 4 26B A4B MLX に切り替える
上の「central realtime の active `conversation_backend` は `lmstudio_gemma4_e4b`」という判断は、
ユーザーが latency を犠牲にしても意味性を試したいという判断により一旦否定する。

2026-05-27 時点では、central realtime の active `conversation_backend` は
`lmstudio_gemma4_26b_a4b` とする。
LM Studio の OpenAI 互換 API は既存の `http://192.168.11.66:1234` を使い、
model は `gemma-4-26b-a4b-it-mlx` とする。

E4B は `lmstudio_gemma4_e4b` として残し、速度優先へ戻す比較候補にする。
26B は初回 model switch / load が約10秒かかることがあるが、ロード後の dummy 会話では
first content 0.31〜0.41s のケースがあり、意味性は E4B より明確に良かった。
明日の実ブラウザ比較では `logs/backend-trace.jsonl` の `role="conversation"` と
`logs/server-debug.log` の `ThinkFastMode llm_prompt` / reply text を見る。

### 確定した判断: メイン会話LLMは Gemma 4 26B A4B MLX で一旦FIXする
上の「明日の実機比較では会話LLMを Gemma 4 26B A4B MLX に切り替える」という暫定判断は、
実ブラウザ会話の体感確認により本採用へ進める。

2026-05-27 の実会話ログでは、`lmstudio_gemma4_26b_a4b` が fallback せず conversation backend として動作し、
人生の軸に関する相談で、一般論を即答するのではなく、複雑さを受け止めて次の内省質問へつなげられた。
ユーザー体感でも E2B 時より明らかに楽しく、Romi よりも楽しい領域に入った。

したがって、メイン会話モデルの探索はここで一旦止め、次は Tomoko からの自発的発話・候補選択・話しかける間合いの調整へ進む。
速度優先比較用として `lmstudio_gemma4_e4b` は残すが、会話品質の基準線は 26B A4B とする。

### 確定した判断: Phase 10.10 自発発話は会話開始用の一言として整える
自発発話 candidate の `generated_text` は、単なる興味メモや topic 名ではなく、
Tomoko がそのまま話しかける 1〜2文の会話開始文として扱う。

world observation 由来など直前文脈と別件になりやすい candidate には、
`さっきの話とは別で、` のような橋渡しを入れる。
`を動かすための専用チップ` のような主語欠け断片や、`最新情報を知っている` 断定は保存前に落とす。

自発発話そのものでは `conversation_session` を開始しない既存判断は維持する。
ただし人間が follow-up した最初の LLM prompt には、直前の Tomoko 自発発話を
assistant turn として明示的に入れる。
これにより `それってどういうこと?` と聞かれた時、Tomoko が「関係なかった」と撤回せず、
自分が出した話題を説明できるようにする。

候補 policy では、`recent_heavy_conversation` 直後の別件 topic shift は
bridge がない限り少し score を下げる。
ただし最終 gate と session lifecycle の所有者は引き続き `TomoroSession` とする。

### 確定した判断: Phase 10.10 のユーザー判断反映
active STT backend は `local_whisper_mlx_large_turbo_q4` が正であり、
`local_whisperkit_serve_large_turbo_632m_cpu_ne` は比較 lane として残す。

world observation など別件 candidate の bridge は、固定文の自動補完ではなく
候補生成 prompt 側に寄せる。
ただし `topic_shift_bridge_required` tag と prompt 契約は残し、bridge なし candidate は
`recent_heavy_conversation` 直後の policy score で軽く不利にする。

断片 candidate reject は厳しめで始める。
initiative / arrival の直前 precomputed reply を follow-up context に入れる現行実装は維持する。
実ブラウザ評価は既存候補で試すため、候補が来たが発話しなかった理由をブラウザ UI に表示する。

### 確定した判断: attention timeout は Tomoko の再生中に進めない
2026-05-27 の実ブラウザログでは、Tomoko の音声 chunk がまだ再生中なのに
`attention changed from engaged to cooldown` が発火していた。
例として 04:51:23 に chunk 18 の playback が始まり、04:51:34 に終了したが、
04:51:32 に `engaged -> cooldown` へ落ちていた。

原因は、`TomoroSession.process_audio_chunk()` が VAD state `idle` の無音 chunk を受けるたびに
attention idle time を進めており、`AudioTurnController.playback_state` を見ていなかったこと。
Tomoko が喋っている間は「会話が途切れた」時間ではないため、attention timeout は進めない。
以後は playback state が `idle` の時だけ `engaged -> cooldown -> ambient` の idle timer を進める。

### 気づき: Apple Speech framework は Swift sidecar なら Python STT backend として比較できる
Apple 純正 Speech framework は CoreML モデルを直接ロードする形ではないが、
Swift sidecar CLI を Python から subprocess で呼ぶ `apple_speech` backend として比較できる。

実装上、`SFSpeechRecognizer.requestAuthorization` を CLI から呼ぶと TCC が
`NSSpeechRecognitionUsageDescription` を認識せず abort する環境があった。
このため sidecar は authorization request を明示実行せず、`.app` bundle + embedded Info.plist +
ad-hoc codesign した実行ファイルで `SFSpeechURLRecognitionRequest` を実行する。

2026-05-27 の 3 runs 比較では、synthetic `say` 音声で Apple Speech avg 183.3ms、
MLX Whisper large turbo q4 avg 240.6ms。
実録音 `work/audio-recordings/20260525T125851Z-read_aloud.wav` では Apple Speech avg 242.7ms、
MLX Whisper large turbo q4 avg 248.7ms。
Apple Speech は速い/同程度だが、「ともこ」を「智子」に漢字化し、実録音では
`短い声` を `短いです声` と崩したため、現時点では active STT を置き換えず比較 lane として残す。

### 確定した判断: Apple Speech STT を mactop 観測用に一時 active にする
上の「active STT backend は `local_whisper_mlx_large_turbo_q4` が正」という判断は、
2026-05-27 の CPU / ANE 実観測実験の間だけ否定する。

central realtime の active `stt_backend` は `local_apple_speech_ja` とする。
目的は Apple Speech framework が CPU / ANE / GPU のどこを使うかを、実ブラウザ会話と
`mactop` / system monitor で観測すること。

品質面では前回比較どおり `ともこ -> 智子` などの表記ゆれがあるため、
採用判断はこの観測後に戻す/維持するかを決める。

### 気づき: Apple Speech STT はまだ比較用の薄い backend であり失敗分類が必要
2026-05-27 の実ブラウザ会話では Apple Speech STT の認識精度と速度は実用範囲に見えたが、
sidecar が `{"error":"No speech detected"}` を exit code 1 で返した時に Python backend が
`RuntimeError` として WebSocket 経路まで伝播させ、会話 runtime を落としていた。

このため現時点の `apple_speech` は、Apple 純正 Speech framework を Python から比較するための
薄い実験レーンであり、本番品質の STT backend としては失敗分類・権限状態・空認識・timeout の扱いを
まだ詰め切っていない。
`No speech detected` は VAD が拾った短い/弱い区間で起きうる通常系として扱い、空 transcript に畳む。
権限エラーや sidecar 実行失敗など未知の失敗は引き続き例外として表に出す。

### 確定した判断: VAD listening だけで未出力 reply を捨てない
2026-05-27 の実会話ログでは、09:10:57.008 に `lmstudio_gemma4_26b_a4b` の会話 reply が開始した直後、
09:10:57.110 に VAD が `listening` へ入っただけで
`stale reply cancelled reason=resumed_user_speech_before_output` が発火していた。
しかし、その後の STT は `text=''` / `reason=empty` で、低音量の短い区間を Apple Speech が空認識しただけだった。

未出力 reply を `listening` への遷移だけで捨てると、ノイズ・息・残響・空 STT によって
Tomoko の返答が消える。
以後は VAD が listening に入っただけでは未出力 reply をキャンセルしない。
意味のある follow-up transcript が確定して参加対象になった時は、次の `_start_reply_task()` が既存 reply を差し替える。

### 確定した判断: turn-taking judge は rule-first + worker 補助にする
Phase 10.11 では、未出力 reply を消すかどうかを VAD state ではなく確定 transcript 後の
`TurnTakingJudge` で判定する。

`TurnTakingInput` / `TurnTakingDecision` は `server/shared/models.py` の DTO とし、session 内部の生 dict 判定にしない。
hot path では `RuleFirstTurnTakingJudge` が空 transcript / 低信号 / stop word / 訂正 / 相槌 / 実質 follow-up を先に分類する。
明確な stop / restart / continue は worker を待たず、`defer_output` のような曖昧ケースだけ
`TurnTakingWorkerClient` が別プロセス worker へ投げる。

worker は `background-process/run_turn_taking_worker.py` として常駐し、会話文は生成せず固定 enum JSON だけを返す。
`make turn-taking-worker` は小型 MLX model 用の別 queue、`make turn-taking-worker-once` はモデルロードなしの rule sample として使う。
worker timeout / unavailable / parse error は rule fallback へ戻し、TomoroSession が最終 gate と session lifecycle を所有する。

### 確定した判断: playback 中 interrupt 候補も turn-taking judge に通す
上の「pending reply / 生成中 reply の確定 transcript に judge を適用し、playback echo は既存 barge-in 層に残す」
という初期判断は部分的に否定する。

実ブラウザログでは、Tomoko 再生中の `ちょっと待って` が `playback_active_chunk` の echo として消費され、
turn-taking judge / worker の動作確認ができなかった。
以後は playback 中でも `待って` / `ストップ` / `違う` などの interrupt 候補は先に `TurnTakingJudge` へ通す。
一方、通常 follow-up や回り込みらしい transcript は `turn_taking_skipped reason=playback_non_interrupt_candidate`
をログに出した上で、従来通り `BargeInDetector` / playback echo grace に任せる。

STT は `待って` を `待とうか` のように起こすことがあるため、turn-taking 側では
`待とう` / `まとう` も wait keyword として扱う。
また、既存 stop-intent が拾う語は turn-taking interrupt candidate として先に扱い、
「judge を skip した後に stop-intent worker だけが hard stop する」経路を減らす。

### 確定した判断: 明示的な記憶想起では query embedding を共有し、session summary を優先する
2026-05-27 の実ログでは、`この前話したAIの話って結論どうなったか覚えてる` という発話で
`ContextSnapshotBuilder depth=deep elapsed_ms=100.6 budget_ms=100 timed_out=True` となり、
`session_summaries=0 memory_hits=0` のまま LLM に渡った。

SQL 自体は小さい。
実 DB では completed session summary は 8 件、turn embedding は 211 件で、
pgvector search の `EXPLAIN ANALYZE` は session summary 約 0.13ms、turn memory 約 1.3ms だった。
したがって 100ms timeout の主因は SQL の複雑さではなく、BGE-M3 の query embedding 生成と
同一発話に対する二重 `embed_query()` 実行だった。

以後、`ContextSnapshotBuilder` の 1 build 内では query embedding を 1 回だけ作り、
session summary search と turn memory search で共有する。
また、明示的な記憶 cue（`この前` / `覚えてる` / `話してた` など）で deep に入った場合だけ
context budget を 300ms に上げる。
通常の長文 deep は従来どおり 100ms に留める。

記憶想起ではまず会話単位の `conversation_sessions.summary_text` を優先し、
turn-level `conversation_embeddings` は補助として扱う。
Context build log には `query_embedding` / `session_summaries` / `memory_hits` の
stage timing、cache hit、skipped reason、source error を出し、今後は「記憶が空だった理由」を
ログから切り分ける。

### 確定した判断: STT final transcript は既存 `/ws` の観測 event として UI に流す
2026-05-28 に、ambient / 会話中の人間発話の STT 確定文字列を UI に表示する経路を追加した。

DB 保存経路は変更しない。
`ambient_logs` / `conversation_logs` / `conversation_sessions` は従来どおり `TomoroSession` が所有し、
UI には同じ `/ws` 上の `transcript_final` JSON event として観測情報だけを送る。

`transcript_final` には `text` / `attention_mode` / `participation_mode` / `attended` /
`audio_level_db` / `is_final` を含める。
会話参加発話では、`TomoroSession` が session を確保した後に
`conversation_session_id` も付ける。
ambient observer 発話では session を作らず、UI だけが表示する。

クライアントは判定を行わず、高さ固定の transcript log に追記表示するだけにする。
これにより、1 本の WebSocket と server-side state ownership を維持したまま、
実ブラウザで「STT が何を聞いたか」を確認できる。

### 確定した判断: deep retrieval 結果は active session 内で短期 carryover する
2026-05-28 の実ログでは、`著作権の話とか覚えてる` では `depth=deep` の
`session_summaries` / `memory_hits` が prompt に投入されたが、直後の
`どういう風に考えてたっけ` は `depth=fast` になり、長期記憶が 0 件の prompt になった。

このため、一度 deep retrieval で取り出した長期記憶は、active conversation session 内の
`TomoroSession` 作業メモとして短期 carryover する。
これは DB の source of truth でも `ContextSnapshotBuilder` の read cache でもなく、
自然な follow-up のための prompt 補助である。

carryover は `MemoryHit.source_id` があればそれを優先し、なければ speaker / timestamp /
normalized text hash で dedupe する。
`conversation_sessions` 由来の summary は `session_summary:<session_id>` を `source_id` として持つ。
固定値として最大 6 entry / 900 文字から始め、超過時は古い・低 similarity の entry から落とす。
session close / withdrawn / ambient 復帰では clear する。

ログには `carryover_added` / `carryover_used` / `carryover_evicted` / `carryover_cleared` を出し、
follow-up のたびに embedding search を増やさず、前 turn の retrieval 結果を再利用できたか確認する。

### 確定した判断: Stop/Disconnect は SessionEvent 経由で conversation session を閉じる
2026-05-28 の確認で、UI Stop は WebSocket を閉じるだけで、`cooldown -> ambient` や `withdrawn` を経由しないため、
active `conversation_sessions` が `summary_status='not_ready'` のまま残り、session summarizer の対象にならないことがわかった。

ただし、`/ws` adapter が `conversation_session_store.close_session()` を直接呼ぶ設計は採用しない。
transport 層は Stop / Disconnect という事実を `SessionEvent` に変換するだけにし、conversation session lifecycle の最終判断は
引き続き `TomoroSession` に集約する。

UI Stop は `client_stop` JSON event として送られ、`TomoroSession` が `client_stop_requested` を受けて
active session を `end_reason='ui_stop'` で閉じる。
WebSocket disconnect は connection registry の snapshot を `connected_output_state_changed` として戻し、
connected client が 0 になった場合だけ `end_reason='client_disconnect'` で閉じる。
これにより既存 `PostgresConversationSessionStore.close_session()` 契約で `summary_status='pending'` へ進み、
background summarizer が拾える。

### 確定した判断: fast follow-up でも渡された long_term_memory は prompt に入れる
2026-05-28 の実ログ確認で、`詳しくはどんな話やったっけ` の turn は
`TomoroSession carryover_used count=6` まで到達していたが、実際の `ThinkFastMode llm_prompt` には
長期記憶ブロックが入っていなかった。

原因は retrieval / carryover ではなく、長期記憶 prompt formatting が `ThinkDeepMode` に閉じており、
`ThinkFastMode` が `ThinkingInput.long_term_memory` を読んでいなかったことだった。
以後、fast / deep のどちらでも `ThinkingInput.long_term_memory` が空でない時は同じ formatter で
「長期記憶として関連しそうな過去会話」を system prompt に追加する。

これは fast mode に新しい DB 検索や embedding search を増やす判断ではない。
`TomoroSession` / `ContextSnapshotBuilder` がすでに選んだ memory を、会話生成 prompt へ正しく接続するだけの判断である。

### 確定した判断: Phase 8.8.8 retrieval は quota と weight を両方使う
2026-05-28 に Phase 8.8.8 の memory retrieval weighting / session turn restore を実装した。

`ContextSnapshotBuilder` は memory source を `session_summary` / `user_turn_snippet` /
`tomoko_turn_snippet` / `memory_hit_user` / `memory_hit_tomoko` / `lexicon_term` に分け、
source ごとの quota で占有上限を切った後、`raw_similarity * source_weight * role_weight *
recency_weight * salience_weight` の final score で選択・並び替える。

初期値では user turn を主、Tomoko turn を補助として扱う。
`tomoko_turn_snippet` は max 1 / role_weight 0.25、`user_turn_snippet` は max 4 / role_weight 1.0 とし、
Tomoko の過去発話がユーザー原文を押しのけないようにする。
cue type は rule-first で `recall` / `detail` / `stance` / `normal` に分類し、
`detail` では user turn snippets、`stance` では user turn と lexicon を相対的に強める。

summary hit 後の原文復元は `ConversationSessionSummaryStore.read_session_turns()` から raw logs を読む optional source とする。
session_id から読むだけなら online path で新しい embedding は作らない。
今後 rerank に embedding が必要になった場合も、同一 context build 内の `query_embedding_task` を使い回し、
同じ発話から二重に query embedding を生成しない。

`ContextBuildTrace` には `cue_type` と selected / dropped / score breakdown を残す。
実ブラウザでの最終 tuning は、`ContextSnapshotBuilder` の `source_scores` と `ThinkFastMode llm_prompt` を見て行う。

### 確定した判断: Phase 10.16 以降は runtime state proxy を使わない
2026-05-28 に、Phase 10.15 実装を一度戻した上で、Phase 10.16 として
`TomoroSession` の `_RUNTIME_STATE_FIELDS` / `__getattr__` / `__setattr__` を削除した。

以後、session 内部の状態 access は `runtime_state.xxx` を明示する。
`state` / `attention_mode` / `latest_segment` / `active_conversation_session_id` は
既存テストや観測用の read-only property として残すが、書き込みは
`runtime_state.xxx` または `_transition()` / `_transition_attention()` / `_set_start_reason()` などの
意味 method 経由にする。

Phase 10.15.Re の再実装では、`SessionInputSignal` は既存 semantic DTO
`Transcript` / `PlaybackTelemetry` / `SessionEvent` の alias として扱い、
audio binary は引き続き signal に包まない。
`SessionSignalDispatcher` は type switch の目次に限定し、transcript の処理本体は
`TranscriptFlow` に移した。
flow から state を読む場合も暗黙 proxy は使わず、`session.runtime_state.xxx` または
TomoroSession の意味 method を使う。

### 確定した判断: SessionEventRunner が event runtime を所有する
2026-05-28 に、`core.py -> 内部 -> core.py -> 内部` の戻りを減らすため、
`server/session/event_runner.py` に `SessionEventRunner` を追加した。

`SessionEventRunner` は `SessionEvent` の queue / drain / reduce / event-local command handling を担当する。
`SessionSignalDispatcher` は `SessionEvent` と `PlaybackTelemetry` を runner へ渡し、
`TomoroSession` は public API / state owner / output boundary として残る。

これにより、event path は概ね
`TomoroSession.accept_signal()` -> `SessionSignalDispatcher` -> `SessionEventRunner` ->
`TransitionResult` -> `TomoroSession` output boundary という読み方になる。
`core.py` には `_post_event` / `_drain_events` / `_process_event` / `_reduce` を残さない。

### 確定した判断: Phase 10.17.2 の session watcher 移行は 1 command ずつ進める
2026-05-28 に、`TomoroSessionEffects.run_commands()` が実行する `session_watcher` command と
pending command の表を更新した。

`send_audio_control_stop` は既存 `_send_reserved_audio_stop()` を呼ぶだけで、`audio_control` stop event の
payload 形式を変えないため、低リスクな 1 種類として Effects 実行済みに移した。

`cancel_reply_generation` は reply task / TTS worker / `reply_cancel_status` の待ち合わせに触るため、
今回の移行では pending のまま残す。
`save_tomoko_turn` / `start_reply_generation` / `write_ambient_observer` も、それぞれ log write、
LLM/TTS orchestration、ambient observer path の境界整理が必要なので pending のまま扱う。

この移行では新しい Demand / Watcher / OutputDemand 型は追加しない。
`reply_done` / lifecycle routing / audio hot path も変更しない。

### 確定した判断: write_ambient_observer は transcript/event path の observer write として Effects へ移す
2026-05-28 に、`write_ambient_observer` を pending `session_watcher` command から
`TomoroSessionEffects` 実行済み command へ移した。

この command は `SessionEventRunner` の `transcript_finalized` reduce が、playback echo /
continue speaking の observer 経路で返す。
実行内容は既存の observer write と同じく `ambient_log_writer.write()` を await し、
その後 `transcript_final` を client notification として出すだけにする。

ambient log write は audio chunk hot path ではなく STT 確定後の transcript / event path にある。
既存 direct write は DB write 失敗を catch していないため、Effects 側も例外を握りつぶさず伝播させる。

この移行では result input 化、新しい Demand / Watcher / OutputDemand 型追加、
`cancel_reply_generation` / `reply_done` / lifecycle routing / hot path の変更は行わない。

### 確定した判断: Phase 10.17.4a TranscriptFlow はまず現状 map と characterization で固定する
2026-05-28 に、`TranscriptFlow` を closed-loop の changer として読むため、
`server/session/transcript_flow.py` に `TRANSCRIPT_FLOW_CLOSED_LOOP_MAP` を追加した。

この map は runtime 挙動を変えるものではなく、現状分類のための静的な表である。
`transcript_filter` / `turn_taking_decision` / `barge_in_decision` /
`participation_decision` / `session_lifecycle` は changer として読み、
`reply_start_decision` は現時点では watcher boundary、`audio_input_reset` は input boundary として読む。

今回の characterization では、barge-in / participation / session lifecycle に残っている
client event、ambient log write、conversation log write、embedding scheduling、reply start などの
direct output をあえて移動せず、テストで現状として固定した。
新しい command 追加、reply orchestration 変更、`reply_done` / hot path / `/ws` contract の変更は行わない。

### 確定した判断: Phase 10.17.4b TranscriptFlow direct output は分類だけ先に固定する
2026-05-28 に、`TRANSCRIPT_FLOW_DIRECT_OUTPUT_CLASSIFICATIONS` を追加し、
`barge_in_decision` / `participation_decision` / `session_lifecycle` の direct output を分類した。

`initiative_feedback` は active initiative feedback scope を消費するため changer/state update として残す。
`ambient_log_write`、`insert_stop_intent_observation`、`write_ambient_observer`、
`conversation_log_write`、`conversation_embedding_schedule`、`send_audio_control_stop` は
demand emission 候補として扱う。
`client_barge_in_event`、`client_transcript_final_event`、`client_participation_event` は
既存 `/ws` contract の client notification として維持する。
`cancel_reply_generation` は reply task / TTS cancellation ordering に触るため、
TranscriptFlow ではなく reply orchestration 側の所有物として分類する。

この分類では direct output の移動、新しい command 追加、reply orchestration 変更、
audio hot path 変更、`/ws` contract 変更は行わない。

### 確定した判断: Phase 10.17.4c demand emission 候補の Effects 到達状況
2026-05-28 に、`TRANSCRIPT_FLOW_DEMAND_EMISSION_READINESS` を追加し、
TranscriptFlow の demand emission 候補が既存 `SessionCommand` / `TomoroSessionEffects` へ
到達済みか分類した。

`insert_stop_intent_observation`、`send_audio_control_stop`、`write_ambient_observer` は
already-command-and-effects である。
command-but-effects-pending に該当する demand emission 候補は現時点ではない。
`ambient_log_write` は direct-output-not-command で、新規 command を足すなら別途小さい設計が必要。
`conversation_log_write` と `conversation_embedding_schedule` は user turn persistence、
turn identity、embedding scheduling に絡むため should-not-move-yet とする。

この棚卸しでは direct output の移動、新しい command 追加、reply orchestration 変更、
audio hot path 変更、`/ws` contract 変更は行わない。

### 確定した判断: Phase 10.17.5a Candidate / initiative flow は map だけ先に固定する
2026-05-28 に、B として Candidate / initiative flow の current closed-loop map を固定した。

`idle_timer_elapsed` / `session_started` は reducer 側 changer として candidate reply gate を読み、
`fetch_initiative_candidate` / `fetch_arrival_candidate` demand を出す。
`CandidateCommandRunner` は candidate store I/O を実行し、結果を
`initiative_candidate_loaded` / `arrival_candidate_loaded` として session input に戻す watcher output と読む。

candidate loaded 後の stale 判定、candidate payload 判定、final gate、policy / behavior 判定、
start reason 設定は reducer 側 changer として読む。
`start_initiative_reply` / `start_arrival_reply`、`mark_utterance_spoken`、
`mark_arrival_used`、`dismiss_utterance_candidate` は gateway runner output として扱う。
candidate final gate は引き続き TomoroSession 側に残す。

この map では実行配線、candidate 処理、reply orchestration、hot path、`/ws` contract は変更しない。

### 確定した判断: Phase 10.17.5b Candidate demand/output readiness
2026-05-28 に、`CANDIDATE_FLOW_DEMAND_OUTPUT_READINESS` と
`CANDIDATE_FLOW_FINAL_GATE_READINESS` を追加し、Candidate / initiative flow の
demand/output readiness を分類した。

`fetch_arrival_candidate`、`fetch_initiative_candidate`、`judge_initiative_candidate`、
`mark_arrival_used`、`mark_utterance_spoken`、`dismiss_utterance_candidate` は
already-command-and-runner であり、candidate store I/O は gateway runner 側でよい。
command-but-runner-pending は現時点ではない。

`initiative_candidate_loaded` / `arrival_candidate_loaded` 後の final gate は
TomoroSessionReducer 側に残す。
`start_arrival_reply` / `start_initiative_reply` は runner から
`start_precomputed_reply()` へ渡るが、reply output ordering は session reply path に属するため
reply-orchestration-owned とする。
`candidate_command_failed` は CandidateCommandRunner が `SessionEvent` として
`accept_signal()` に戻す new input として扱う。

この分類では実行配線、新規 command、OutputDemand / Watcher 新設、reply orchestration 変更、
audio hot path 変更は行わない。

### 確定した判断: Phase 10.17.6a ReplyOrchestrator closed-loop map-only
2026-05-28 に、`REPLY_FLOW_CLOSED_LOOP_MAP` を追加し、
ReplyOrchestrator 周辺を実行変更なしで分類した。

`ReplyOrchestrator.reply_to()` は TomoroSession が承認済みの reply input を受けて
LLM/TTS を実行する入口であり、session participation / lifecycle 判断は持たない。
`start_precomputed_reply()` は candidate runner output を受ける TomoroSession 側の
changer/state update として読む。
stop ack reply path は cancellation、reserved audio control、`reply_done` control notification が
絡むため should-not-move-yet とする。

`reply_text` delta は hot-ish client notification、`emotion` は client notification、
TTS flush / audio chunk は audio hot path として維持する。
`reply_done` は lifecycle boundary だが、通常 reply / precomputed reply / stop ack のいずれも
client notification のまま維持する。
reply cancellation / interruption と TTS finished は future new-input candidate として読むが、
今回は配線せず input queue にも戻さない。

この map では実行配線、新規 command、new input queue 再投入、OutputDemand / Watcher 新設、
`reply_done` routing、audio hot path、`/ws` contract は変更しない。

### 確定した判断: Phase 10.17.6b flow map consistency guard
2026-05-28 に、`tests/unit/test_session_flow_map_consistency.py` を追加し、
`candidate_flow.py` と `reply_flow.py` の分類語彙と責務境界を横断 test で固定した。

`already-command-and-runner` / `command-but-runner-pending` は candidate runner readiness の
語彙として扱い、reply flow には持ち込まない。
`start_arrival_reply` / `start_initiative_reply` は candidate flow 上では
reply-orchestration-owned だが、reply flow 側では `start_precomputed_reply()` が
TomoroSession owned の changer/state update 境界として受ける。

`should-not-move-yet` は stop ack path や LLM/TTS 実行順序のように「今は動かさない」
領域を示し、`future new-input candidate` は reply cancellation / TTS finished のように
将来 coarse lifecycle input にできる候補を示す。両者は別概念として扱う。
`candidate_command_failed` は gateway runner output の new input であり、reply flow の
future new-input candidate とは混ぜない。

`reply_text` delta は hot-ish client notification、audio chunk は audio hot path、
`reply_done` は lifecycle boundary だが client notification のまま維持する。
この整合性確認では実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、
reply orchestration 制御変更、audio hot path 変更は行わない。

### 確定した判断: Phase 10.17.6c OutputDemand / output boundary map-only
2026-05-28 に、`server/session/output_flow.py` を追加し、
candidate flow / reply flow / output boundary の語彙を map-only で整理した。

candidate demand は「runner に何かを実行してほしい」という要求であり、
client notification ではない。
`fetch_arrival_candidate` / `fetch_initiative_candidate` /
`judge_initiative_candidate` / candidate store mark / dismiss / reply start は
candidate-demand として読む。

runner output は command 実行結果であり、reply future candidate とは混ぜない。
`initiative_candidate_loaded` / `arrival_candidate_loaded` /
`candidate_command_failed` は gateway runner output 由来の new input である。
一方、reply cancellation / TTS finished は reply flow 側の future output demand candidate であり、
今回も配線しない。

`reply_text` delta と `reply_done` は client notification 側に残す。
audio chunk は audio hot path に残し、OutputDemand 側へ移動しない。
OutputDemand / Watcher は future work として分類するだけで、class や runtime path は実装しない。

この map では実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、
reply orchestration 制御変更、audio hot path 変更は行わない。

### 確定した判断: Phase 10.17.6d reply lifecycle boundary map-only
2026-05-28 に、`server/session/lifecycle_flow.py` を追加し、
reply lifecycle boundary 周辺を map-only で整理した。

`reply_done` は coarse lifecycle boundary として読めるが、現時点では
normal reply / precomputed reply / stop ack のいずれも client notification のまま維持する。
`reply_done` を SessionEventRunner や lifecycle input へ移管しない。

reply cancellation と TTS finished は future new-input candidate として読むが、
今回は event emission も input queue 再投入も行わない。
interruption / cancellation は lifecycle に関係する境界だが、ReplyOrchestrator の制御や
task cancellation ordering は変更しない。

stop ack reply path は `reply_done` control notification を出すため確認対象にするが、
`TomoroSessionEffects._apply_stop_intent_ack()` の経路は変更しない。
audio chunk は audio hot path のまま維持し、LLM/TTS 実行順序は should-not-move-yet として残す。

この map では `reply_done` 移管、cancel / TTS finished の new input 配線、
interruption / cancellation 制御変更、stop ack 経路変更、audio chunk 経路変更、
LLM/TTS 順序変更、OutputDemand / Watcher 実装は行わない。

### 確定した判断: Phase 10.17.6e flow vocabulary registry map-only
2026-05-28 に、`server/session/flow_registry.py` を追加し、
candidate_flow / reply_flow / output_flow / lifecycle_flow に散っていた分類語彙を
map-only の registry として一覧化した。

`candidate-demand` は client notification ではなく、`client-notification` は
candidate demand ではない。
`runner-output` は command 実行結果であり、reply future candidate とは混ぜない。
`future-new-input-candidate` / `future-output-demand-candidate` /
`future-watcher-candidate` は未実装候補であり、実装済み new input / OutputDemand /
Watcher を意味しない。

`should-not-move-yet` は「今は動かさない」領域であり、future candidate とは別概念として扱う。
`audio hot path` / `audio-hot-path` は OutputDemand 側へ寄せない。
`lifecycle boundary` / `lifecycle-boundary` は境界分類であって、即座の lifecycle 移管を
意味しない。
`no-routing-change` / `no-hot-path-change` は共通 guard として扱う。

この registry は実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、
ReplyOrchestrator 制御変更、`reply_done` 移管、cancel / TTS finished new input 配線、
stop ack 経路変更、audio chunk 経路変更、LLM/TTS 順序変更を行わない。

### 確定した判断: Phase 10.17.6f forbidden transition map-only
2026-05-28 に、`server/session/flow_forbidden_transitions.py` を追加し、
flow map 語彙について今のフェーズでは移動・統合・配線してはいけない関係を
forbidden transition として固定した。

`client-notification` と `candidate-demand` は相互に変換しない。
`runner-output` は reply future candidate ではない。
`future-new-input-candidate` / `future-output-demand-candidate` /
`future-watcher-candidate` は runtime-current へ昇格しない。
`should-not-move-yet` は future candidate ではなく、現在の経路を凍結する警戒標識である。

`audio-hot-path` は OutputDemand や client notification に吸収しない。
`lifecycle-boundary` は runtime lifecycle migration を意味しない。
`reply_done` は lifecycle-relevant だが client notification のまま維持する。
`reply_cancelled` / `tts_finished` は new input implementation にしない。
stop ack path は routing change しない。

この forbidden transition map は実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、
ReplyOrchestrator 制御変更、`reply_done` 移管、cancel / TTS finished new input 配線、
stop ack 経路変更、audio chunk 経路変更、LLM/TTS 順序変更を行わない。

### 確定した判断: Phase 10.17.6g runtime touchpoint audit
2026-05-28 に、`server/session/flow_runtime_touchpoints.py` を追加し、
既存 runtime code のうち closed-loop flow map と関係する touchpoint を
read-only / map-only で監査した。

TomoroSession の `accept_signal()` は session-owned runtime entry のまま維持する。
`_send_event()` は websocket client notification path であり、candidate demand へ変換しない。
`_send_audio_chunk()` は audio-hot-path であり、OutputDemand や JSON client notification へ寄せない。

ReplyOrchestrator は `reply_to()` で承認済み reply を実行し、LLM stream ->
`reply_text` / emotion -> TTS queue / flush -> audio -> `reply_done` の順序を維持する。
この LLM/TTS ordering は `should-not-move-yet` に対応する touchpoint である。
normal reply と precomputed reply の `reply_done` は client notification のまま維持する。

CandidateCommandRunner の `initiative_candidate_loaded` / `arrival_candidate_loaded` /
`candidate_command_failed` は runner-output-path であり、既に `accept_signal()` 経由で session に戻る。
これは reply future candidate ではない。

stop ack reply path は `TomoroSessionEffects._apply_stop_intent_ack -> /ws reply_done control`
のまま維持する。
cancellation / interruption は future-migration-candidate touchpoint として記録するが、
`reply_cancelled` / `tts_finished` の new input 実装は行わない。

この runtime touchpoint audit は実行配線、新規 command、runner 実装、
OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、`reply_done` 移管、
cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、
LLM/TTS 順序変更を行わない。

### 確定した判断: Phase 10.17.6h migration readiness checklist
2026-05-28 に、`server/session/flow_migration_readiness.py` を追加し、
10.17.6a〜10.17.6g で固定した flow map / registry / forbidden transition /
runtime touchpoint をもとに、次フェーズで実装変更に入るための readiness checklist を
map-only / docs-only で定義した。

readiness checklist は実装許可ではない。
`future-new-input-candidate` / `future-output-demand-candidate` /
`future-watcher-candidate` は、explicit phase、dedicated test、forbidden transition check、
touchpoint check、owner boundary check、doc update を満たすまで runtime-current に昇格しない。

`should-not-move-yet` は readiness の存在だけでは解除されない。
LLM/TTS ordering と stop ack path は explicit phase が来るまで移動不可とする。
audio hot path は dedicated test と no-hot-path-change guard なしに触らない。
`reply_done` は lifecycle boundary だが、migration readiness を満たすまで client notification のまま維持する。
`reply_cancelled` / `tts_finished` は future new-input candidate だが、explicit phase なしに配線しない。
OutputDemand / Watcher は future candidate であり、別実装フェーズを切るまで実装しない。

runtime touchpoint が記録済みであることは、実装変更の許可を意味しない。
この checklist は runtime code の制御変更、実行配線、新規 command、runner 実装、
OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、`reply_done` 移管、
cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、
LLM/TTS 順序変更を行わない。

### 確定した判断: Phase 10.17.6i minimal runtime change candidate selection
2026-05-28 に、`server/session/flow_runtime_change_candidates.py` を追加し、
10.17.6a〜10.17.6h の map / registry / forbidden transition / runtime touchpoint /
migration readiness をもとに、次フェーズで最初に実装してよい候補を
map-only / docs-only で 1 つに絞った。

first runtime change candidate は `runtime_touchpoint_read_only_helper` とする。
これは既存 `FLOW_RUNTIME_TOUCHPOINTS` を読む read-only helper であり、
route change、audio hot path、ReplyOrchestrator 制御、lifecycle migration、
future-* の runtime-current 昇格、LLM/TTS 実行順序変更、新規 command を伴わないため、
最小で戻しやすく unit test で囲いやすい。

`candidate_runner_output_read_only_helper` は候補としては小さいが、candidate runner output path は
既に runtime-current であり、最初に触ると runner-output と session input の境界を曖昧にするため保留する。
`reply_done_lifecycle_migration` は forbidden transition と lifecycle migration に抵触するため保留する。
`reply_cancelled` / `tts_finished` の new input 化は future-new-input candidate を runtime-current へ
昇格させるため保留する。
OutputDemand / Watcher は future unimplemented abstraction なので別 phase まで実装しない。
stop ack path、audio hot path、LLM/TTS ordering はそれぞれ dedicated phase なしに触らない。

この candidate selection は runtime code の制御変更、実行配線、新規 command、runner 実装、
OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、`reply_done` 移管、
cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、
LLM/TTS 順序変更を行わない。

### 確定した判断: Phase 10.17.6i checkpoint は helper 実装を延期する
Phase 10.17.6i の `runtime_touchpoint_read_only_helper` 選定は維持するが、
次にすぐ実装へ進まない。

この helper は production runtime change というより、既存 `FLOW_RUNTIME_TOUCHPOINTS` を
読みやすくする read-only inspection helper である。
現時点では `FLOW_RUNTIME_TOUCHPOINTS` / `FLOW_RUNTIME_CHANGE_CANDIDATES` /
characterization test / PLAN / MEMORY で判断材料は足りているため、
helper の実装価値はまだ薄い。

もし helper を入れる場合も、production runtime path からは呼ばない。
次 phase は `10.17.6j: runtime touchpoint read-only helper, not used by production path`
として明示し、unit test、docs update、no-routing-change、no-hot-path-change を固定する。
TomoroSession、ReplyOrchestrator、CandidateCommandRunner、websocket adapter、audio path から
import / call しない。

10.17.6i の reject 判断は維持する。
`reply_done` lifecycle migration、`reply_cancelled` / `tts_finished` new input 化、
OutputDemand / Watcher 実装、stop ack path、audio hot path、LLM/TTS ordering はまだ禁止する。
次に進むなら、A: 10.17.6i で停止して 10.17 checkpoint / 実ブラウザ確認へ進む方を推奨する。

### 確定した判断: Phase 10.17 checkpoint runtime verification
2026-05-28 22:31:44 起動後、22:32:05〜22:33:26 の実ブラウザ会話は最後まで通った。

確認できた runtime 経路は、`/ws` 接続、wake word、conversation session start、
`ambient -> engaged`、reply / TTS / audio、follow-up、`cooldown -> ambient`、
conversation session close までである。
`arrival_candidate_loaded` は `SessionEventRunner lifecycle_new_input_candidate`
として trace された。

`reply_text` / TTS / audio は hot-ish / hot path のままで、
`lifecycle_new_input_candidate` には混ざっていない。
`reply_done` は lifecycle input に移管されておらず、client notification のまま維持する。
cancel / TTS finished new input 化の痕跡は直近 runtime log にはない。

22:31:44〜22:33:26 の直近 runtime には `ERROR` / `Traceback` /
未実装 command warning は見当たらない。
NumPy writable warning は既存 PyTorch warning として扱い、
今回の 10.17 closed-loop map 変更由来の破損ではなさそうである。

10.17.6 系の map / registry / forbidden / readiness / touchpoint /
candidate selection は runtime を壊していない。
10.17.6i は checkpoint として維持し、`runtime_touchpoint_read_only_helper` 実装は延期する。
次に進む場合も、runtime 実装ではなく次フェーズ設計または実ブラウザ追加確認から始める。

### 確定した判断: 10.17 の次に runtime 実装へ入る前の原則
Phase 10.17 final checkpoint 時点では、closed-loop map、SessionCommand owner 分類、
TranscriptFlow / CandidateFlow / ReplyFlow / OutputFlow / LifecycleFlow、flow registry、
forbidden transitions、runtime touchpoints、migration readiness、runtime change candidates は
次 phase のための制御地図として固定済みである。

次に runtime 実装へ入る前に、必ず explicit phase、docs / map / characterization test、
no-routing-change guard、no-hot-path-change guard、owner boundary check を先に置く。
runtime touchpoint が map に記録されていることは実装許可を意味しない。

`reply_done` は lifecycle boundary だが client notification のまま維持する。
`reply_cancelled` / `tts_finished` は future new-input candidate だが未配線のまま維持する。
OutputDemand / Watcher は future candidate だが未実装のまま維持する。
audio hot path、LLM-TTS ordering、stop ack path は dedicated phase と dedicated test なしに触らない。

`runtime_touchpoint_read_only_helper` は候補として維持するが、必要性が再確認されるまで実装しない。
次フェーズ候補は A: 実ブラウザ追加確認、B: DB write demand 化の設計だけ、
C: high-risk reply command の個別設計だけ、の3つに絞る。
どれを選んでも最初は runtime 実装ではなく docs / map / characterization test から始める。

### 確定した判断: Phase 10.18.0 DB write demand boundary design
2026-05-28 に、DB write 系の副作用を closed-loop 上で読むため、
`server/session/db_write_flow.py` を map-only / docs-only で追加した。

`ambient_log_write` は `direct-db-write-current` かつ `future-db-demand-candidate` だが、
まだ SessionCommand 化しない。
現在の ambient write は writer がある場合に await され、失敗時は例外伝播する。
future command 化する場合は、この failure policy を維持するか明示的に変更する dedicated phase が必要である。

`conversation_log_write` / `tomoko_turn_save` / `interrupted_turn_save` は turn persistence と
active conversation session id、reply cancellation status に関わるため `should-not-move-yet` とする。
`conversation_embedding_schedule` は memory pipeline / background task に関わり、
失敗時は warning-only なので、DB write demand 化とは別設計にする。

`candidate_store_mark_spoken` / `candidate_store_mark_dismissed` /
`candidate_store_mark_arrival_used` は gateway candidate runner owned であり、
session-owned DB write demand と混ぜない。
candidate runner の失敗は既存通り warning と `candidate_command_failed` new input で扱う。

Phase 10.18.0 は runtime code の制御変更、DB write 実行経路変更、新規 SessionCommand、
TomoroSessionEffects への新規 DB write 実装、candidate runner 変更、OutputDemand / Watcher 実装、
ReplyOrchestrator 制御変更、`reply_done` 移管、cancel / TTS finished new input 配線、
audio hot path、`/ws` contract、LLM/TTS ordering の変更を行わない。

### 確定した判断: Phase 10.18.1 ambient_log_write characterization
2026-05-28 に、`ambient_log_write` の現状挙動を characterization test で固定した。

`ambient_log_write` は `TranscriptFlow` の participation decision 後に direct await される。
participating utterance では、現状の順序は
`ambient_log_write -> user_turn_write -> reply_start` であり、reply start より前に完了する。
observer / non-participating transcript でも ambient log は書かれる。

payload は transcript、previous attention、attended、participation mode、
should participate 相当の `tomoko_participated` を反映する。
`ambient_log_writer.write()` が例外を投げた場合は既存通り例外伝播し、reply start へ進まない。

`ambient_log_write` は `write_ambient_observer` command/effects 済み path とは別系統である。
SessionCommand 化、TomoroSessionEffects への移動、非同期化、result input 化、
failure policy / ordering / payload 変更は行わない。

現時点で `ambient_log_write` を SessionCommand 化する価値はまだ低い。
同期実行のまま command 化しても latency は改善せず、価値は境界整理に限られる。
将来 command 化する場合は、failure policy と reply start ordering を変えない dedicated phase が必要である。

### 確定した判断: Phase 10.19 session package simplification checkpoint
2026-05-28 に、`server/session/` package の分割状態を読みやすさの観点で確認した。

10.17 / 10.18 で得た closed-loop map、forbidden transitions、migration readiness、
runtime touchpoint、DB write boundary の知見は保持する。
ただし、それらを保存するために実装分割を固定し続ける必要はない。
人間が読みづらい場合、map/test-only や docs-like guard から統合・移動してよい。

現時点の読みづらさは、runtime essential な `core.py` / reducer / effects /
ReplyOrchestrator と、map-only guard が同じ `server/session/` 直下に並んでいることにある。
map constants が runtime wiring に見えやすいので、最初の simplification は
`candidate_flow.py` / `reply_flow.py` / `output_flow.py` / `lifecycle_flow.py` /
`db_write_flow.py` と `flow_*` guard 群を `server/session/maps/` または単一
`flow_maps.py` に寄せる案から始める。

runtime essential な `ReplyOrchestrator`、reducer、effects、state、audio hot path はすぐには動かさない。
monolith に戻す場合も、先に lifecycle / transcript / candidate gates / reply boundary /
DB write boundary / command-effects boundary / flow-map appendix の section map を置く。

Phase 10.19 は runtime behavior、public API、`/ws` contract、audio hot path、
ReplyOrchestrator ordering、DB write ordering、新規 SessionCommand、OutputDemand / Watcher、
lifecycle migration、cancel / TTS finished new input 化を変更しない。

### 確定した判断: Phase 10.19.1 map-only guard relocation plan
2026-05-28 に、`server/session/` 直下の map-only / docs-like guard 群の退避先を整理した。

`candidate_flow.py` / `reply_flow.py` / `output_flow.py` / `lifecycle_flow.py` /
`db_write_flow.py` / `flow_registry.py` / `flow_forbidden_transitions.py` /
`flow_runtime_touchpoints.py` / `flow_migration_readiness.py` /
`flow_runtime_change_candidates.py` は、runtime code からは import されておらず、
unit test からのみ参照されている。

最小移動案は `server/session/maps/` へ退避すること。
これにより Python の characterization test を維持したまま、session root から
map-only guard を視覚的に分離できる。
`_docs/session_closed_loop/` は人間には読みやすいが、testable guard として弱くなるため第一候補ではない。
ARCHITECTURE.md / PLAN.md に圧縮してコードファイルを削る案は、
deterministic test guard が失われるため今は避ける。

次に実施する場合も、runtime essential な `core.py`、`reply_orchestrator.py`、
`reducer.py`、`effects.py`、`state.py`、audio hot path は動かさない。
移動対象は map-only / docs-like guard 群だけとし、unit test import 更新に限定する。

### 確定した判断: Phase 10.19.2 map-only guard relocation
2026-05-28 に、map-only / guard-only の 10 ファイルを `server/session/maps/` へ移動した。

移動対象は `candidate_flow.py` / `reply_flow.py` / `output_flow.py` /
`lifecycle_flow.py` / `db_write_flow.py` / `flow_registry.py` /
`flow_forbidden_transitions.py` / `flow_runtime_touchpoints.py` /
`flow_migration_readiness.py` / `flow_runtime_change_candidates.py` である。

runtime essential な `core.py`、`reply_orchestrator.py`、`reducer.py`、`effects.py`、
`state.py`、audio hot path は動かしていない。
unit test import は `server.session.maps.*` に更新し、deterministic test guard は維持した。
runtime code は `server.session.maps` に依存しない。

この relocation は読みやすさの整理であり、runtime behavior、public API、`/ws` contract、
command / runner、OutputDemand / Watcher、`reply_done` 移管、cancel / TTS finished new input 化、
DB write demand 化、audio hot path、LLM/TTS ordering は変更しない。

### 確定した判断: session.py 一枚時代は functional baseline として再評価対象
2026-05-28 の Phase 10.19.x pre-split baseline audit で、Phase 10.12 package split 直前の commit は
`960be36` と特定した。
直後の `b254d32` が `server/session.py` を `server/session/core.py` へ移し、
`carryover.py` / `reducer.py` / `effects.py` / `reply_orchestrator.py` を追加した package split commit である。

`960be36` 時点の一枚 `server/session.py` は、STT / participation / reply / TTS /
playback / turn-taking / candidate / memory の主要 runtime 機能を既に持っていた。
検証は `.venv/bin/python -m pytest -m unit` が `377 passed, 17 deselected`、
`PORT=8018 make server-debug` が startup complete / `GET /` 200 の smoke までである。
一方で、Phase 8.8.8 memory tuning、Phase 10.10 initiative、Phase 10.11 turn-taking の
実マイク browser quality tuning は未完了だったため、これは quality-complete baseline ではなく
unit + startup smoke 済み functional baseline として扱う。

もし一枚時代へ戻すなら、baseline は `960be36` を第一候補にする。
ただし、split 後に入った `write_ambient_observer` effects 実行 path と
`send_audio_control_stop` effects path は、戻すと失われる可能性がある実機能として別途保持候補にする。
flow maps / registry / forbidden transitions / readiness / runtime touchpoints / DB write flow /
reply lifecycle inventory / closed-loop vocabulary は、実装分割として保持しなくても
PLAN / LOG / MEMORY / README / test appendix に知見として残せる。

### 確定した判断: 復旧ブランチでは closed-loop をまず一枚 session.py の読み方として固定する
2026-05-28 の復旧ブランチでは、runtime code は `960be36` の `server/session.py` 一枚構成を baseline として戻した状態で扱う。

未来の PLAN.md / LOG.md / MEMORY.md / ARCHITECTURE.md に残る package split、closed-loop convergence、
map-only guard、forbidden transition、readiness、rollback 判断は、今すぐ再実装する計画ではなく、
何を触ると危ないか、何を禁止すべきかの記録として読む。

closed-loop architecture を再開する場合も、まず `server/session.py` の中の現行メソッド群を
`input` / `changer` / `state` / `demand` / `watcher` / `output` / `new input` /
`hot path` / `should-not-move-yet` に対応づける docs-only map から始める。
`dispatcher.py` / `effects.py` / `event_runner.py` / `flow_*` / `maps` package は再作成しない。
OutputDemand / Watcher class、DB write の SessionCommand 化、`reply_done` lifecycle input 化、
cancel / `tts_finished` new input 化、audio hot path、TTS flush、audio chunk、playback timing、
LLM/TTS ordering、ambient / conversation log write ordering は触らない。

### 確定した判断: Phase 10.20.0 split restart は closed-loop 用語に合わせて 1 責務ずつ進める
2026-05-29 の Phase 10.20.0 では、未来の package split / dispatcher / effects /
event_runner / maps package 方式を再実装しない。

復旧ブランチの一枚 `server/session.py` baseline から split を再開する場合は、
ARCHITECTURE.md の closed-loop 用語に合わせ、1 phase で 1 責務だけを対象にする。
最初の候補は `state container` に絞る。
現時点では `server/session/state.py` が存在せず、runtime state field は `TomoroSession.__init__` と
`get_now_state()` / transition helpers に残っているため、次に切るなら `state.py` への pure extraction だけを検討する。

ただし Phase 10.20.0 自体では runtime code を変更しない。
先に PLAN.md 上で、state container を候補として固定し、input_router 相当の入口整理や pure helper 抽出を今回は保留した。
次に進む場合も characterization test で `get_now_state()` と state ownership を固定してから、
runtime behavior を変えない pure extraction に限定する。

`TomoroSession` は引き続き final owner であり、state container は判断体にしない。
audio hot path、reply / TTS ordering、`reply_text` / `reply_done` routing、
cancel / TTS finished new input 化、OutputDemand / Watcher、DB write SessionCommand 化、
ambient_log_write 非同期化、dispatcher / effects / event_runner / maps package 復活は行わない。

### 確定した判断: Phase 10.20.1 state container 初回候補は latency probe state に限定する
2026-05-29 の Phase 10.20.1 では、monolithic `server/session.py` の
`TomoroSession.__init__` 内 field を docs-only で棚卸しした。

依存注入 collaborator は state container に入れない。
`state` / `attention_mode` / `audio_turns` は authoritative state だが、VAD hot path、
attention lifecycle、playback telemetry、candidate final gate に近いため初回抽出では触らない。
reply task / TTS queue / cancellation state、candidate request id、active conversation session id、
memory carryover、precomputed reply context、turn-taking transient state もそれぞれ ordering / gate /
DB write / memory quality / stop-restart 体感へ影響するため core に残す。

次に state container 実装へ進む場合は、`_latency_speech_end_at` /
`_latency_reply_start_at` / `_latency_first_reply_text_at` /
`_latency_tts_start_at` / `_latency_first_audio_chunk_at` の
`latency probe state` 1 グループだけを候補にする。
これは観測用 state で、`get_now_state()` の public snapshot には直接出ず、
DB write ordering、candidate gate、conversation lifecycle、reply routing を変えずに
`reset` / `mark_*` / `elapsed_*` の pure container として characterization しやすい。

Phase 10.20.1 では `state.py` 作成、field 移動、property/proxy 追加、import path 変更、
runtime behavior 変更は行わない。

### 確定した判断: Phase 10.20.2 latency probe は characterization を先に固定する
2026-05-29 の Phase 10.20.2 では、`latency probe state` の抽出には進まず、
現状挙動を `tests/unit/test_session_latency_probe_characterization.py` で固定した。

固定対象は `_latency_speech_end_at` / `_latency_reply_start_at` /
`_latency_first_reply_text_at` / `_latency_tts_start_at` /
`_latency_first_audio_chunk_at` / `_reply_output_started` /
`_reply_output_defer_until` である。

`_reset_latency_probe()` は 5 つの latency timestamp と `_reply_output_started` を reset するが、
現状 `_reply_output_defer_until` は reset しない。この挙動は変更せず characterization として固定する。
elapsed 計算は `None` なら `0.0`、mark 済みなら `time.perf_counter()` 差分 ms とする。
`reply_text` は first reply text と output started を mark し、TTS chunk / audio send は
TTS start、first audio chunk、output started を mark する。
`_defer_reply_output()` / `_maybe_wait_reply_output_defer()` は、遅い deadline を保持し、
1 回だけ最大 250ms 待って defer を clear する現状仕様として扱う。

次に実装する場合も、抽出対象はこの latency probe group だけに限定する。
`state.py` 作成、field 移動、property/proxy 追加、import path 変更、runtime behavior 変更は
Phase 10.20.2 では行わない。
reply task lifecycle、TTS queue ownership、audio hot path、LLM/TTS ordering、DB write ordering、
candidate gate、conversation session lifecycle は引き続き core に残す。

### 確定した判断: Phase 10.20.3 は LatencyProbeState 専用抽出に限定する
2026-05-29 の Phase 10.20.3 では、`server/session_latency.py` を追加し、
`LatencyProbeState` に latency probe state だけを抽出した。

抽出対象は `_latency_speech_end_at` / `_latency_reply_start_at` /
`_latency_first_reply_text_at` / `_latency_tts_start_at` /
`_latency_first_audio_chunk_at` / `_reply_output_started` /
`_reply_output_defer_until` 相当の状態である。

`TomoroSession._reset_latency_probe()` と `_elapsed_since_*_ms()` は残し、
内部で `LatencyProbeState` へ委譲する。
mark 位置、latency log 文言、elapsed ms 計算、reply output timing は変更しない。
`LatencyProbeState.reset()` は Phase 10.20.2 の characterization どおり
`reply_output_defer_until` を reset しない。

これは汎用 state container ではない。
`server/session/` package、`state.py`、dispatcher / effects / event_runner / maps、
OutputDemand / Watcher は作らない。
`state` / `attention_mode` / `audio_turns`、reply task / TTS queue lifecycle、
candidate gate、conversation session lifecycle、DB write ordering、LLM/TTS ordering は
引き続き `TomoroSession` core に残す。

### 確定した判断: Phase 10.20.4 split 再開は安全地点ごとに止める
2026-05-29 の Phase 10.20.4 では、Phase 10.20.3 の `LatencyProbeState` 抽出を
monolith baseline からの最初の安全な小分割として扱う。

人間側の実ブラウザ確認で、wake word、conversation session start、`reply_text`、TTS audio、
playback telemetry、follow-up、memory recall が通った。
latency log は `reply_start` / `first_reply_text` / `tts_start` / `first_audio_chunk`
として出ており、runtime error / Traceback / 未実装 warning は見当たらない。
空 transcript / `too_short` / `low_audio_short_text` drop は filter 正常系として扱う。

今後 split を再開する場合も、1 回に 1 責務だけを扱う。
名前は ARCHITECTURE.md の closed-loop 用語または現行責務と一致する dedicated name にする。
汎用 `state.py`、`server/session/` package、dispatcher / effects / event_runner / maps、
OutputDemand / Watcher はまだ作らない。
audio hot path、reply orchestration、reply task / TTS queue、DB write ordering、
candidate gate、conversation session lifecycle は dedicated phase と characterization test なしに触らない。
実装前に必ず characterization test で現状挙動を固定する。

次に抽出してよい候補は `retrieved context carryover state` 1 つだけにする。
対象は `_RetrievedContextCarryoverEntry`、`_retrieved_context_carryover`、
`_retrieved_context_carryover_seq`、carryover merge / remember / evict / clear helper 群である。
これは authoritative state ではなく、audio hot path、reply task / TTS queue、
LLM-TTS ordering、DB write ordering、candidate gate には直接触れない。
ただし memory quality に関わるため、実装する場合は merge order、dedup key、eviction、
session close clear、log 文言を characterization test で固定してから pure extraction に限定する。

### 確定した判断: Phase 10.20.5 は retrieved context carryover 専用抽出に限定する
2026-05-29 の Phase 10.20.5 では、`server/session_carryover.py` を追加し、
`RetrievedContextCarryoverState` に長期記憶 carryover の小さな state/helper だけを抽出した。

抽出対象は `_retrieved_context_carryover` / `_retrieved_context_carryover_seq` 相当の状態と、
`_merge_carried_long_term_memory()` / `_carried_long_term_memory()` /
`_remember_retrieved_context()` / `_evict_retrieved_context_carryover()` /
`_evict_one_carryover()` / `_clear_retrieved_context_carryover()` の中身である。

`TomoroSession` 側の private method 名は残し、既存 log 文言も維持する。
key 生成は `source_id` 優先、fallback は normalized text の sha1 digest のままにする。
merge は fresh memory を先、carried memory を後にし、duplicate key は先に現れた hit を残す。
entry count / text budget eviction、session close clear の挙動も維持する。

この phase では memory retrieval policy、ContextSnapshotBuilder、prompt format、DB query、
context quota / weight、reply orchestration、audio hot path、DB write ordering、
candidate gate、conversation session lifecycle は変更しない。
OutputDemand / Watcher / dispatcher / effects / event_runner / maps、汎用 `state.py` も作らない。
commit は行わず、人間の実ブラウザで「智子、〇〇のこと覚えてる？」、
「もっと詳しく」、同一会話内 follow-up、memory recall / carryover log、返答品質を確認してから判断する。

### 確定した判断: Phase 10.20.6 は pure session payload helper 抽出に限定する
2026-05-29 の Phase 10.20.6 では、`server/session_payloads.py` を追加し、
`server/session.py` 末尾にあった payload helper だけを抽出した。

抽出対象は `json_safe_payload()` / `json_safe_value()` /
`optional_str_payload()` / `optional_int_payload()` / `optional_float_payload()` /
`playback_payload()` / `playback_telemetry_from_event()` である。

これらは状態を持たず、I/O せず、DB / LLM / TTS / audio / task / queue に触らない。
`server/session.py` 側は import と呼び出し名の置き換えだけにし、playback payload 形式、
telemetry coercion、transition emission payload の意味は維持する。

`_candidate_policy_payload()` は `CandidateSpeakDecision` に依存し、今回の pure payload helper
抽出から広げる必要がないため `server/session.py` に残す。

この phase では `server/session/` package、汎用 `state.py`、dispatcher / effects /
event_runner / maps、OutputDemand / Watcher は作らない。
audio hot path、reply orchestration、reply task / TTS queue、`reply_done` routing、
cancel / TTS finished new input 化、DB write ordering、conversation session lifecycle、
memory retrieval policy、prompt format は変更しない。

### 確定した判断: Phase 10.20.7a は session summary memory helper 1 個だけを抽出する
2026-05-29 の Phase 10.20.7a では、Phase 10.20.7 の read-only audit で選定した
`_session_summary_hit_to_memory()` だけを `server/session_memory_helpers.py` に抽出した。

抽出後の public-ish helper 名は `session_summary_hit_to_memory()` とする。
これは `SessionSummaryHit -> MemoryHit` の pure value conversion であり、I/O せず、
DB / LLM / TTS / WebSocket send / audio hot path / task / queue に触らない。

固定した変換は、`speaker="tomoko"`、`text="会話セッション要約: {summary_text}"`、
`timestamp=ended_at or started_at`、`similarity=hit.similarity`、
`emotion=None`、`source_id="session_summary:{session_id}"` である。

この phase では memory retrieval policy、ContextSnapshotBuilder、prompt format、
session summary の取得タイミング / 件数 / ranking、turn memory との優先順位、
timeout / degraded context / fallback、reply orchestration、DB write ordering、
conversation session lifecycle、candidate gate は変更しない。
`server/session/` package、汎用 `state.py`、dispatcher / effects / event_runner / maps、
OutputDemand / Watcher も作らない。

### 確定した判断: Phase 10.20.8 は candidate request id formatter だけを抽出する
2026-05-29 の Phase 10.20.8 では、`server/session.py` に残る key generation 系を棚卸しし、
`_new_candidate_request_id()` 内の request id 文字列 format だけを
`server/session_key_helpers.py` の `candidate_request_id(kind, sequence)` に抽出した。

抽出対象は `initiative-1` / `arrival-2` のような文字列生成だけである。
`_candidate_request_sequence += 1`、`_active_initiative_request_id` /
`_active_arrival_request_id` 更新、`_is_stale_candidate_result()`、candidate final gate は
引き続き `TomoroSession` に残す。

`_new_candidate_request_id()` method 全体、conversation session id、turn id / chunk id、
playback telemetry id、`_context_build_id` は ordering / lifecycle / stale 判定 / context build に
近いため抽出しない。

この phase では runtime behavior、audio hot path、playback telemetry ordering、reply routing、
LLM/TTS ordering、DB ordering、conversation lifecycle、memory retrieval policy、
ContextSnapshotBuilder、prompt format、candidate gate、stale result discard policy は変更しない。
OutputDemand / Watcher、dispatcher / effects / event_runner / maps、`server/session/` package split、
汎用 `state.py` も作らない。

### 確定した判断: Phase 10.20.9 では次の helper extraction 候補を選ばない
2026-05-29 の Phase 10.20.9 では、`server/session.py` に残る small helper /
value object / formatter / coercion / mapping 的な候補を read-only で棚卸しした。

Phase 10.20.6 の `server/session_payloads.py`、Phase 10.20.7a の
`server/session_memory_helpers.py`、Phase 10.20.8 の `server/session_key_helpers.py`、
および `server/session_carryover.py` の抽出範囲は、いずれも narrow helper / state に限定されている。

残っている low-risk 候補は `_elapsed_ms()` と `_retrieved_context_key()` の薄い wrapper だが、
これは新しい helper extraction ではなく cleanup 対象である。
`_candidate_policy_payload()` は pure に近いが candidate policy / gate observability に依存する。
`_accepts_keyword()` は pure だが DB writer compatibility path にあり、DB write ordering に近い。
`_start_reason_from_participation_mode()` は pure だが conversation lifecycle の start reason に直結する。

そのため Phase 10.20.9 の next-extractable-candidate は 0 個とする。
次に進む場合も、候補を別 Phase で 1 つに絞り、characterization test から始める。
candidate gate、stale result discard、reply lifecycle、turn-taking、playback timing、
memory retrieval policy、ContextSnapshotBuilder、prompt format、DB write ordering には踏み込まない。

### 確定した判断: candidate policy helper extraction は payload shaping だけに限定する
2026-05-29 の Phase 10.20.7 candidate policy helper extraction では、
`_candidate_policy_payload()` だけを `server/session_candidate_policy_helpers.py` の
`candidate_policy_payload(event)` に抽出した。

この helper は `event.payload["policy_decision"]` が `CandidateSpeakDecision` の場合だけ
`to_json()` を返し、それ以外は `None` を返す pure payload shaping である。
`schema_version` / `decision` / `score` / `threshold` / `reason` / `signals` の JSON shape は維持する。

candidate final gate ownership は移動しない。
`_candidate_reply_gate_reason()`、`_candidate_reply_gate_payload()`、
`_new_candidate_request_id()`、`_is_stale_candidate_result()` は `TomoroSession` に残す。
candidate store mark、DB read/write、reply start、TTS / audio、WebSocket send、
SessionCommand 追加、OutputDemand / Watcher、`server/session/` package split は行わない。

### 確定した判断: Phase 10.20.8 remaining helper audit では次候補を選ばない
2026-05-29 の Phase 10.20.8 read-only audit では、monolithic `server/session.py`
baseline を維持したまま remaining helper candidates を棚卸しした。

`server/session_payloads.py` と `server/session_candidate_policy_helpers.py` は抽出済みであり、
それぞれ pure payload / coercion helper と `CandidateSpeakDecision` payload shaping に限定されている。
関連する `server/session_key_helpers.py`、`server/session_memory_helpers.py`、
`server/session_carryover.py` も narrow extraction のまま維持する。

残っている low-risk に見える候補は `_elapsed_ms()`、`_retrieved_context_key()`、
carryover wrapper、latency probe wrapper などの cleanup-only である。
`_start_reason_from_participation_mode()` と `_accepts_keyword()` は pure だが、
conversation lifecycle / DB writer compatibility に近い。
candidate gate、stale result discard、playback / output target、withdrawn behavior、
turn-taking、reply orchestration、memory retrieval policy、ContextSnapshotBuilder、
prompt context、DB write ordering に近い helper は抽出対象にしない。

そのため、この read-only audit の next-extractable-candidate は 0 個とする。
次に進む場合も、候補を別 Phase で 1 つに絞り、characterization test から始める。

### 確定した判断: Phase 10.20.10 の 2 ペイン STT ログ UI は client-only に限定する
2026-05-29 の Phase 10.20.10 では、現行 UI と STT 結果ログを左右 2 ペインで表示するが、
ambient / 人間 / Tomoko 回り込みの分類は行わない。

既存 `/ws` の `transcript_final` event を UI 側で一覧表示するだけにし、
`TomoroSession`、`server/session.py`、WebSocket payload shape、参加判断、
conversation session lifecycle、DB write ordering、TTS / playback ordering は変更しない。

正確な ambient / human / tomoko echo 分類が必要になった場合は、UI が推測せず、
server 側から分類済み field を出す別 Phase として扱う。

### 確定した判断: Phase 10.20.11 の Tomoko 返答ログは reply_text 集約に限定する
2026-05-29 の Phase 10.20.11 では、右ペインに Tomoko の返答も表示するが、
実際の `TTSInput.text` を新しい WebSocket payload として出す変更は行わない。

ブラウザが既に受け取っている `reply_text` delta を 1 つの Tomoko log entry に集約し、
`reply_done` で entry を閉じる client-only 実装に限定する。
これにより `TomoroSession`、`server/session.py`、reply orchestration、
TTS ordering、audio hot path、WebSocket payload shape は変更しない。

文単位で実際に TTS backend へ渡された文字列を見たい場合は、server 側から
TTS chunk text を観測用 payload として出す別 Phase として扱う。

### 確定した判断: Phase 10.20.12 は candidate policy の副作用なし判断 helper 2 個だけを抽出する
2026-05-29 の Phase 10.20.12 では、candidate policy 周辺のうち
副作用なしで判定できる小領域だけを `server/session_candidate_policy_helpers.py` に追加した。

抽出対象は `initiative_candidate_text_ready(candidate)` と
`candidate_policy_route(policy_decision)` の 2 つである。
前者は initiative candidate が text-ready かを `maturity >= 1` かつ
`generated_text is not None` で判定するだけにする。
後者は `CandidateSpeakDecision` を `wait` / `needs_llm_judge` / `speak` の route に分類するだけにする。

`TomoroSession` 側の active request id clear、dismiss / judge / reply command 生成、
candidate final gate、stale result discard、DB read/write、reply start、TTS / audio、
WebSocket send は移動しない。

`_candidate_reply_gate_reason()` / `_candidate_reply_gate_payload()` は引き続き
TomoroSession final gate として残す。
`_new_candidate_request_id()` / `_is_stale_candidate_result()` も request sequence / active id /
stale policy に近いため移動しない。
arrival candidate behavior 分岐も `mark_arrival_used` と command ordering に近いため今回の対象外にする。

### 確定した判断: Phase 10.20.13 は context snapshot の long-term memory 整形だけを抽出する
2026-05-29 の Phase 10.20.13 では、context / memory 周辺のうち純粋な整形処理だけを
`server/session_memory_helpers.py` に追加した。

抽出対象は `context_snapshot_long_term_memory(snapshot)` だけである。
これは `TomokoContextSnapshot.session_summaries` を既存の
`session_summary_hit_to_memory()` で `MemoryHit` に変換し、
その後ろに `TomokoContextSnapshot.memory_hits` を連結するだけにする。
順序は session summary memory が先、turn-level memory hits が後である。

`ContextSnapshotBuilder` の policy / DB read / timeout / degraded context、
memory retrieval policy、prompt format、carryover merge / remember / evict / clear、
reply orchestration、TTS / audio / WebSocket send は変更しない。

`session.py` は method 並び替えをせず、section comment だけを追加する。
大きな reorder は挙動差分が埋もれるため、closed-loop の読みやすさ改善は
見出し追加までに限定する。

### 確定した判断: 短期作業メモリは揮発 buffer で prompt hint として試す
2026-05-29 の最小実験では、短期作業メモリを PostgreSQL や long-term memory へ保存しない。
`server/session_short_memory.py` の `ShortMemoryBuffer` を process-local / session-local な
揮発 buffer として扱い、最大 5 件、デフォルト 4 turn TTL で expire する。

この buffer は source of truth ではなく、次ターン以降の prompt hint である。
`ContextSnapshotBuilder` は引き続き読み取り専用の DB/context builder とし、
short memory への書き込み責務を持たせない。
`TomoroSession` が reply 完了後に非同期 extraction task を起動し、
現在ターンの応答 hot path は待たせない。

初期 extraction は LLM structured output ではなく rule/heuristic にする。
保存対象は「作業文脈」「ユーザーの短期意図」「次に試したいこと」程度に絞り、
embedding / dedupe / tombstone / persona snapshot 昇格 / task scheduling は行わない。
prompt では `SHORT WORKING MEMORY` と明示し、確定事実ではなく最近の作業メモとして
必要な時だけ使うように渡す。

UI は `/ws` の server event を表示する monitor panel に限定する。
STT partial/final、reply stream、ContextSnapshot summary、short memory snapshot/extraction status を
表示するが、client 側で状態判断や retry 判断はしない。

### 確定した判断: short memory extraction は LLM lane と heuristic fallback の二段にする
2026-05-29 の follow-up では、short memory extraction を heuristic-only から任意の LLM structured output lane へ拡張した。
`InferenceRouter` に `memory_extraction` role を追加し、現行 config では `lmstudio_gemma4_e2b` を使う。
会話本体の `lmstudio_gemma4_26b_a4b` と別モデルで並列性を測るため、LM Studio backend の queue key は URL だけではなく model も含める。
これにより同一モデルの同時 request は引き続き抑制しつつ、別モデル lane は別 queue として観測できる。

ただし short memory extraction は引き続き reply 完了後の background task であり、現在ターンの応答 hot path は待たせない。
明らかな挨拶・聞こえる確認・短すぎる発話は heuristic prefilter で LLM に投げず skip し、LLM structured output が失敗した場合は heuristic fallback に戻す。
LLM result は `decision=store|skip`、`reason`、`proposals`、`raw_text` を持ち、保存する場合だけ `ShortMemoryBuffer` に揮発 note として追加する。
DB 永続化、long-term memory 昇格、embedding、dedupe、tombstone、task scheduling は引き続き行わない。

### 確定した判断: short memory LLM extraction 失敗時は raw fallback store しない
2026-05-29 の実サーバー確認で、`gemma-4-e2b` の structured output が JSON parse に失敗した場合、
heuristic fallback がユーザー発話全体を `verbatim` note として保存し、次ターン prompt に
`123って言う数字を覚えてください` のような raw 指示文が載ることを確認した。

これは「LLM は覚えるべき item だけを返し、プログラム側で merge / dedupe / prompt 展開する」という
方針に反するため、LLM extraction backend がある場合の parse / backend failure では
`source=heuristic_fallback` かつ `decision=skip` とし、raw 発話を保存しない。

また conversation prompt で使う formatter は `server/gateway/thinking/short_memory_prompt.py` 側である。
`server/session_short_memory.py` だけを更新しても実際の会話 prompt には反映されないため、
gateway 側 formatter でも `verbatim` note を `Remember verbatim: ...` として展開し、重複を除去する。

### 確定した判断: short memory extraction schema は text/mode だけを LLM に出させる
2026-05-29 の LM Studio 実測では、`remember_items[].confidence` と
`remember_items[].expires_after_turns` を required にした JSON schema は E2B / E4B / 26B のいずれでも
途中で空白を吐き続けるなど不安定だった。
一方、schema を `text` と `mode` だけに簡略化すると、E2B/E4B でも structured output が安定した。

そのため short memory extraction は structured output を維持しつつ、LLM の責務を
「覚えるべき item の text と mode だけを返す」ことに限定する。
`confidence` はサーバー側で 0.85 に補完し、TTL は `ShortMemoryBuffer` の default を使う。
merge / dedupe / prompt 展開も引き続きプログラム側で行う。

また extraction input に Tomoko reply を含めると、recall 質問への回答から新規 memory を作る誤 store が起きた。
そのため LLM extraction には latest user transcript だけを渡し、recall / answer request / hearing check は
deterministic guard で LLM に渡す前に skip する。

現行 config では memory extraction backend を `lmstudio_gemma4_e4b` にする。
TomoroSession の音声なし simulation では、数字 `123` と作業文脈では short memory 有効時だけ回答できた。
ただし英字 `ABC` は `Remember verbatim: ABC` が prompt に入っても 26B reply が空文字になったため、
英字 verbatim 再現は追加調整が必要である。

### 気づき: short memory hint だけでは task ledger としては不安定
2026-05-29 の音声なし TomoroSession simulation で、口頭タスク更新シナリオを試した。
シナリオは `ログ確認、UI確認、テスト実行` の 3 タスクを登録し、
`ログ確認は終わった`、`UI確認も終わった`、`ブラウザ確認を追加して` と続け、
最後に `今残っているタスクを短く教えて` と聞くもの。

short memory cue に `タスク` / `終わった` / `完了` / `追加` を足し、
TTL default を 5 turn にすると、初期リスト、完了2件、追加1件はすべて memory note として prompt に入った。
しかし 26B の最終回答は `ブラウザ確認だけ` となり、未完了の `テスト実行` を落とした。
途中 turn では `残るはテスト実行だけかな？` と言えていたため、複数の自然文 working_context note を
prompt hint として渡すだけでは、task ledger の再構成が安定しない。

この結果から、short memory は秘書感の hint には効くが、完了/追加/残タスクを正確に扱うには
task-specific structured note か deterministic reducer が必要である。
これは reminder / scheduler 実装ではなく、揮発 short memory 内の task-like working context 整形として
別途小さく扱うのがよい。

### 確定した判断: short memory extraction backend は品質優先で 31B を採用する
2026-05-29 の follow-up simulation で、口頭タスク更新シナリオを E4B extraction と
31B extraction で比較した。
シナリオは `ログ確認、UI確認、テスト実行` を初期タスクとして登録し、
`ログ確認は終わった`、`UI確認も終わった`、`ブラウザ確認を追加して` の後に、
`今残っているタスクを短く教えて` と聞くもの。

E4B extraction は short memory note をすべて prompt に載せたが、最終回答で
`テスト実行` を落とし `ブラウザ確認だけ` と答えた。
31B extraction は `タスク：ログ確認、UI確認、テスト実行`、`ログ確認は終わった`、
`UI確認も終わった`、`ブラウザ確認を追加` のように後段 26B が扱いやすい note に整理し、
3 回連続で `テスト実行` と `ブラウザ確認` の両方を残タスクとして答えた。

そのため現行 config では `memory_extraction_backend` を `lmstudio_gemma4_31b` にする。
ただし 31B extraction は warm 後でも 1.8〜3.2 秒程度かかるため、background task として扱い、
現在ターンの応答 hot path は待たせない。
短い連続発話で次ターンに間に合わない場合は、その次以降の prompt hint として扱う。
E4B backend は速度比較や fallback 候補として設定に残す。

### 気づき: Apple Speech は wake word だけを落とす/崩すことがある
2026-05-30 に `logs/server-debug.log` を確認したところ、Apple Speech active 時に
「ともこ聞こえますか」系と思われる実発話で、wake word 部分だけが落ちたり崩れたりするパターンが複数あった。

確認できた典型例は、`智子聞こえますか` / `智子聞こえる` のように `智子` が出た場合は
`WakeWordJudge` が `wake_word_detected` として参加できる一方で、近接する試行では
`聞こえますか` / `聞こえてますか` / `聞こえてる` だけが transcript になり、
ambient では wake word 不在として扱われるものだった。
また `朝子聞こえてますか`、`大聞こえてますか`、`どう子聞こえてますか` のように
語頭だけが別語へ崩れる例もあった。

このため、現象は ParticipationJudge が `智子` を拾えない問題ではなく、
STT が短い呼びかけ語を欠落/誤認識した後に、transcript filter や wake word 判定で自然に落ちる問題として扱う。
Apple Speech sidecar に contextual strings / custom language model を与えること、
もしくは `朝子` / `どう子` などの実ログ由来 alias を限定的に wake word 候補へ足すことを次の検討候補にする。

### 確定した判断: Apple Speech は contextualStrings で Tomoko 語彙を補助する
2026-05-30 に Apple Speech sidecar へ `--contextual-string` を追加し、
Python の `AppleSpeechSTT` wrapper から `ともこ` / `トモコ` / `Tomoko` / `智子` / `朋子` / `tomoko` を渡すことにした。

これは ParticipationJudge の alias を広げる前に、STT が wake word 自体を落とす/崩す問題を
認識リクエスト側で補助する最小変更である。
`朝子` / `どう子` / `大` のような実ログ由来 alias を wake word 判定に足すかどうかは、
contextualStrings 適用後の実ブラウザログを見てから判断する。

### 確定した判断: persona updater は diff-only output と deterministic merge にする
2026-05-30 に persona updater の LLM 出力を full snapshot 生成から diff-only 生成へ変更した。
31B structured output でも、previous snapshot 全体を input に入れ、さらに full snapshot 全体を
output させる構造は、人格 snapshot が育つほど JSON truncation / parse failure を再発させる。

そのため LLM は `lexicon_diff_json` / `state_diff_json` だけを返す。
previous snapshot は code 側で salience と件数上限に基づく compact prompt slice に圧縮し、
evidence や低 salience item は prompt へ入れない。
返ってきた diff は code 側で full snapshot に merge し、user terms / phrases / relationship markers /
corrections / open threads などを上限つきで prune する。
これは「落とすもの」や snapshot 全体の整合性を LLM に委ねないための境界である。

`persona-updater-once` は歴史順に session ごとに人格 snapshot を逐次更新する batch なので、
1 回の make では `PERSONA_UPDATE_LIMIT ?= 1` を基本にする。
大量に進める場合だけ人間が明示的に `PERSONA_UPDATE_LIMIT=N` を指定する。
以前の `PERSONA_UPDATE_MAX_TOKENS = 1600` 方針は、full snapshot output 前提だったため否定する。
diff-only output では `PERSONA_UPDATE_MAX_TOKENS = 4096` とし、さらに schema の `maxItems=6` で
各 diff 配列の伸びを抑える。

### 確定した判断: world observation normalizer は raw 保存を優先し deterministic fallback を持つ
2026-05-30 の外部観測収集で、Perplexity 由来の `2026-05-30-world-observation.md` は
strict Markdown validator を通ったが、ingest 中の LLM normalizer で失敗した。

最初の失敗は `lmstudio_gemma4_26b_a4b` の context length 超過だった。
LM Studio は SSE で `event: error` と `data: {"error": ...}` を返していたが、
parser が error payload を空 delta と同じ扱いにしていたため、表面上は
`chunk_count=0` と `JSONDecodeError` に見えていた。
LM Studio SSE parser は error payload を `RuntimeError` として扱う。

world observation normalizer は会話 hot path ではなく background ingest なので、
短い candidate generation lane ではなく `memory_extraction` lane を使う。
現行 config では `lmstudio_gemma4_31b` で、長い観測 Markdown の context を扱える。

ただし 31B でも raw Markdown 全体を一括 JSON 化すると生成が長くなり、
JSON truncation / parse failure が起きる。
そのため LLM normalizer は代表 item 最大 8 件・backend timeout 45s・retry なしを基本にし、
失敗時は Markdown の `## topic` / `### title` / 本文 excerpt から deterministic fallback item を作る。
raw Markdown は DB に保存されるため、fallback item は原本の代替ではなく traceable entry point として扱う。

将来品質を上げる場合は、raw Markdown を勝手に要約・改変するのではなく、
section 単位 chunking で normalizer に渡す。

### 確定した判断: MaAI react 相槌の本番閾値は 0.45 にする
2026-05-30 に、以前の `p_bc_react >= 0.68` 方針を否定し、本番側の react 相槌閾値を
`p_bc_react >= 0.45` に下げた。

MaAI adapter の suggestion 発火 default と TomoroSession の release gate は同じ 0.45 に揃える。
`TOMOKO_MAAI_REACT_THRESHOLD` が指定されている場合は従来どおり env override を優先する。

この変更は「弱めの react cue でも候補にする」ためのものであり、暴発抑制のための
Tomoko idle gate、user speaking gate、同一 user speech segment 1 回制限、global cooldown 2000ms は維持する。
`p_bc_emo` は引き続き suggestion JSON の観測対象であり、LLM なし release 対象にはしない。

### 確定した判断: MaAI 相槌は ambient の参加判定前には release しない
2026-05-31 の実サーバー確認で、`TOMOKO_MAAI_BACKCHANNEL_ENABLED=1` にしたところ、
`attention_mode=ambient` の user speech に MaAI react 相槌が先に鳴り、
その直後の wake word transcript が Tomoko playback 中の barge-in / echo 系として扱われる挙動を確認した。

これは相槌を「会話中の gesture audio」として扱う設計に反する。
MaAI 相槌は、Tomoko が既に会話に参加している `engaged` / `cooldown` 系の文脈でだけ release し、
`ambient` では `backchannel_skipped reason=attention_not_engaged` として捨てる。

この gate は wake word / ParticipationJudge の前段を MaAI が横取りしないためのもの。
`state=listening`、Tomoko idle、segment 1 回制限、cooldown だけでは足りない。

### 確定した判断: engaged 中の短い未完 follow-up fragment は通常 reply を開始しない
2026-05-31 の実サーバー確認では、MaAI 相槌を挟んだ会話中に
`相槌の` / `相槌のタイミングで` のような短い STT 断片が
`attention_engaged_followup` として invite され、通常 LLM reply が開始されていた。

これは MaAI 相槌の release gate ではなく、engaged follow-up の参加判定が
「未完の短い断片」を通常 turn として強く扱いすぎていた問題である。
Wake word がない engaged / cooldown follow-up でも、12 文字以下で
`の` / `で` / `を` / `が` / `に` / `と` / `は` / `も` のような助詞で終わるものは
`low_confidence_followup` として observer に落とし、conversation log や通常 reply を開始しない。

`さっきの続きなんだけど` のような短いが意図として成立する follow-up は維持するため、
`けど` / `から` / `って` / `とか` は今回の fragment 終端には含めない。

### 確定した判断: MaAI 相槌の playback telemetry は通常 echo 判定に混ぜない
2026-05-31 の実サーバー確認で、MaAI 相槌 `うん` の再生 telemetry が
`turn_id=None` として届き、その直後の user transcript が Tomoko playback の
echo grace / active chunk として observer に落ちる挙動を確認した。

MaAI 相槌は通常 reply ではなく gesture audio なので、`turn_id=None` の
`playback_started` / `playback_ended` telemetry は `AudioTurnController` の
通常 playback state / echo grace に反映しない。
ログや外部イベントとして観測されることは許すが、turn-taking / barge-in / echo 判定の
authoritative state には混ぜない。

これにより、相槌直後の user 発話が `playback_ended_grace` や
`playback_active_chunk` だけを理由に落ちる経路を塞ぐ。

### 確定した判断: engaged 中の長い未完 continuation tail も通常 reply を開始しない
2026-05-31 の実ログでは、`...そういうのって` で STT segment が一度切れ、
`attention_engaged_followup` として通常 LLM reply が開始された。
その後にユーザーが続きを話していたため、Tomoko が遮る形になった。

以前の「`って` / `とか` は短い未完 fragment 終端には含めない」という判断は、
短文 fragment guard に限って維持する。
一方で、長い発話が terminal punctuation なしで
`って` / `とか` / `みたいな` / `という` / `というか` で終わる場合は、
継続中の文として `low_confidence_followup` に落とす。

これは LLM なしで発話継続らしさを保守的に扱う gate であり、
`。` / `？` / `!` などの終端記号がある場合は通常 follow-up として扱う。

### 確定した判断: 相槌は同一 speech segment でも cooldown 後なら複数回出す
2026-05-31 の実ブラウザ確認では、相槌は鳴るようになったが長い user speech segment 中に少なすぎた。
原因は TomoroSession 側の `already_released_in_speech_segment` gate が、
同一 user speech segment 内の 2 回目以降の MaAI react 相槌を止めていたことだった。

以前の「同じ user speech segment 内でまだ相槌していない」という release 条件は否定する。
相槌は通常 reply ではなく gesture audio なので、長い発話中には複数回出てよい。
暴発抑制は Tomoko idle gate / user speaking gate / attention engaged gate / global cooldown で行う。

TomoroSession 側の global cooldown は 2000ms から 1500ms に短縮する。
MaAI adapter 側の suggestion cooldown 900ms は維持し、実ログでまだ少ない場合に次の調整対象にする。

### 確定した判断: VAD speech_end 後の未完 transcript は participation gate で聞き続ける
2026-05-31 の実ログでは、`...よくさぁ` や `...関係が` のような未完発話が
`attention_engaged_followup` として通常 reply を開始していた。
この問題は VAD silence threshold を単純に伸ばすだけでは、レイテンシーと切り分けの副作用が大きい。

まずは speech_end 後の transcript を participation gate で分類し、
長い発話でも `さぁ` や助詞 `が` で終わる場合は `low_confidence_followup` として observer に落とす。
これは発話を「無視する」ためではなく、通常 reply を開始せずに聞き続けるための gate である。

将来、未完 fragment の内容を次の transcript と統合して LLM に渡す必要が出たら、
observer に落とすだけではなく、TomoroSession に pending user utterance buffer を持たせる Phase を切る。

### 確定した判断: MaAI gesture audio は AudioTurnController の speaking state を立てない
2026-05-31 の実ログでは、MaAI 相槌 `なるほど` の直後に user transcript が finalized され、
`playback_state=speaking` として turn-taking / barge-in 判定に入っていた。
原因は `_release_backchannel_audio()` が通常 reply と同じ `audio_turns.begin_turn()` /
`reserve_audio_chunk()` 経路を使っていたことだった。

以前の「`turn_id=None` playback telemetry は echo state に混ぜない」だけでは不十分だった。
サーバー内部でも、相槌は Tomoko の通常発話ではなく gesture audio として扱う。
MaAI backchannel release では audio turn を begin せず、`AudioTurnController` の
`is_tomoko_speaking()` / `recent_tomoko_text` / playback state / reply output latency state を進めない。

通常 reply、stop ack、pregenerated candidate は引き続き audio turn を使う。
この分離により、相槌が user turn の終端や通常 reply start の trigger になる経路を塞ぐ。

### 確定した判断: MaAI backchannel release は TomoroSession の外で行う
2026-05-31 の会話確認では、相槌を TomoroSession 内部の `backchannel_suggested` event /
`release_backchannel_audio` command として扱う限り、通常会話 state と gesture audio の境界が曖昧になった。

以前の「TomoroSession 内で MaAI suggestion を reduce して release する」設計を否定する。
MaAI 相槌は会話 turn ではなく server-owned gesture audio lane であり、
TomoroSession は `get_now_state()` の read-only snapshot を提供するだけにする。

release gate は `GestureAudioEmitter` が snapshot の `attention_mode` / `vad_state` /
`playback_state` と cooldown / TTS availability から判定する。
audio は既存 `/ws` の binary send を使うが、`audio_start` / `audio_end` / `SessionCommand` /
`AudioTurnController` / reply output latency state は通さない。

TomoroSession から `backchannel_suggested` reduce、`apply_backchannel_suggestion()`、
`release_backchannel_audio` command は削除する。
通常 reply 用の `_send_audio_chunk()` / `_flush_tts_text()` は無害化オプションを持たない形に戻し、
gesture audio の例外は gateway 側に閉じ込める。

### 確定した判断: 短い時刻質問は低音量でも transcript filter で落とさない
2026-05-31 の実ログでは、`今何時` は STT で `transcript text='今何時'` まで出ていたが、
`TranscriptFilter` が `low_audio_short_text` として drop していた。
同じ会話で長めの `俺今何時とかっていうのは反応できひんのかな` は accept され、
prompt の current local time から Tomoko が `深夜の1時32分` と答えられていた。

つまり問題は LLM や clock prompt ではなく、短い低音量発話を hallucination 対策で落とす filter の例外不足だった。
`今何時` / `いま何時` / `何時ぐらい` / `時刻` などの clock query は、
Tomoko が system prompt の current local time で即答できる実用 command なので、
低音量短文 filter より前に accept する。

### 確定した判断: user speech が再開したら未出力 reply は stale cancel する
2026-05-31 の実ログでは、長い user 発話の VAD speech_end 後に通常 reply task が起動した直後、
user が話し続けて `state changed to listening` へ戻っていた。
それでも reply output がまだ始まっていない task が cancel されず、Tomoko が発話を始めて遮る形になった。

これは VAD が一度 speech_end を出したこと自体は自然な挙動だが、その後の resumed listening を
stale reply cancel の signal として使えていなかった問題である。
`listening` へ遷移した時点で、reply generation が active かつ reply output が未開始なら
`resumed_user_speech_before_output` として cancel する。
空 transcript を待ってから判断する方針は、今回の実会話では遅すぎるため否定する。

### 確定した判断: output lane と closed-loop floor ownership を分けて読む
2026-05-31 時点では、TomoroSession の出力を「人間 transcript に対する通常 reply」と暗黙に同一視しない。
MaAI 相槌と将来の Tomoko 割り込み発話を考えると、音声を鳴らすこと、会話 turn を開始すること、
conversation log に保存すること、次の入力判定で echo / barge-in / turn-taking の材料にすることは別の責務である。

`OutputLane` は `reply_turn` / `initiative_turn` / `gesture_audio` / `stop_ack` / `interrupting_turn` として読む。
`reply_turn` / `initiative_turn` / `interrupting_turn` は conversation log 対象、
`gesture_audio` と `stop_ack` は conversation log 対象外にする。
`AudioTurnController` が扱うのは turn audio だけであり、`gesture_audio` は明示的に拒否する。
MaAI 相槌は `GestureAudioEmitter` の `gesture_audio` lane に留め、通常 turn の
audio_start / audio_end / playback_state / echo grace / conversation log へ混ぜない。

closed-loop の観点では、各 lane が「入力を受ける / 床を取る / 出力する / 出力結果が次の入力判定へ戻る」
のどこへ接続されるかを固定する。
現行 candidate / arrival gate は `initiative_turn` の `ambient_idle` floor policy であり、
人間が話している最中に Tomoko が床を取る `interrupting_turn` の実装は別 Phase とする。

### 確定した判断: world observation integration は共有DBの topN に依存しない
2026-05-31 の `tests/integration/test_phase180_world_observations_db.py` では、
`fetch_candidate_interpretations(limit=10)` の global topN に fixture interpretation が入ることを期待していた。
共有DBに既存 candidate が多い場合、この assertion は実装破損ではなく DB 状態で落ちる。

`PostgresWorldObservationStore` へ connection / transaction を外部注入する設計は、
現時点ではテスト都合の影響が大きいため採用しない。
integration test は `try/finally` で checksum fixture を必ず cleanup し、
自分が作った `item_id` / `interpretation_id` が `world_observation_trace` に見えることを
直接 DB query で確認する。
store の global fetch order / limit は、共有DB fixture の存在を前提にした assertion へ使わない。

### 確定した判断: GPU pressure は mactop headless を optional provider として読む
2026-05-31 時点では、GPU 使用率を別 terminal で人間が眺めるだけにしない。
Tomoko の会話 latency / backend trace / server-debug log と同じ観測面へ、
Apple Silicon の GPU active / power / frequency / memory / thermal sample を取り込む。

mactop は v2 で `--headless --count` JSON を出せるため、Tomoko 側へ private IOReport 実装を移植しない。
`_tools/system_metrics.py` が mactop を optional external command として呼び、
`logs/system-metrics.jsonl` に normalized JSONL を追記する。
`make monitor` はこの JSONL の最新 sample を read-only snapshot として表示する。

mactop 未インストール、timeout、parse failure は runtime failure ではなく、
`available=false` の sample として記録する。
GPU 観測は会話 hot path / TomoroSession state / backend routing の authoritative input にはしない。

2026-05-31 の実測では `mactop --headless --count 1 --interval 2000` が約 6.9 秒かかり、
初期実装の timeout 5 秒では `available=false timeout_after_sec:5.0` が連続した。
mactop headless は interval だけでなく起動と初回 sample に余裕が必要なため、
timeout は `max(10s, interval_sec + 8s)` にする。
monitor dashboard は最新行が timeout でも、直近に available sample があればその値を表示する。

### 確定した判断: MaAI react 相槌の本番閾値は 0.50 に少し戻す
2026-05-31 の実ブラウザ会話では、MaAI 相槌が `gesture_audio` lane として自然に入り、
通常 turn / playback state を汚さない状態になった。
一方で長い発話中に `turn_id=None` の相槌 playback が複数回入り、体感として少し多い。

以前の「本番 react threshold は 0.45」という判断は、相槌が少なすぎた段階の補正としては有効だったが、
現状では少し低すぎるため否定する。
MaAI adapter の suggestion 発火 default、env 未指定時の `TOMOKO_MAAI_REACT_THRESHOLD` default、
`GestureAudioEmitter` の release gate default は `0.50` に揃える。

cooldown は 1500ms、MaAI adapter 側 suggestion cooldown は 900ms のまま維持する。
今回の調整は弱めの react cue だけを落とす小幅変更であり、
gesture audio lane / output lane / floor ownership / AudioTurnController 分離は変更しない。

### 確定した判断: Perplexity browser automation は Tomoko 隣の別 repo に隔離する
2026-05-31 時点では、ログイン済み Chrome / Perplexity UI を CDP で操作する research operator を
Tomoko 本体へ入れない。
`/Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator` を別 git project として作り、
Tomoko からは MCP 風の外部 capability として呼ぶ。

operator は Perplexity UI 操作、raw artifact 保存、structured `ResearchResult` 返却だけを担当する。
Tomoko 側は rule-based intent detection、operator call、result validation、Tomoko DB insert、
`ResearchResultReady` event、通知/回答発話タイミングを担当する。

`chatgpt-el` は CDP workflow の参考にはするが、GPLv3-or-later source を Tomoko や operator へコピーしない。
selector や completion heuristic の考え方を読み、自前実装として小さく作る。

### 確定した判断: Research MCP は command runner 経由で TomoroSession に戻す
2026-05-31 時点では、Tomoko の会話 hot path で `tomoko-research-mcp` の完了を同期待ちしない。
`ResearchIntentDetector` が rule-based に `ResearchRequest` を作り、
`TomoroSession` は `research_requested` event から `submit_research_request` command を出す。
`ResearchCommandRunner` が MCP subprocess を呼び、`ResearchResult` を
`research_result_ready` event として TomoroSession に戻す。

MCP response は `content[0].text` ではなく `structuredContent` を正とし、
Tomoko 側で citation URL dedupe、status 分離、speakable 判定を行う。
`completed` かつ `short_answer` がある場合だけ通知文は `調べ終わったよ。聞く？` にする。
`failed` / `timeout` / `needs_human` は成功と混ぜず、`調べきれなかったみたい。` として扱う。

この初段では DB 永続化、「教えて」で本文を読む処理、ContextSnapshotBuilder への接続、
conversation prompt への research result 直入れは行わない。

### 確定した判断: Research MCP smoke は deterministic subprocess を標準にする
2026-05-31 時点では、実 Perplexity / Chrome 操作はログイン済み browser state と外部 UI timing に依存する。
Tomoko 側の e2e/smoke では、まず fake MCP subprocess を起動して JSON-RPC `tools/call`、
structured result parse、TomoroSession emission までを固定する。

実 operator は同じ smoke script に `--command` を渡して任意確認する。

### 確定した判断: Research answer follow-up は LLM ではなく pending result を読む
2026-05-31 時点では、`research_result_ready` が speakable な場合、TomoroSession が
pending research result として保持する。
その直後の「教えて」「聞かせて」「結果を教えて」「はい、お願い」系 transcript は、
通常 LLM reply へ流さず、filter 後・turn-taking / participation 前に
`research_answer_requested` として処理する。

`research_answer_requested` は pending result の `short_answer` を
`start_research_answer_reply` command に載せ、`start_precomputed_reply()` から
`reply_text` / TTS / `reply_done` へ流す。
pending result は一度読んだら消費し、同じ result を二重に読まない。

この Phase では DB 永続化と、最初の「調べて」発話を通常 transcript path から
`research_requested` へ自動接続する処理はまだ行わない。

### 確定した判断: Research result は再読できる短命 cache として扱う
2026-05-31 時点では、Research answer follow-up を一度だけで消費する方針を否定する。
`research_result_ready` の speakable result は TomoroSession 内の短命 latest research cache として残し、
「教えて」だけでなく、pending result の query と重なる
「OpenAIについて知ってることある？」のような follow-up でも `short_answer` を返す。

query overlap がない「知ってることある？」系発話では pending result を誤用しない。
将来 DB 永続化する Phase では、research result を保存する時に embedding も生成し、
同一セッション外でも安価に取り出せる索引として扱う。

### 確定した判断: Research result は LLM summary を deep context に混ぜる
2026-05-31 時点では、Research result の raw `short_answer` をそのまま deep context に混ぜない。
MCP result 取り込み時に LLM summary を作り、その summary を embedding して保存する。
`ContextSnapshotBuilder(depth="deep")` / `reflective` は research result store を optional source として検索し、
prompt には `RESEARCH CONTEXT` として summary だけを渡す。

fast / normal では research result source を読まない。
Research result は会話記憶ではなく外部調査メモなので、provider / fetched_at / citations を保持し、
必要な時だけ参照する。
