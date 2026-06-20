# tomoko v2

v1で得た知見を元にv2として作り直す
v1のソース、テスト、probe、メモリ、ログ、アーキテクチャはv1ディレクトリにある。
v2のメインは任意タイミングの発話体験の発掘である

- ターンの概念を持たない
  常に聞いて任意のタイミングで答えるcool downの概念を持たない
- closed-loopを行わずプロセス分離で整理する
- postgresへのLISTEN/NOTIFYで情報を共有する
- v1とはdbスキーマを変える
- GPUが全く足りなくなるまで推論のqueシステムは導入しない
- 構造が安定するまでタスク機能は捨ておく

会話にセッションIDを付与するタイミングについては、無音期間ができた、というのを元にtomoko-processで発行してDBにSTT結果を入れるタイミングで行う

## プロセス

db接続をきちんとプールしておくこと。そこで時間の浪費を行う意味がない。
モデルはプリロードしてwarm-upしておくこと。そこで時間の浪費を行う意味がない。

### hot-path-process

tomoro-processに対して透過的なprocessである。

- 音声シグナルはv1と同様にdtoを使わない
- uiは従前のものを再利用する
- gateは持たない
- STT apple(差し替え可能な形)
- メイン会話LLM
- メインTTSはvoicevox(2倍速(今は応答が遅い))
- STTのパーシャルについてはいつ内容を捨ても良いというSTT finalとは別テーブルに乗せてtomoko-processにnotifyする。 finalを入れるdb作業についてはtomoko-processの方で責任を持つ

### tomoko-process
人格の確信 v1のtomoro status相当

- finalのSTT結果についてすぐにfinal用のdbのテーブルにinsertする。発話反応自体はhot-pathから出てくる
- promptを作り hot-path-processにNOTIFYする
- 各種コンテキストをLISTEN/pollingしてpromptに流し込む
- 無音時に話し始めるなどの判断も計算モデルによって行う
  計算モデル自体は別クラスで差し替え可能な形で実装しておく
  LLMは使わない
- 意味の飽和度とか話して良いか、の最終判断を行う(v1のgate相当)

#### カレンダー情報
1minに一回DBから構造化された情報をメモリにDTOのリスト/マップとして持っておき必要なタイミングでプロンプトに入れる 例 {"2026-06-16 13:00" : xxxxxの会議}みたいな構造にしておき現在時刻+n分とかで軽量にクエリする

#### <当面は実装しない>think由来のcandidate
candidateを使いpromptへ投げ込む文字列を取り出して埋め込む

#### user-status-aquire-process由来のstatus情報をpromptに投げ込んだりユーザーの存在、何しているのかを元に計算モデルでの発話判断計算を行う

### think-process

candidateに積む責務のあるプロセス
会話中に思い出したりちょっと考えないと、ということを考える

- 過去の会話サマリーや会話セッションのembeddingによって結びつけてrememberしてcandidateに積む機能
- dbにあるinfo-aquire-process経由の情報と会話セッションをembeddingによって結びつけてcandidateに積む機能
- 任意の調査タスクを発行しinfo-aquire-processにNOTIFYし、調査完了後candidateに積む

### info-aquire-process
world information/google calendarを取得しdbに積む
thinkerから調査依頼があればworld information系のchrome perplexity経由での調査を行い終わればdbに積む

### user-status-aquire-process
screen shotとユーザーのPC前にいる、いないの2値を取得しつづける
ocrで画面の文字を拾って何をしているかを推測するocrの結果自体をそのままdbに突っ込んでsummay-processにNOTIFYする 1分に1回
screenショットからyoutubeを見ているかどうかについては感知しない、ocrで拾える文字情報からビデオを見ていることをしる。(推論能力が足りない)

### summary-process
会話を要約してembeddingしてdbに一時まとめをおく
サマリは キーワードと結論の1文
ex) ユーザーはDDDに懐疑的である
など。

