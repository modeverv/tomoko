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