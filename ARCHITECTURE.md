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