## モデル
LLM推論は全て構造化出力を行い、
LLMに要求するスキーマはできるだけ簡素にする。複雑なスキーマを要求しない
スキーマは定数的に別のpythonに一覧できるように持っておく

### メイン会話LLM
ここはKVキャッシュが聞くはず
gemmma 4 26b mlx + dflash + MTP draft付き
### サマリLLM
ここはKVキャッシュを期待しない。MTP機能の効果だけを期待する
gemmma 4 31b mlx + dflash + MTP draft付き
### TTS はvoicevox
### STT はapple
### ocrはできればapple(vision flamework)
### ユーザーのPC前にいる、いないはE2Bでの構造化出力(いる、いないの2値出力)

## 2026-06-18 追記: hot-path は人格の物理インターフェースである

上の設計における「hot-path-process は tomoko-process に対して透過的である」という説明を、
v2 の発話体験に合わせて次のように具体化する。

hot-path-process は Tomoko の物理インターフェースである。
耳として mic / VAD / STT を扱い、口として TTS / audio chunk / playback を扱う。

tomoko-process は人格・意志・文脈・記憶・発話判断・発話テキスト生成を所有する。
hot-path-process は人格判断を持たない。

hot-path-process が持ってよい状態は、身体制御として必要な短命状態だけである。

- mic / VAD / STT の短命状態
- 現在発話中の speech-order
- append queue
- TTS chunk queue
- playback generation
- replace / stop / fade / short silence の制御状態

hot-path-process が持ってはいけない状態は、人格や会話判断に属する状態である。

- この発話が妥当か
- ユーザーに返答すべきか
- 自発発話すべきか
- candidate を採用すべきか
- 人格・記憶・長期文脈

## 2026-06-18 追記: LLM は tomoko-process が所有し、hot-path は speech-order を実行する

メイン会話 LLM は hot-path-process ではなく tomoko-process 側に寄せる。

hot-path-process は音声入出力の低レイテンシ実行機であり、
STT observation を tomoko-process に流し、
tomoko-process から届く speech-order を TTS/audio として物理的に実行する。

tomoko-process は以下を所有する。

- partial / final STT observation の解釈
- 意味飽和度を含む発話判断計算モデル
- 自発発話・応答・割り込み・言い直しの統合判断
- speech queue / stack / priority の管理
- メイン会話 LLM による発話テキスト生成
- calendar / candidate / user status / memory を使った発話内容の決定

hot-path-process は以下を所有する。

- WebSocket
- mic bytes の受信
- VAD / STT
- STT observation の送出
- speech-order の受信
- TTS
- audio chunk の送出
- speech-order mode に基づく replace / append / stop

つまり hot-path-process が受け取るのは llm-order ではなく speech-order である。
LLM を使ったかどうかは tomoko-process の内部事情であり、
hot-path-process にとっての契約は「この text をこの mode で声にする」ことである。

## 2026-06-18 追記: speech-order と latest-wins audio execution

tomoko-process は任意のタイミングで speech-order を発行してよい。
ユーザー発話への応答、自発発話、カレンダー通知、candidate 由来の発話、言い直し、停止は
すべて speech-order として扱う。

speech-order の最小契約は次の通りである。

```text
speech_order:
  id
  text
  mode: replace_current | append_after_current | stop
```

必要になった場合だけ、以下を追加する。

```text
  reason
  priority
  supersedes_order_id
  created_at
```

hot-path-process は mode に従って物理実行する。

```text
replace_current:
  現在の TTS / audio chunk を捨てる
  必要なら短い無音または fade を挟む
  新しい text の TTS chunk を流す

append_after_current:
  現在の speech-order が終わった後に次の text を流す
  すでに append queue があれば順番に入れる

stop:
  現在の TTS / audio を止める
  append queue も消す
```

hot-path-process は発話してよいかを判断しない。
古い order の chunk が遅れて届いた場合は request id / generation を見て捨てる。
新しい order が来た場合は latest-wins とし、必要なら音声バイトレベルで差し替える。

これにより、Tomoko が話している途中でユーザーが覆い被さって話した場合も、
tomoko-process が新しい speech-order または stop order を出せば、
hot-path-process は古い音声を捨てて新しい音声へ切り替えられる。

## 2026-06-18 追記: 「いきなり話し始める」は特殊機能ではない

Tomoko がいきなり話し始めることは、特別な経路では扱わない。

tomoko-process の発話判断計算モデルが、
user status / calendar / candidate pressure / curiosity / memory / silence / presence を入力として
「今話したい」と判断したら speech-order を出す。

ユーザー発話への応答も、自発発話も、カレンダー通知も、同じ speech-order に落とす。

例:

```text
ユーザー発話への応答:
  partial/final STT -> 意味飽和度 -> 文脈 -> LLM -> speech-order

いきなり話し始める:
  calendar/candidate/user status -> 発話圧 -> LLM -> speech-order

応答の直後に別件を続ける:
  user reply speech-order
  calendar reminder speech-order(mode=append_after_current)

言い直す:
  new STT observation / stop / semantic change -> new speech-order(mode=replace_current)
```

したがって、いきなり話し始める系とユーザー発話への応答は
tomoko-process 内の同じ計算モデルと speech queue で吸収する。

## 2026-06-18 追記: 意味飽和度はアクセル、上書きはブレーキ

v2 では STT final を待つことを絶対条件にしない。
partial STT から意味飽和度が高いと判断したら、
tomoko-process は発話テキスト生成と speech-order 発行まで進んでよい。

ただし、間違っていた場合に備えて常に上書き可能にする。

```text
意味飽和度はアクセル。
新しい observation / stop / user speaking / 意味変化による speech-order 上書きはブレーキ。
final STT は発話開始の検問ではなく、後から来る整合性チェック材料。
```

意味飽和度を LLM で推定する場合、出力はまず saturation のみにする。
構造化出力を複雑にしない。

```text
SemanticSaturationJudge output:
  SATURATION=0.0..1.0
```

remaining_info_risk / semantic_split_risk / safe_response_level のような多項目出力は初期実装では要求しない。
それらが必要になった場合も、まずは deterministic な stale / replace 条件として扱う。

stale / replace の代表条件:

- 新しい partial が stable prefix から大きく外れた
- 「ただ」「でも」「いや」「というか」「一個だけ」などで意味が後から変わった
- user_speaking が true になった
- stop intent が出た
- final transcript が発話根拠と大きくずれた

この方針では、tomoko-process に provisional / speculative という概念を必ずしも置かない。
tomoko-process はその時点の最善判断として speech-order を出す。
後からより新しい事実が来たら、別の speech-order で上書きする。

## 2026-06-18 追記: 発話判断計算モデルは scheduler である

発話判断計算モデルは、単に speak / not speak を返す分類器ではない。
複数の発話候補を enqueue / replace / append / suppress / stop する scheduler である。

入力:

- partial / final STT observation
- semantic saturation
- silence / VAD / VAP / p_yielding
- current speech-order state
- user speaking / user presence
- calendar urgency
- candidate pressure
- curiosity pressure
- recent rejection / fatigue
- playback state

出力:

```text
enqueue              # 通常の待ち行列に積む
replace_current      # 今の発話を止めて差し替える
append_after_current # 今の発話の直後に続ける
suppress             # 候補を捨てる
stop                 # 音声を止める
```

例:

```text
「xxxxだと思うよ」
  source=user_reply
  mode=replace_current

「ところで14時から会議の予定入ってるね」
  source=calendar
  mode=append_after_current
```

この scheduler は tomoko-process に置く。
hot-path-process は scheduler の判断を知らず、speech-order の mode だけを実行する。

## 2026-06-18 追記: 発話判断計算モデルは重み付き pressure model として調整する

発話判断計算モデルは、体験の調律対象である。
そのため、固定ルールだけでなく、重み付きの pressure model として実装し、
Tomoko の話し方を後から微調整できるようにする。

ただし、最初から設定項目を大量に外出ししない。
まずは dataclass の default weight として持ち、ログを見ながら必要なものだけ config 化する。

### 入力状態

計算モデルは、会話 turn ではなく、常に更新される状態を入力にする。

```text
SpeechSchedulerInput:
  partial_stt_text
  final_stt_text
  stable_prefix
  semantic_saturation
  silence_ms
  p_yielding
  user_speaking
  user_present
  tomoko_currently_speaking
  current_speech_order
  candidate_pressure
  calendar_urgency
  curiosity_pressure
  memory_relevance
  recent_rejection_penalty
  fatigue
  stop_intent
```

### 内部 pressure state

Tomoko が「今どれくらい話したいか」は、単発判定ではなく pressure の蓄積と減衰で扱う。

```text
SpeechPressureState:
  reply_pressure
  initiative_pressure
  calendar_pressure
  curiosity_pressure
  followup_pressure
  interruption_penalty
  recent_rejection_penalty
  fatigue
  last_spoke_at
  last_user_spoke_at
```

pressure はイベントで増減し、時間で指数減衰する。

```text
semantic_saturation high
  -> reply_pressure += x

candidate generated
  -> initiative_pressure += priority * urgency

calendar near
  -> calendar_pressure += urgency

user overlaps / stop requested
  -> interruption_penalty += x

user rejects / says "ちゃんと聞いて"
  -> recent_rejection_penalty += x

pressure *= exp(-dt / tau)
```

この形にすると、ユーザー発話への応答、自発発話、カレンダー通知、言い直しを同じ計算モデルで扱える。

### 重み

初期の重みは少数に絞る。

```text
SpeechSchedulerWeights:
  reply_weight
  initiative_weight
  calendar_weight
  curiosity_weight
  memory_weight
  maai_weight
  saturation_weight
  interruption_penalty_weight
  rejection_penalty_weight
  fatigue_weight
```

概念式:

```text
intent_score =
  + reply_weight      * reply_pressure
  + initiative_weight * initiative_pressure
  + calendar_weight   * calendar_pressure
  + curiosity_weight  * curiosity_pressure
  + memory_weight     * memory_relevance
  + saturation_weight * semantic_saturation
  + maai_weight       * p_yielding
  - interruption_penalty_weight * interruption_penalty
  - rejection_penalty_weight    * recent_rejection_penalty
  - fatigue_weight              * fatigue
```

この score は「話すかどうか」だけではなく、どの speech-order action を選ぶかの材料にする。

### action selection

出力は binary な speak / not speak ではない。
speech-order queue に対して何をするかを返す。

```text
SpeechSchedulerOutput:
  action:
    suppress
    enqueue
    append_after_current
    replace_current
    stop

  text_intent:
    reply
    initiative
    calendar_notice
    followup
    correction
    stop

  llm_prompt_basis
  reason
  score
  score_breakdown
```

action の基本規則:

```text
if stop_intent is high:
  action = stop

elif current_speech_order exists and new_score > current_score + replace_margin:
  action = replace_current

elif current_speech_order exists and new_score > append_threshold:
  action = append_after_current

elif current_speech_order does not exist and new_score > speak_threshold:
  action = replace_current

else:
  action = suppress
```

threshold / margin も重みと同様に最初は少数だけ持つ。

```text
SpeechSchedulerThresholds:
  speak_threshold
  append_threshold
  replace_margin
  stop_threshold
```

### 挙動の調整例

重みは Tomoko の振る舞いを調整するノブである。

```text
よく相槌を打つ Tomoko:
  maai_weight high
  saturation_weight high
  speak_threshold low

控えめな Tomoko:
  interruption_penalty_weight high
  speak_threshold high
  replace_margin high

予定をよく思い出させる Tomoko:
  calendar_weight high
  append_threshold low

雑談好きな Tomoko:
  curiosity_weight high
  initiative_weight high

被せ気味に話す Tomoko:
  replace_margin low
  maai_weight high
```

この調整は人格をコードに散らすためではなく、
体験上の不快さを実測しながら少数の重みで補正するために使う。

### LLM との関係

LLM は scheduler の最終判断者ではない。

```text
scheduler decides:
  何を話したいか
  いつ出すか
  replace / append / stop のどれか

LLM generates:
  実際の発話テキスト
```

意味飽和度を LLM で推定する場合も、LLM は saturation を出すだけにする。
speech-order を出すかどうか、どの mode にするかは scheduler が決める。

処理の流れ:

```text
STT / user-status / candidate / calendar
  -> SpeechScheduler
  -> LLM text generation in tomoko-process
  -> speech-order(text, mode)
  -> hot-path TTS/audio
```

### score_breakdown を必ずログに残す

重み付きモデルは、なぜ話したかが追えなければ調整できない。
そのため、scheduler は必ず score_breakdown を structured log と DB に残す。

例:

```json
{
  "action": "append_after_current",
  "text_intent": "calendar_notice",
  "score": 0.73,
  "breakdown": {
    "reply": 0.12,
    "calendar": 0.44,
    "saturation": 0.18,
    "maai": 0.05,
    "interruption_penalty": -0.06
  },
  "reason": "calendar pressure is high enough to append after current reply"
}
```

このログが、後から「なぜ今しゃべったか」「なぜ黙ったか」「なぜ上書きしたか」を説明する材料になる。

## 2026-06-20 追記: 計算モデルは Materials -> Pressures -> Gates として整理する

発話判断計算モデルは、見通しのために次の三語で分ける。

```text
Materials:
  観測された事実、外部情報、人格傾向などの計算材料

Pressures:
  materials から計算された、話したさ・自然さ・出しやすさなどの中間量

Gates:
  pressures と candidate から実行 decision を出す裁定器
```

`input` という語は曖昧なので、raw 音声情報、無音時間、VAP/MaAI、
STT partial/final、外部調査結果、性格傾向は `materials` と呼ぶ。
`silence_ms` や `p_yielding` は特定 pressure model の所有物ではない。
複数の pressure model が同じ material を参照して、それぞれ違う意味の圧力を計算する。

```text
Raw observations / memory / world info / personality
  -> Materials
  -> Pressure models
  -> LlmFireGate
      pressure を合成して LLM に行くかだけを決める
  -> LLM
  -> PreparedSpeechCandidate
  -> SpeechEmissionGate
      今 hot-path に speech-order として送るか
  -> SpeechOrder
  -> hot-path TTS/audio
```

### Materials

Materials はまだ判断ではない。
観測された事実や、別 worker が持ってきた材料を、Tomoko 内で pressure 計算に使える形に整える。

```text
TurnMaterials:
  audio_rms
  silence_ms
  user_speaking
  speech_probability
  playback_active
  p_yielding
  p_bc_react
  p_bc_emo
  stt_partial
  window_ms

WorldMaterials:
  external_result_importance
  memory_relevance
  calendar_urgency
  followup_age_ms
  followup_importance
  curiosity_relevance

PersonalityMaterials:
  talkativeness
  curiosity
  restraint
  empathy
  interrupt_tolerance
```

hot-path から tomoko-process へ渡る 200ms 程度の WebSocket payload は
`TurnMaterials` である。これは durable log ではなく latest-wins の realtime material であり、
DB / NOTIFY に raw MaAI/VAP frame を流さない。

### Pressures

Pressure は materials から計算された中間量である。
gate は raw material を雑に合成せず、原則として pressure を読む。

```text
DialogueTurnPressure:
  semantic saturation
  stable prefix / final STT
  VAP / p_yielding
  user_speaking / silence_ms
  -> reply_readiness
  -> turn_opportunity
  -> interruption_risk

NaturalSpeechPressure:
  MaAI backchannel score
  silence_ms
  personality empathy/restraint
  -> backchannel_desire
  -> light_reaction_desire
  -> filler_desire
  -> clarification_desire
  -> naturalness

MotivationPressure:
  Tomoko の話したい圧
  personality talkativeness/curiosity/restraint
  silence_ms
  -> initiative_desire
  -> personality_push
  -> restraint
  -> interrupt_tolerance

WorldPressure:
  external result / memory / calendar / followup
  silence_ms / p_yielding / user_speaking
  personality curiosity/restraint
  -> importance
  -> urgency
  -> relevance
  -> deliverability
  -> decay
```

例えば `silence_ms` は `DialogueTurnPressure` では「ユーザーが譲ったか」に効き、
`NaturalSpeechPressure` では「間を埋めても自然か」に効き、
`WorldPressure` では「外部情報を今差し込めるか」に効く。
同じ material を複数 pressure が使うが、pressure の意味は混ぜない。

### LlmFireGate

LlmFireGate は、Tomoko 側で LLM を fire して
発話候補を作るかどうかだけを決める gate である。
ここではまだ hot-path に音声を出さない。
また、ここで「main reply」「initiative」「world summary」のような intent 分岐を持たない。
その区別は pressure の内訳として観測されるだけで、gate の責務は
pressure を合成して `fire` / `do_not_fire` / `cancel_or_replace_pending` を返すことに限定する。

主な入力:

```text
TurnMaterials
DialogueTurnPressure
NaturalSpeechPressure
MotivationPressure
WorldPressure
pending_inference
```

主な出力:

```text
do_not_fire
fire
cancel_or_replace_pending
```

この gate の目的は、低レイテンシーのために早く準備を始めることと、
意味が不十分な partial や低重要度の外部情報で重い推論を乱発しないことの両立である。
semantic saturation、無音、外部調査結果、calendar、motivation は
それぞれ pressure として合成 score に効く。
どの pressure が強かったかは `score_breakdown` に残すが、LLM 前に intent enum は作らない。

### SpeechEmissionGate

SpeechEmissionGate は、Tomoko 側に生成済みの発話候補がある時に、
それを今 hot-path 側へ `speech-order` として送出するかを決める gate である。

ここで判断するのは「推論を走らせる価値があるか」ではなく、
「今、物理的に口から出してよいか」である。
したがって candidate priority や motivation は重要な係数だが、最終判断では
ユーザー発話の遮り、勘違いリスク、semantic confidence、freshness、
WorldPressure.deliverability、stop intent と競合させる。

主な入力:

```text
PreparedSpeechCandidate
TurnMaterials
DialogueTurnPressure
NaturalSpeechPressure
MotivationPressure
WorldPressure
current_speech_score
tomoko_currently_speaking
stop_intent
recent_rejection_penalty
fatigue
```

主な出力:

```text
emit_now
append_after_current
replace_current
hold
suppress
stop
```

この gate は「ユーザー発話を遮ってでも、少し勘違いしながら発話するか」を扱う。
motivation が高く、semantic confidence が中程度で、関係性イベントとして許容できる時は
短く撤回可能な割り込みを `replace_current` または `emit_now` へ寄せる。
一方で、ユーザーが強く話している、誤解リスクが高い、直近で拒否された、stop intent がある場合は
`hold` / `suppress` / `stop` へ寄せる。

### ログ上の分離

二段 gate は、ログと artifact でも分けて残す。

```json
{
  "llm_fire_gate": {
    "decision": "fire",
    "score": 0.88,
    "score_breakdown": {
      "dialogue_reply_readiness": 0.55,
      "motivation_initiative": 0.08,
      "world_deliverability": 0.17
    },
    "reason": "pressure synthesis crossed LLM fire threshold"
  },
  "speech_emission_gate": {
    "decision": "replace_current",
    "score": 1.12,
    "reason": "prepared reply beats current speech and interruption risk is acceptable"
  }
}
```

これにより、「LLM を早く走らせる問題」と
「生成済み発話を実際に声に出す問題」を別々に調整できる。
2026-06-20 時点の実装では、`SpeechScheduler` は legacy unit と返却互換の型名として残すが、
`TomokoConversationCore` の通常発話裁定経路からは外す。
通常経路は次の順で判断する。

```text
TurnMaterials / WorldMaterials / PersonalityMaterials
  -> DialogueTurnPressure / NaturalSpeechPressure / MotivationPressure / WorldPressure
  -> LlmFireGate
  -> LLM
  -> PreparedSpeechCandidate
  -> SpeechEmissionGate
  -> SpeechOrder
```

`LlmFireGate` は pressure を合成して LLM に行くかだけを見て、
`SpeechEmissionGate` は LLM 後の生成済み候補を今 hot-path に送出するかを最終的に整える。
hot-path から届く `TurnMaterials` は pressure models の material であり、
gate に直接押し込む raw input ではない。

## 未来メモ: 口喧嘩できる Tomoko

Tomoko の中長期的な到達点のひとつは、「口喧嘩できる Tomoko」である。
これは攻撃的な発話を増やす、という意味ではない。
人間の会話が盛り上がった時に起こる、
相手が完全に話し終える前に少し前のめりに入り、
時には勘違いし、揚げ足を取り、相手から「ちょっとちゃんと聞いてよ」と言われるような、
関係性のあるリアルな応答を作る、という意味である。

この挙動は単一の LLM prompt ではなく、複数の軽量な信号の合成で作る。

```text
semantic_saturation:
  意味的にもう返せるか / どれくらい確からしく返せるか

MaAI backchannel:
  聞いている感じを固定相槌として出す

VAP / p_yielding:
  音響的に相手が譲り始めているか

motivation:
  Tomoko が今どれくらい話したいか、反論したいか、茶々を入れたいか

partial gate:
  早すぎる発話のブレーキ

final reconcile:
  前のめりに入った後で final STT と整合しなかった時の回復
```

現時点で、ピースは揃いつつある。

- semantic saturation は蒸留 scorer 化でき、hot path でほぼゼロコストに近い。
- partial STT から final 前に main reply を準備する経路がある。
- partial gate は連続確認と閾値で過発火を抑えられる。
- final reconcile により、前のめり発話後の重複発話を抑えられる。
- MaAI fixed backchannel lane により、本文返答とは独立して相槌を返せる。
- scheduler は score_breakdown を持ち、なぜ話したか / 黙ったかを後から調整できる。

残る中心課題は motivation の設計である。

motivation は、単なる「話す頻度」ではなく、
閾値を動かす圧力として扱う。
Tomoko が静かに聞きたい時は final や明確な無音まで待ち、
Tomoko が強く反論したい時、盛り上がっている時、茶々を入れたい時は、
semantic_saturation が少し低くても、VAP が終端寄りなら前のめりに入る。

```text
semantic high + motivation normal:
  普通に返答する

semantic high + VAP yielding + motivation high:
  final 前に main reply を準備または発話する

semantic medium + motivation high:
  短く前のめりに入る
  例: 「いや、それってさ」

semantic low + motivation very high:
  勘違い / 茶々 / 揚げ足取りとして短く入る
  例: 「待って、今の言い方ずるくない？」

semantic low + motivation low:
  黙る、または fixed backchannel だけ返す
```

低 semantic で発話する場合は、断定的な回答ではなく、
撤回可能な短い割り込みとして扱う。
ここでの誤解は必ずしも失敗ではなく、
人間側が「違う違う」「ちゃんと聞いて」と返せる関係性イベントになる。

この方向に進める時も、hot-path-process に人格状態を持たせない。
hot-path は音声シグナル、MaAI、cached backchannel、低レイテンシー出力を担当する。
motivation の所有者は tomoko-process 側に置き、
hot-path には、その時点の閾値やモードだけを渡す。

## 未来メモ: NOTIFY/LISTEN から WS origin へ

現行 v2 の process 間連携では、PostgreSQL を source of truth とし、
`LISTEN/NOTIFY` は DB row id だけを流す control plane として使っている。
これは durable な会話記録、recovery、debug、replay のためには有効である。

一方で、VAP、MaAI、semantic saturation、motivation を使って
発話開始タイミングを詰めていく future path では、
short-cycle の制御線を DB / `LISTEN/NOTIFY` に乗せ続けると
insert / select / notify / reconnect / recovery poll の儀式が体感レイテンシーに乗る。
特に VAP のような 20ms から 50ms 周期の音響シグナルは、
DB に流すのではなく hot-path のメモリ内に閉じ込めるべきである。

将来的には、リアルタイム制御の origin を `LISTEN/NOTIFY` から
常時接続 WebSocket へ寄せる。

```text
Browser
  <ws: audio/events>
Hot-path process
  <internal ws: realtime control/events>
Tomoko process
  <world ws: research/tasks/events>
World information worker
```

hot-path は 2 本の WebSocket を持つ。

```text
browser <-> hot-path:
  audio frames
  playback events
  browser-facing transcript / status events

hot-path <-> tomoko-process:
  turn opportunity snapshot
  motivation snapshot
  threshold profile
  mode / interruptiveness
  turn opportunity
  early-start decision
  backchannel emitted
  decision trace
```

Tomoko process は、さらに world information worker と WebSocket を張る。
調査タスクは HTTP request / response の一括完了を待つのではなく、
途中結果を切り詰めて返せる streaming task として扱う。

```text
tomoko-process -> world worker:
  research_task(query, budget_ms, urgency)

world worker -> tomoko-process:
  partial_findings
  usable_summary
  final_summary
```

この構成での責務分担は次の通り。

```text
hot-path:
  音声反射
  VAP / MaAI / semantic saturation
  fixed backchannel
  low-latency TTS/audio execution

tomoko-process:
  motivation
  personality state
  relationship state
  memory/context
  conversation policy
  threshold profile

world information worker:
  search / research / summarization
  long I/O
  partial information streaming

PostgreSQL:
  durable conversation log
  memory
  audit / replay
  final research result persistence
```

重要なのは、WebSocket に raw VAP の全フレームを流さないことである。
VAP の生値は hot-path 内で平滑化し、境界を渡るのは
`turn_materials` のような Tomoko 側 pressure model が消費できる material にする。
同様に、Tomoko から hot-path へ渡すのも人格状態そのものではなく、
その時点で hot-path が使う `motivation_snapshot` や `threshold_profile` に限定する。

つまり、DB は保存と再現の場所、WebSocket は神経系、
hot-path は反射、Tomoko process は人格、world worker は外界認識として分離する。
2026-06-20 時点では、hot-path / Tomoko process 間の会話制御線は
internal WebSocket を origin に寄せる。

```text
hot-path -> tomoko-process:
  stt_observation(partial/final)
  turn_materials
  playback_state

tomoko-process -> hot-path:
  speech_order
  cancel_order
  ack / backpressure

tomoko-process -> PostgreSQL:
  durable utterance
  scheduler decision
  prompt request
  speech-order audit
  model/output summary
```

`LISTEN/NOTIFY` は hot control RPC から外し、WS control plane の fallback /
比較用として残す。DB は保存と再現の場所、WebSocket は神経系、
hot-path は反射、Tomoko process は人格、world worker は外界認識として分離する。
