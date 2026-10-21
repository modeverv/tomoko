# Tomoko の実装で得られた知見 — v1 / v2 再構築資料

作成日: 2026-10-21。移動前の Git HEAD: `55763037e176cc83dd92fc8e53e90cb486821fe9`。
ファイル名 `knowlaedge.md` は今回指定された綴りを採用している。

この文書は、最初の実装 `v1/` と、今回 `v2/` に保管した旧 root の実装から、
再構築時に失いたくない設計理由・失敗・実測・制限を取り出したもの。
単なる機能一覧でも、次の実装の確定仕様でもない。
元文書には追記によって否定された方針が残るため、記録時点と実装状態を分けて読む。

## 目次

- [読み方と証拠の扱い](#knowledge-01)
- [再構築前に押さえる結論](#knowledge-02)
- [資料の全体地図](#knowledge-03)
- [世代をまたいで変わったこと](#knowledge-04)
- [v1 の到達点と読み方](#knowledge-05)
- [v1: 音声経路と境界の知見](#knowledge-06)
- [v1: 返答生成・TTS・再生停止の知見](#knowledge-07)
- [v1: LLM、prompt、cache、LoRA の知見](#knowledge-08)
- [v1: 記憶・DB・人格を混同しない設計](#knowledge-09)
- [v1: 自発発話、相槌、turn-taking の知見](#knowledge-10)
- [v1: 調査、予定、タスク、通知、外界観測](#knowledge-11)
- [v1: 構造変更と検証から得た知見](#knowledge-12)
- [v1: 再実装時に参照する資産と未完事項](#knowledge-13)
- [現行実装（アーカイブ v2）の構造から得た知見](#knowledge-14)
- [音声と性能評価から得た知見](#knowledge-15)
- [意味飽和度モデル・蒸留・教師比較から得た知見](#knowledge-16)
- [評価をやり直すときに失わないこと](#knowledge-17)
- [再構築に向けた横断的な確認項目](#knowledge-18)
- [プライバシーと実験データの境界](#knowledge-19)
- [今回の保管と再実行時の注意](#knowledge-20)
- [次の設計時に明示的に決めること](#knowledge-21)

<a id="knowledge-01"></a>

## 読み方と証拠の扱い

- **実装確認**: 保存した source・test に処理が存在する。実機で自然に動くことまで保証しない。
- **当時の実測**: LOG、latency、benchmark report に日時と条件付きの結果がある。今回再測定した値ではない。
- **当時の判断**: MEMORY / ARCHITECTURE に採用理由がある。後の追記で変わっている場合は併記する。
- **計画・提案**: PLAN / PLANS / 評価設計にはあるが、完成の証拠はない。新実装へ自動採用しない。
- **未解決**: 実測不足、既知の欠陥、判断保留。成功した数字と同じ重みで引き継ぐ。

出典リンクはこのファイルからの相対パス。`../v1/` は最初の実装、`../v2/` は今回保管した実装。
旧文書内の `root` は、その世代の当時の実装ルートを指す。
同じ `S23` が複数用途で使われるなど Phase 番号だけでは一意にならないため、日付・題名・内容で同定する。
`v2.md` 等のファイル名だけで「v2 で実装済み」と判断しない。
`logs/`、`work/`、ローカル比較実験、学習済みの大型モデルは Git 管理外のものを含む。
その場合、文書から参照できても別の checkout に成果物があるとは限らない。

今回の抽出はローカルの設計資料・コード・テスト・実験集計を中心に行った。
実会話本文、画面の内容、個人の予定、認証情報は知見集へ転載しない。
外部ライブラリや API の記述は当時の記録であり、2026-10-21 時点の最新版仕様を確認したものではない。

<a id="knowledge-02"></a>

## 再構築前に押さえる結論

1. **音が出たこと、答えたこと、聞こえ終わったことは別の事実。**
   ACK や相槌で first audio を短縮しても、本回答の待ち時間は短くなっていない場合がある。
   planned text、生成済み text、送信済み audio、実際に再生された範囲も分ける。
2. **速い制御と永続化は寿命が異なる。**
   partial / VAP / playback / cancel を DB 経由の逐次 RPC にすると、
   永続化の待ちが会話制御へ入り込む。v2 は途中で internal WS を制御の起点へ変更した。
3. **「状態を一か所に置く」という宣言だけでは競合はなくならない。**
   LLM/TTS を await している間に材料が変わる。実行の直前に再判定すること、
   古い結果を失効させる世代、制御命令が届く独立した経路を実装・観測する必要がある。
4. **意味の準備、音響的な話し終わり、参加意欲を同じスカラーに潰さない。**
   VAD、VAP、MaAI の相槌信号、STT partial、semantic saturation は別の情報を測る。
   意味飽和の軽量学生は高速だが、独立した未知例に対する誤開始・待ちすぎは残った。
5. **最新の資料ほど完成しているとは限らない。**
   v1 に実装済みの豊富な記憶・人格・周辺機能があり、v2 では簡素化した境界・仮実装もある。
   v2 の簡易要約・文字ハッシュ embedding と v1 の本来の意味検索を同じ能力として数えない。
6. **モデル単体の速度は会話の速度ではない。**
   endpointing、前処理、IPC、queue、全文待ち、TTS、ブラウザ予約再生まで測る。
   cold/warm、合成/実マイク、file/realtime、教師/学生を分けて比較する。
7. **テストの成功範囲をそのまま引き継ぐ。**
   fake replay の成功は制御 contract の証拠。DB integration は保存・通知の証拠。
   人間の体験品質、音声出力開始、実機の割り込み成功まで含めて証明したことにはならない。

<a id="knowledge-03"></a>

## 資料の全体地図

| 調べたいこと | 最初に読む資料 | 読む際の注意 |
|---|---|---|
| v1 の設計理由と失敗 | [v1 MEMORY](../v1/MEMORY.md)、[v1 LOG](../v1/LOG.md)、[v1 ARCHITECTURE](../v1/ARCHITECTURE.md) | 追記で撤回された値や案がある |
| 現行 v2 の変遷 | [v2 MEMORY](../v2/MEMORY.md)、[v2 LOG](../v2/LOG.md)、[v2 ARCHITECTURE](../v2/ARCHITECTURE.md) | 初期の DB 中心設計と後期の WS 制御を区別 |
| 実装工程と達成範囲 | [v1 PLAN](../v1/PLAN.md)、[v2 PLAN](../v2/PLAN.md)、[旧 v2 PLAN](../v2/PLAN.old.md)、[f.md](../v2/f.md) | チェック済みの測定対象を確認する |
| v2 に残った制御上の問題 | [設計レビュー](../v2/PLANS/openai.md)、[改修計画](../v2/PLANS/openai.plan.md)、[repository-map](../v2/_docs/openai-plan/repository-map.md)、[O0A contract](../v2/_docs/openai-plan/o0a-contract.md) | 指摘・予定と、実装済み O0A-01/02 を分ける |
| 音声・LLM の実測 | [latency](../v2/_docs/latency.md)、[benchmarks](../v2/_docs/benchmarks/) | 単発最良値で一般化しない |
| 軽量モデルの再現 | [make-model README](../v2/make-model/README.md)、[教師比較](../v2/_docs/benchmarks/teacher-comparison-20260927/REPORT.md)、[特徴学習](../v2/_docs/benchmarks/feature-learning-20260927/REPORT.md) | public synthetic と private を混ぜない |
| 会話体験の評価軸 | [evaluation](../v2/_docs/evaluation.md) | 評価設計案であり、人間評価が収集済みとは限らない |
| 最初の実装以前の制約 | [_reference README](../v2/_reference/README.md) | Unity/REST の経験を、そのまま新設計に複製しない |

<a id="knowledge-04"></a>

## 世代をまたいで変わったこと

| 論点 | v1 で積み上げたもの | v2 での変更・残った限界 |
|---|---|---|
| 会話の所有権 | TomoroSession、central/edge、attention、参加判断 | tomoko-process と身体実行 hot-path の分離。実行競合の課題は残る |
| 通信 | ブラウザ `/ws` と DTO、プロセス間連携 | DB id-only NOTIFY から、hot control は internal WS 起点へ変更 |
| 早期応答 | VAD/partial/相槌/投機経路等の検討 | Materials→Pressures→Gates、partial と final の reconcile、SpeechOrder |
| 記憶 | 原本・session・summary索引・人格snapshot・検索 | v2 schemaへ再構成。一部は簡易要約・64次元文字ハッシュに縮小 |
| 評価 | backend probe、実マイク、unit/integration、体験評価設計 | fake/real replay・autopilot。ACKと本回答、送信と再生の区別は追加課題 |
| 意味飽和 | 小型モデルや音響側の可能性を検討 | public synthetic hash-ridge runtimeと教師比較。汎化不足・shortcutを実測 |

以下は、世代ごとの詳細、性能・学習の証拠、再構築時の確認項目の順に読む。

<a id="knowledge-05"></a>

## v1 の到達点と読み方

この節は、主に 2026-05-23〜2026-06-16 の v1 の設計・実装・測定記録から抽出した。数値は当時のマシン、モデル artifact、warm/cold 条件に依存する履歴値であり、2026-10-21 に再計測した結果ではない。文書中の「完全」「最速」「正式採用」などの表現は、そのまま一般化せず、確認したシナリオの範囲に限定して読む。

- v1 は単なる音声チャット試作ではなく、VAD/STT、参加判定、会話 LLM、TTS、再生 telemetry、割り込み、会話 DB、要約、embedding、人格 snapshot、自発発話候補、調査、予定、タスク、timer/alarm、外界観測まで実装していた。
- README の M1/M2 は実装済み、M3 自発発話は自然さを調整中、M4 は一部実装、M5 複数部屋・複数人の本格運用は未着手だった。後半には thinker2 のカメラ・画面認識まで進んでいる。
- v1 最終 LOG の full unit は 769 passed / 29 deselected / 3 warnings。これは当時の成功記録であり、v1 単独ディレクトリを現在そのまま実行できる保証ではない。
- v1 の `MEMORY.md` は追記と否定が多数あり、冒頭の「確定した判断」だけでは最終状態を復元できない。README、ARCHITECTURE、PLAN も更新時点がずれる。後述する矛盾表と、実ファイルの config / factory / runtime を併読する。
- 文書が何度も参照する `v1/_docs/latency.md` は、今回の抽出時点では v1 内に存在しなかった。旧 `_docs`、docker migration、実験録音等の参照が root 側や管理外 artifact に分散していた可能性がある。存在しないリンクを実測原本の代わりにしない。

出典: [v1 README](../v1/README.md)、[v1 LOG](../v1/LOG.md)、[v1 PLAN](../v1/PLAN.md)、[v1 MEMORY](../v1/MEMORY.md)。

### v1 の最終ファイルと履歴文書が食い違う箇所

| 項目 | 履歴に残った複数の判断 | アーカイブの実ファイル / 最後の記録 | 再利用時の注意 |
|---|---|---|---|
| session 構造 | 一枚 → package / reducer / effects / maps → 一枚へ復旧 | `server/session.py` が存在。グラフで確認した `TomoroSession` は 4,012 行 | 冒頭の約 2,200 行という説明は凍結当時の値。分割済みという記録を現状扱いしない |
| VAD 無音時間 | 400 → 1000 → 800ms、後に VAP 動的調整 | central/edge config は基準 400ms、pre-roll 500ms、VAP hybrid 150 + delta 650ms / probability 0.90 | 固定 800ms を「最終設定」として転載しない |
| STT 音響フィルタ | bandpass + signal gate 常時 ON という判断 | `server/edge/main.py::_create_default_stt_audio_frontend()` は `enabled_filters=()` | 実装や unit があることと本番で有効なことは別 |
| TTS | say → Kokoro → Irodori → Kokoro → Supertonic → VOICEVOX、6/11 に Kokoro 正式採用という記録 | central/edge config は `voicevox_tsumugi_chunked`、segment 0.6 秒 | 「正式採用」の単一記述から再現環境を決めない |
| VOICEVOX chunk 長 | 0.6 → 0.2 秒で first chunk 高速化 | 6/13 の途切れ対策で 0.6 秒へ戻す。実コードの default も 0.6 | 最短 first chunk と連続再生の安定性を区別 |
| 過去 user prompt | cache のため wrapped prompt 全体を保存・再利用 | 6/11 に膨張を理由に履歴は raw `turn.text` 優先へ戻す | cache hit のために動的文脈を履歴へ複製しない |
| shadow semantic JSON | 2 キーの constrained structured output を強制 | 6/13 は Gemma E2B lane のみ compact prompt + 厳格 parser に変更 | structured output の対応可否・モデル適性を実測する |
| ノード間通信 | DB のみ / pub-sub なしという初期原則 | edge/gateway WebSocket、後に PostgreSQL LISTEN/NOTIFY を実装 | 初期スローガンより、実際に導入された例外と所有境界を把握する |
| thinker2 | T2.9 では capture/inference をまだ常駐統合しない | 後続 LOG では `thinker2-capture-once` に実 capture / VLM を追加 | 計画のチェック済みと特定機種での動作確認を区別 |

出典: [v1 central config](../v1/config/central_realtime.toml)、[v1 edge config](../v1/config/edge_kitchen.toml)、[STT factory](../v1/server/edge/main.py)、[session runtime](../v1/server/session.py)、[VOICEVOX backend](../v1/server/shared/inference/tts/voicevox.py)。

<a id="knowledge-06"></a>

## v1: 音声経路と境界の知見

### 音声入力とブラウザ

1. Unity/REST 時代の録音完了→OGG→Base64→REST 一括送受信→MP3 変換という経路をやめ、ブラウザから float32 chunk を既存 `/ws` へ送る方式にした。変換回数、待ち時間、状態 flag の分散を減らす狙いだった。
2. AudioWorklet は 16kHz / 512 samples = 32ms の chunk 単位。VAD の無音閾値は chunk 境界へ丸められる。合成 scorer で 300ms は 320ms、400ms は 416ms、500ms は 512ms になった。
3. `MediaStreamSource -> AudioWorkletNode` でグラフを止めるとブラウザが pull せず `process()` が継続しない可能性があった。`AudioWorkletNode -> GainNode(gain=0) -> destination` で無音接続して処理を維持した。
4. float32 の VAD hot path では配列をそのまま扱い、発話境界だけ `SpeechSegment` を生成する。時刻取得や境界 DTO の大量生成を hot loop に入れない。
5. VAD 検知前の音声を捨てると wake word 冒頭が失われる。idle 中も直近 500ms を bounded deque に保持し、`idle -> listening` 時に segment 先頭へ連結した。超過 sample は古い側を切り、reset 時にクリアする実装が残っている。
6. VAD の無音閾値を延ばせば文中の小休止で切れにくいが、1000ms では体感遅延が強かった。閾値、pre-roll、意味的な継続判定、未出力 reply cancel は別の問題として測る必要がある。
7. マイク / 出力先 selector は `enumerateDevices`、`getUserMedia({deviceId})`、`setSinkId()` のローカル UI 責務として認めた。出力は Web Audio mixer → `MediaStreamDestination` → hidden audio element。非対応ブラウザでは selector を無効化して既定出力を使い、会話 state や `/ws` contract は変えなかった。
8. debug 録音は REST を追加せず `/ws` の start/stop event で制御し、録音中は通常 VAD/STT へ送らず recorder にだけ保存した。同一録音で frontend / STT を比較できることが、実装を評価する前提になった。

出典: [v1 MEMORY: VAD / AudioWorklet / debug recording](../v1/MEMORY.md)、[VADProcessor](../v1/server/edge/pipeline/vad.py)、[client](../v1/client/)。

### STT の失敗は音響・認識・参加判定に分ける

- 「wake word に反応しない」は、認識語の alias 不足だけではなかった。segment 語頭欠落、Apple Speech が呼びかけ語だけを省略/別語化、transcript filter、ambient wake-word gate の各段に原因があった。`transcript_final` を UI に出し、STT が聞いた内容と参加結果を別々に観測した。
- Apple Speech は Swift sidecar + `.app` bundle / embedded Info.plist / ad-hoc codesign を使った。CLI の `requestAuthorization` が TCC の usage description を認識せず abort する環境があり、通常の Python backend と同じ起動・権限条件ではなかった。
- Apple Speech の `No speech detected` を未知例外と同じにすると WebSocket runtime が落ちた。空認識は通常系として空 transcript にし、権限や sidecar 実行失敗は分類して表へ出す。
- `contextualStrings` に Tomoko の表記候補を渡した。誤認識された語を際限なく wake-word alias に足すより、認識側の語彙補助を先に試した。
- MLX Whisper large turbo は実会話精度を改善した一方、低信号で反復幻聴・GPU 高負荷が見えた。無音や短いノイズを大型 STT へ送るコストを考慮した。
- STT 前の `SttSignalGate` と STT 後の `TranscriptFilter` は役割が違う。前者は duration / RMS / peak / active frame ratio から投入可否を決め、後者は定型 hallucination、反復、低信号 ASCII、低信頼断片を抑える。
- 低信号の実録音では raw / frame gate でも幻聴、spectral subtraction で反復悪化。ノイズプロファイル自体がデジタル無音に近く、ノイズ低減比較の素材として弱かった。filter に名前があるだけでは有効性を主張できない。
- 初期 signal gate は `rms_db < -45 AND active_frame_ratio < 0.25`、短すぎは 80ms を基準にしたが、最終 factory では frontend 全 OFF。閾値は再利用可能な実験条件であり、無条件に復活すべき設定ではない。
- 100Hz high-pass / 7.2kHz low-pass は比較的軽かったが、2kHz low-pass は子音を崩した。RNNoise/ffmpeg arnndn は 5 秒素材で約 60〜65ms、低信号幻聴は解消しなかった。常用にせず比較用に残した。
- 日本語の「低音量かつ20文字以下」drop は成立した短文まで落としたため、blanket filter を6文字以下に狭めた。時刻質問や調査結果の明示要求など、短い実用 command は先に accept する必要があった。
- 英語/中国語混入は STT にも LLM reply にも存在した。入力ログだけを見て「LLM は日本語」と決めたり、reply を見ず「すべて STT 幻聴」と決めたりしない。

出典: [v1 MEMORY: STT signal gate / Apple Speech / TranscriptFilter](../v1/MEMORY.md)、[audio frontend](../v1/server/edge/pipeline/stt_gate.py)、[STT 比較ツール](../v1/_tools/bench_stt_backends.py)。

### STT の履歴測定

| 条件 | 当時の値 | 読み取れること |
|---|---:|---|
| 同じ say 音声、faster-whisper small | 977.5ms | 同期推論は `asyncio.to_thread` に逃がした |
| MLX Whisper small warm | 102.1ms | 初回モデル取得/cache込み warm は 13,404.8ms。warm と cold を混ぜない |
| WhisperKit CLI small | 4,755.6ms | 毎回プロセス起動は online 経路に重い |
| WhisperKit serve small | 常駐後214.3ms、auto-start warm4,791.6ms | 常駐 HTTP backend で CLI 起動分を除去 |
| STT 30回 p95、MLX small | idle106.3ms / Kokoro同時165.2ms / Kokoro+LFM同時165.7ms | TTS/LLM と同時に測ると GPU 競合が見える |
| 同条件 WhisperKit CoreML p95 | 222.0 / 216.9 / 216.0ms | 単体最速ではないが固定約200ms lane として安定 |
| Apple Speech 対 MLX large turbo q4、合成音声3回平均 | 183.3 対240.6ms | 認識表記・語頭欠落と速度を併せて評価 |
| 同比較、実録音平均 | 242.7 対248.7ms | 合成音声だけの優位を実マイクへ一般化しない |
| Apple Speech rolling partial、約6.06秒素材 | 1.27 / 2.60 / 3.94 / 5.30 / 6.70秒で部分出力 | 1秒蓄積の再認識による部分出力。最後の不足分は final `transcribe` で補完 |

出典: [v1 MEMORY: STT ベンチ各節](../v1/MEMORY.md)。これらは別日・別負荷の測定を含み、単一順位表ではない。

<a id="knowledge-07"></a>

## v1: 返答生成・TTS・再生停止の知見

### 非同期生成と再生の事実を分ける

1. WebSocket 受信ループで reply/TTS 全体を await するとマイク処理へ戻れず、実際の barge-in ができない。reply task と TTS worker を background 化し、sentence flush された text を queue に入れた。
2. hard interrupt では LLM/reply task と TTS worker を cancel するだけでは足りない。ブラウザで既に予約された `AudioBufferSourceNode` を server の `audio_control stop` で止める。
3. `audio_start.turn_id`、`chunk_id` は順序並べ替えのためではなく、cancel/stale discard/telemetry の対応付けに使う。単一 WebSocket 自体のメッセージ順序保証と混同しない。
4. Chrome AEC/NoiseSuppression だけでは MacBook スピーカー→内蔵マイクの回り込みが残った。client は再生開始/終了という事実だけを送信し、server が `(turn_id, chunk_id)` の active set と終了猶予を持つ。
5. echo grace は 1200→2000→1200ms と揺れた。改善の主因は終了猶予の長さより active chunk の把握だった。ミリ秒 timestamp で playback と transcript を照合することが必要。
6. playback 中でも stop/訂正/待って等の明示 interrupt は echo gate より先に judge へ通す。通常 follow-up は既存 echo / barge-in 判定へ回す。
7. 音声送信完了、ブラウザ再生完了、reply 完了は別の事実。v1 の送信 contract は後に `reply_text -> audio_start -> binary -> audio_end -> reply_done` に統一した。`audio_end` はまだブラウザの物理再生終了そのものではない。 また v1 の `playback_started` は予定された `startAt` に合わせた timer 通知であり、ハードウェアから音が出た瞬間の直接測定ではない。`playback_ended` は AudioBufferSourceNode の ended event に基づく。
8. attention timeout は VAD idle かつ playback idle の無音 chunk だけで進める。Tomoko が再生中なのに engaged→cooldown へ落ちるバグが実ログで確認された。
9. VAD listening 再開時の「未出力 reply 即 cancel」は、早い遮り防止とノイズによる返答消失のトレードオフで、採用→否定→再採用になった。どちらも実例があり、単一の恒久ルールとして結論づけられない。出力済みか、実発話か、意味が継続したかを別々に扱う必要がある。
10. WebSocket が長い TTS 中に切れた後、`audio_end` を送って ASGI exception になった。output disconnected を記録し、以後の audio / end / reply_done を止めることは通常終了処理である。

出典: [v1 MEMORY: AudioPlaybackControl / telemetry / cancel / disconnect](../v1/MEMORY.md)、[session runtime](../v1/server/session.py)。

### TTS API の streaming と体験上の streaming は異なる

- ブラウザは WebSocket binary 1件ごとに `decodeAudioData()` するため、それぞれが complete WAV である必要があった。numpy PCM や HTTP byte stream をそのまま渡さず backend 側で RIFF/WAVE に包んだ。
- Kokoro `generate_stream()` は短文で1 chunkだけ返る場合がある。API名だけでは早期音声・複数chunk・小さな停止粒度を保証しない。
- Irodori v2/v3 の当時の mlx-audio は `stream=True` 未実装。v2へ下げても解決しなかった。文単位/短文単位の逐次生成は実現したが、モデル内部の true streaming とは区別した。
- Irodori v3 の `seconds` 指定で duration predictor を省き、6 steps + sway sampling で短単位約100msへ縮められた。しかし duration を短くし過ぎると末尾切れや詰まりが起き、x1.5では first463.0ms / total1264.6msへ悪化した。最速設定が自然な声とは限らない。
- Kokoro の日本語 voice は `language="ja"` と `misaki.ja.JAG2P(version="pyopenjtalk")` で扱った。unidic package の存在と辞書本体の存在は別で、fugashi/mecabrc 不足が「文字は出るが音が出ない」の原因になった。
- voice の emotion mapping が存在しない `jf_beta` を選ぶと特定感情だけ無音になった。`list_voices()` で確認して既定 voice へ fallback、声色は speed 等でも表現した。
- Kokoro に短い呼びかけ断片だけを渡すと余計な末尾音が出た。読点 soft flush に10文字以上という保護を試した後、構成単純化で sentence flush に戻した。TTFTのための細切れは音質評価が必須。
- Supertonic CoreML は HF cache 内 `.mlpackage` の symlink を compiler が追えず `weight.bin` を見失った。`copytree(..., symlinks=False)` で実体展開して動いた。モデルのダウンロード成功と実行可能性は別。
- VOICEVOX PR1823 は legacy multipart complete-WAV parts ではなく single `audio/wav` stream。headerを読み24kHz mono PCMを complete WAV単位に包み直す。24kHz以外は422で拒否される契約だった。
- `is_last` 判定のために1 chunkを pendingにするとfirst audioが遅れる。最初は `is_last=False` として即yieldし、stream末尾だけfinal扱いにした。
- segment 0.2秒は first166.0ms / total2203.1ms /15chunks、0.6秒は first1512.2ms / total2283.6ms /5chunksという直接smokeがあったが、後の実再生で0.2秒は供給が追い付かずunder-runした。6/13に0.6秒・CPU threads4へ戻した。TTFTだけでは声の途切れを評価できない。

出典: [v1 MEMORY: TTS 各判断](../v1/MEMORY.md)、[VOICEVOX streaming実装](../v1/server/shared/inference/tts/voicevox.py)、[v1 TTS backend群](../v1/server/shared/inference/tts/)。

### TTS と日本語正規化の評価履歴

| 条件 | first / total | 留意点 |
|---|---:|---|
| Irodori v3 品質寄り短文 | 2959.1ms / 1chunk | キャッシュ済みでもtrue streamingなし |
| Irodori短単位、6steps等 | 96.6 /192.7ms | durationを詰めた設定。後に音質問題 |
| Qwen3-TTS 0.6B | 142.6 /545.3ms | 比較用。採用判断は試聴が必要 |
| Qwen3-TTS 1.7B | 216.7 /820.5ms | 同上 |
| Kokoro、句読点保持Gemma正規化後2文 | 112.8 /166.6ms、2chunks | 正規化約211.1msは別。合算first audio約323.9ms |
| Kokoro CoreML CLI | 4816.4 /4816.5ms | Japanese IPA と `--stream` が併用不可、ファイル生成fallback |
| Supertonic CoreML warm | 平均102.4ms、min98.9/max112.8 | 約4.35秒音声。モデルload約5.5秒、別startup測定ではwarm-up約8秒 |
| VOICEVOX通常 `/synthesis` 短文 | 364.7 /364.9ms | 起動済み外部engine |
| Kokoro 6/11比較 | 134.6 /134.8ms、1chunk | 約21.3倍速の生成でも、実際の発声時間は別 |

- 英語混じりの入力をKokoroへ直送すると音声長13.25秒、Gemmaで読み上げ日本語へ正規化すると7.175秒という比較があった。
- ただし正規化で句読点を消すと全文がつながり、flush単位も悪化する。文数・順序・句読点を保持し、文末句読点をcodeで補う必要があった。
- Qwen2.5:7BにTTS向け指示を足すだけでは、8入力中TTS-readyがbaseline2/8、強化prompt0/8、few-shot2/8だった。promptの強さと実際の契約遵守は比例しない。
- Gemma E2Bは同じ8入力でTTS-ready8/8、平均first body204.7msという結果があり、構成を単純化して専用正規化を無効化する時期があった。これは当該8ケースの結果であり、未知語やすべての混在入力を保証しない。

出典: [v1 MEMORY: TTS正規化 / ベンチ](../v1/MEMORY.md)。

<a id="knowledge-08"></a>

## v1: LLM、prompt、cache、LoRA の知見

### 推論ランタイムと出力契約

- 内蔵 `mlx-vlm.stream_generate()` は当時 `asyncio.to_thread` 上でMLX thread-local stream errorになった。同じthreadで同期消費するとevent loopを塞ぐ懸念があり、LM Studio等の常駐別プロセスをSSEで読む方式へ移行した。
- warm-up済みinstanceを会話でも再利用しなければ意味がない。app startupでSTT/TTS/Routerを温め、sessionごとに別instanceを作ってcold costを再発させない。
- 同一URL semaphoreでbackground大型モデルがconversationを塞ぐ懸念があった。URL+model別queueへ分ける変更も入ったが、queue分離は物理GPU/メモリ分離を意味しない。26B同時推論で20秒超に悪化した記録もある。
- emotion行は表示・立ち絵信号として本文/TTSから分離する。モデルが改行を落とす、未定義emotionを出すケースがあり、許可label以外をneutralへ丸めるruntime guardが必要だった。
- `HTTP 200` や first any token は返答成功を意味しない。dflash/LFM系は `reasoning_content` だけを出し、Tomokoが読む `delta.content` は空という例があった。visible/speakable content、構文、本文言語、生成完了を別に測る。
- dflash Gemma4では `think=false` / `thinking=false` は効かず、当時の正しいrequestは `chat_template_kwargs.enable_thinking=false`。server側のtemplate引数でも明示した。APIがパラメータを受理しただけでは効果を確認できない。
- 31Bが遅い原因をhidden thinkingと決め付けず、LM Studio formatted input/outputを確認した。当時LM Studio経路ではthought本文は観測されず、サイズ/prefill/decodeの影響と判断した。dflashのreasoning問題とは別件。

出典: [v1 MEMORY: Gemma / LM Studio / dflash](../v1/MEMORY.md)、[backend群](../v1/server/shared/inference/backends/)。

### prompt の最適化で起きた逆効果

1. 固定persona/overlay/rulesをsystem prefixへ、時刻・今回のcontextを後方user messageへ分けた。実runtime6turnで平均first5142.1→4697.1ms、total7715.7→7064.3ms。ただしturnごとの揺れがあり、常に速いとは言えなかった。
2. startupで808token中804tokenをrestoreしていても、本番1338/1337/1155tokenの各turnはrestore0で約4.9〜5.3秒のprefillになった。「warm-up成功」だけでlive latency改善を判断できない。
3. 前turnへ実際に送ったwrapped user promptを履歴へ保存するとcacheが効くraw A/Bがあった。一方、calendar/taskなどが毎turn履歴に重複蓄積してpromptが膨張したため、6/11に過去履歴をraw transcriptへ戻した。cache最適化にはbounded contextという制約が必要。
4. active sessionへglobal recent turnsを補充すると、turnが増えるたび補充数が変わりprefixが揺れた。同一sessionにturnがあればsame-sessionを使い、空の時だけ過去sessionを補助にする方式へ変更した。
5. 時刻block後置は意味を壊さなかったが、4turn平均first audioは6257.6→6002.1→6413.0msと揺れた。小さな差を明確な高速化と断定しない。
6. topic-return hintは話題復帰が成功した一回があったが、そのrunのfirst audioは悪化した。会話品質修正とlatency修正を同じ実験で混ぜず、hintを外して目的を限定した。
7. persona/calendar/taskのsession-stable structured contextを先頭、現在発話、dynamic recall/research/timeを後方とする比較ではtotalが改善する条件があったが、firstが悪化する条件もあった。prompt orderは普遍則ではなく測定対象。
8. 最新contextをTASKS/MEMORIES/SHORT MEMORY/RESEARCHに圧縮し、冗長な説明/metadataを削減した。6/11のwarm2回目3turnではvoice-end→first text3560.1〜3812.4ms、transcript→first text2447.9〜2658.5ms。依然としてLLM時間が大きい。

出典: [v1 MEMORY: 2026-06-07〜11のprompt/cache比較](../v1/MEMORY.md)、[runtime比較ツール](../v1/_tools/bench_runtime_thinkfast_conversation.py)。

### 小型・大型モデルとLoRAを比較した結果

| 実験 | 観測 | 保持すべき結論の範囲 |
|---|---|---|
| LM Studio E4B/26B/31B、短い本番相当prompt | E4B first252〜331ms、26B281〜331ms、31B905〜1369ms | 大型化だけで会話の良さが速度低下を正当化するわけではない |
| 26BとE2B/E4Bの実会話 | 26Bは文脈を受け止める応答が良いというユーザー体感 | 体験品質評価として残す。定量万能評価ではない |
| dflash26B、no-think direct短文3回平均 | first276.6ms / total369.1ms | 実Router単発は541.4/643.1ms。raw APIとアプリ経路は違う |
| dflash31B、同条件 | first306.7ms / total984.0ms | Routerでは1064.8/1723.5ms。背景laneとして評価 |
| Gemma E2B短反応、warm、max_tokens8、10本 | first p50 400.9/p95 475.9ms、total p50 412.5/p95 490.8ms | 全10本500ms内はLLMテキストのみ。発声までにはTTS等を加える |
| Qwen3 1.7B short persona候補、8本 | EMOTION0/8、thinking leak8/8、英字8/8 | no-think引数が通っても出力を検証。LoRA学習前の入口評価で除外 |
| Gemma E2B OptiQ LoRA | 58train/10valid、120iter、12layers、batch2、peak5.230GB | artifactによってmlx_lm互換性が違う。Gemma E2B一般の非互換ではない |
| 同adapter適用 | emotion12/12、basic11/12、短文5本平均451.7ms、4/5が500ms未満 | adapter経路は短反応lane候補 |
| 通常quantized fuse | emotion先頭7/10、許可emotion6/10 | ファイル作成成功でも品質が崩れる |
| dequantized fuse | 8.7GB、emotion10/10、basic9/10、平均689.1ms | 品質回復とメモリ/速度悪化を両方記録 |
| LFM2.5 8B A1B | 本文content空、reasoning_contentのみ | 既存Tomoko parserでは無言。単純backend差替え不可 |
| LFM2-350M-Extract semantic16ケース | strict shape0/16 | 抽出特化という名称だけでは現行schema適性を保証しない |
| Gemma E2B semantic16ケース | structured shape16/16、accuracy15/16。compact shape/accuracy16/16 | compact prompt + strict parserを当該laneで採用 |

- LoRAでpersonaを焼き込んでも人物設定を完全にpromptから消すと汎用丁寧語assistantへ戻った。89文字のminimal personaでキャラクターをtriggerできた小規模評価があり、full約2700文字+baseのTTFT約1825msに対しminimal+fused約766msだった。ただしモデルとpromptを同時に変えた比較なので、短縮の全量をLoRA単独の効果とはできない。
- `mlx_lm` APIのsampling引数、`--num-layers`、`python -m mlx_lm lora`等が変わった。LoRA実験を再現する時は当時のruntime版・model artifact・chat templateをセットで読む。
- Gemma OptiQのtrainingは、dataset loaderが`enable_thinking=False`を渡さないため、messages形式ではなくnon-thinkingで事前renderしたtext JSONLにした。推論時と学習時のtemplateを揃える必要がある。
- 当時のdflashはadapter引数の受け口があってもruntimeロードで適用されず、fused modelを渡す必要があった。現バージョンでも同じとは限らない。

出典: [v1 MEMORY: LLM/LoRAベンチ](../v1/MEMORY.md)、[LoRA資産](../v1/loras/)、[短反応ベンチ](../v1/_tools/bench_short_reaction_mlx.py)。

<a id="knowledge-09"></a>

## v1: 記憶・DB・人格を混同しない設計

### 原本・索引・解釈・揮発メモの区別

| データ | 役割 | 失敗時/再生成/利用の扱い |
|---|---|---|
| `conversation_logs` | user/tomoko role行の会話原本 | `completed/interrupted/cancelled/error` を区別。中断返答も原本として残し、短期promptは当初completedだけ |
| `conversation_sessions` | 会話の明示的境界とmetadata | 最初の参加発話で開始、attention timeout / withdrawn / UI stop / disconnectで終了 |
| session summary / summary embedding | 会話単位の検索索引 | closed sessionをbackground claimして作る。原本を置き換えない |
| `conversation_embeddings` | turn単位の検索索引 | 原文へのFK、model/embedded_atを保持。別model空間を混ぜない |
| persona/lexicon versioned JSONB | 会話から導いた解釈・人格状態 | 全snapshot+diff、schema version、出典session。base personaを自動上書きしない |
| retrieved carryover | 同一会話内のfollow-up用作業メモ | max6件/900文字から開始、source idでdedupe、session close等でclear |
| `ShortMemoryBuffer` | 数turnだけ使うprompt hint | 最大5件、4turn TTLから開始。DB原本やtask台帳にはしない |
| `task_ledger_entries` | 明示的なタスク状態 | active/completed等の構造化row。自然文hintの積み重ねで再構成しない |
| candidate / pregenerated audio | 将来話す候補・再生成可能cache | expires/spoken/dismissed/used等のlifecycle。会話原本と違う |
| world observations / interpretations | 外部取得文書の正規化と人格に基づく解釈 | raw、normalized fact、private reaction、candidate seedを分離 |

- 既存会話のsession境界を後から推定して埋めず、旧rowのsession idはNULLのまま維持した。過去に存在しなかった意味を移行時に作らない。
- current user turnはDBへ先に書くので、recent turn検索やembedding検索に現在発話自身が混ざる。prompt末尾への二重投入と自己検索hitを除去した。
- UI Stopがsocketを閉じるだけだとsessionが`not_ready`のまま残り要約対象にならない。adapterは事実をevent化し、session ownerが終了理由とsummary pendingを確定するようにした。
- session summary失敗はerror/statusを残し、原本を維持する。再実行はpendingへ戻す運用。summaryを事実と誤認しないため、検索後にraw session turnsを復元するsourceも作った。
- E5-small384次元からBGE-M3 1024次元へ切り替えた時は、旧embeddingを削除してbackfillする移行を行った。次元だけでなくmodel空間を揃える。
- persona updaterはfull snapshotを入出力すると成長につれてtruncationする。previous snapshotをsalience/件数でprompt slice化し、LLMはdiffだけを返す。codeがmerge/pruneしfull snapshotを保存する方式へ変更した。
- persona updateではprompt-only JSONが31Bでも壊れ、json_schemaを要求した。diff-onlyでも最大tokens4096、各diff配列maxItems6等の上限を持った。構文制約と意味的groundingは別である。
- short memory抽出にTomoko返答まで渡すと、想起質問への回答から新規記憶を創作する誤storeが起きた。latest user transcriptだけを渡し、recall/hearing-check等を先にskipする。
- short memory schemaへconfidence/TTLまでLLMに要求すると空白反復等が出た。text/modeだけに縮め、confidenceや期限、dedupe、prompt整形はcodeへ戻した。
- parse/backend失敗時にraw指示文をverbatimとして保存するfallbackは撤去した。失敗を「記憶できた」に変換せずskipを明示する。
- タスク完了・追加を自然文メモに全て残しても、未完了タスクを落とすsimulationがあった。31B抽出で3回成功した後も永続task ledgerへ分離した。LLMを大きくすることだけでは状態台帳の保証にならない。

出典: [v1 MEMORY: Phase7/8/8.5/8.6/8.7/short memory/persona](../v1/MEMORY.md)、[DTO集約](../v1/server/shared/models.py)、[v1 ARCHITECTURE: memory](../v1/ARCHITECTURE.md)。

### Retrieval と prompt exposure を別にする

- `MemoryGate` は「探すか」と「取れたものを今のpromptへ出すか」を分離した。recall requestは会話記憶、calendar requestはcalendarだけ、self-statement/chitchat/unclearはlong-term候補を抑制する実装が残る。
- Retrievedだから関連する、関連するから話すべき、とは限らない。retrieved/exposed/suppressed/reason/source_counts/top_suppressedをlogに出した。
- summary、user原文、Tomoko原文、persona、short memory、calendarは粒度を分ける。初段gateが直接裁いたのはMemoryHit化したlong-term/calendarであり、persona/short memoryは別routeだった。
- retrievalはsource quotaとweightの両方を使った。Tomoko turn snippet max1・role weight0.25、user snippet max4・weight1.0から始め、AI自身の過去発話がユーザー原文を押しのけないようにした。
- 最終scoreはraw similarity × source/role/recency/salienceのweight。detail/stance/recall cueごとにsourceを調整し、selected/dropped/score breakdownをtraceに残した。
- deep取得の次のfast follow-upでmemoryが0になる問題にはsession-local carryoverを使った。さらにfast thinking formatterが渡されたlong_term_memoryを無視していた接続漏れを修正した。storeへ保存しただけではLLMが読んだ証拠にならない。

出典: [MemoryGate実装](../v1/server/session_memory_gate.py)、[ContextSnapshotBuilder](../v1/server/gateway/context.py)、[v1 MEMORY: MemoryGate / retrieval weighting / carryover](../v1/MEMORY.md)。

### ContextSnapshotBuilder の予算・cache・計測

- Builderはread-onlyでsnapshot DTOを返す。sessionがstate/active-session/depthを決め、thinking layerがprompt化する。thinking層がDB schemaを直接知る構造にしない。
- fast/normal/deep/reflectiveを用意し、初期online budget20/50/100ms、reflectiveはbackground。timeoutは失敗でなくdegraded contextとし、同一session recent turnsを優先、optional sourceを省く。
- 独立DB sourceを制限付きparallelで読む。deadlineで未完をcancel/skippedにし、返った順ではなくsource/relevance/recency/salience/token budget/dedupeで採用する。
- `ContextBuildTrace` はelapsed/budget/timed_out/depth/counts/skipped/stage_timings/cache_hits/source_errors、後にcache age/TTLやscore breakdownを含めた。
- 100ms deep timeoutの実例ではSQL自体がsummary約0.13ms、turn検索約1.3ms。支配要因はquery embeddingと同一発話の二重embeddingだった。1buildでembedding taskを共有し、明示recallだけbudget300msに広げた。
- same-session readはindexが効いてSQL0.077msなのに、毎回connectの初回24.525ms・warm7.2〜9.6msが支配していた。`psycopg_pool.AsyncConnectionPool`によりwarm平均0.936/max1.217msへ改善し、一時50msへ広げたfast budgetを20msへ戻した。
- process-local TTL cacheはDB read高速化だけに限定。attention/playback/barge-in/active sessionの権威あるstateやwriteをcacheしない。
- same-session turnsは毎turn増えるためcacheしない。calendar/taskはactive session idをkeyにしたsnapshot cacheへ移行し、task作成/完了でinvalidate、session変更でpruneした。
- 同一sessionのcalendar/task deep microbenchは19.317→0.117→0.090ms、session変更で10.487ms・cache miss。ただしsession中の外部calendar更新の即時反映まで保証した測定ではない。
- BGE-M3は初回download+embed36990.5ms、cached fresh process7838.9ms、warm query32.8ms。モデル常駐を含む起動条件を明示する。

出典: [ContextSnapshotBuilder](../v1/server/gateway/context.py)、[v1 MEMORY: context / query embedding / pool / session cache](../v1/MEMORY.md)。

<a id="knowledge-10"></a>

## v1: 自発発話、相槌、turn-taking の知見

### 候補生成と発話許可は分ける

- background thinkerはsource→seed→evaluator→text-ready候補→必要ならTTS pre-generation→arrival precomputeを行う。source/evaluator/store失敗はerror countとlogへ閉じ、会話を止めない。
- `utterance_candidates` はcreated/expires/spoken/dismissed/maturity、arrivalはcomputed/valid_until/usedでlifecycleを表す。期限内・未消費だけ取得し、priority/createdなどで安定順にする。
- initial dedupeは`context_tags`内のdedupe key。同じactive seedを再insertしないが、spoken/dismissed済みは後で再生成できる。これは初期の最小実装でDB一意性を完全保証する設計ではない。
- arrival precomputeは3分のfresh candidateとし、LLM不正/失敗時は`wait_silent`へfallback。出力可能でないからといって推論文を無理に捏造しない。
- 45秒timerは「必ず45秒で喋る」のではなく候補確認間隔。desire、speakability、personality、candidate soft scoreと、sessionのhard gateを分離した。
- desireは1/5/30分のEMA、無反応/拒否/深夜等でdecay/penalty。ambient logが無いだけで不在と断定しない。presence、VAD、音量、last human speechなどの観測が必要。
- candidate policyはsoft scoreを決め、runtimeのattention/VAD/playback/output availability/stale requestを判断しない。最終gateはsession ownerが結果到着時に再確認する。
- LLM judgeは境界scoreだけに使い、timeout/malformed/unavailableはwaitへ倒す。LLMが話す価値を評価したことと、今実際に話せることは別。
- 自発発話だけではconversation sessionを開始しない。人間が返した時に通常sessionを開始するが、その最初のpromptには直前initiativeをassistant turnとして入れ、自分で出した話題を忘れないようにした。
- 発話候補はtopic名/興味メモでなく、そのまま話せる1〜2文にする。主語欠落や最新情報の過剰断定をrejectし、直前と別の話題は自然な橋渡しを候補生成側で作る。
- feedbackはsource/topic/emotional_needのscopeを持つ。一つの話題への拒否で全自発発話を一律抑えない。
- audio targetがない時はcandidateがあっても発話しない。v1は接続ごとのSessionを維持し、常時生存central Sessionは将来課題だった。
- arrival期限切れはsource-of-truthではないため、7日より古いexpired行をthinker側でDELETEする。autovacuumは「何を消すか」を決めてくれない。

出典: [v1 MEMORY: Phase9/10/initiative policy](../v1/MEMORY.md)、[v1 ARCHITECTURE: desire / speakability](../v1/ARCHITECTURE.md)。

### 音声出力laneを混ぜると入力判定が壊れる

| lane | 会話turn / floor | 原本conversation log |
|---|---|---|
| reply_turn | 通常reply | 保存 |
| initiative_turn | candidate/arrivalから発話 | 保存 |
| interrupting_turn | 人間発話中に床を取る将来lane | 保存想定、v1本格実装は別Phase |
| gesture_audio | 相槌。通常turn/playback ownershipを進めない | 保存しない |
| stop_ack | 停止への短いack | 保存しない |

- MaAI/VAPは会話前段の必須filterにせず、user mic/Tomoko audioをoptional tapへ複製するsensorにした。tap失敗で通常会話を止めない。
- 相槌を通常audio turnで鳴らすと、直後のuser transcriptがTomokoのechoとして落ちた。`turn_id=None` telemetryを無視するだけでは不十分で、server内部でもAudioTurnControllerを進めない必要があった。
- 最終的にGestureAudioEmitterがread-only session snapshotを見てreleaseし、通常session command/reducer/audio_start/audio_endから切り離した。WebSocket binary経路は共用しても状態所有は共用しない。
- ambient中の相槌がwake-word認識を横取りする問題があったため、会話参加前はreleaseしない。user listening、Tomoko playback idle、cooldown、TTS/output存在などを別gateにした。
- thresholdは合成会話に合わせた0.68から、実materialのreactが最大約0.512だったため0.45へ下げ、最後は0.50へ戻した。model raw score、suggestion、session release、実audioを別々に確認する必要がある。
- 同じspeech segment一回だけでは長い話中に少な過ぎたため、segment回数制限を外しglobal cooldown1500msへ。adapter cooldown900msと別物だった。
- `emo` suggestionは観測するがrelease対象にしない。reactとemotionを同じthresholdで解釈しない。
- MaAI result queueはblocking待ちで終了時threadpoolが残るためtimeout pollを使った。短smokeでsuggestionが空でも、本体を通りprocessが終了することを別の検証項目にした。
- Tomoko側tapの時刻は最初server send時刻であり、browser playback時刻の精密補正は未完。音響モデルへ渡す二者channelの時間整合性は再確認が必要。

出典: [GestureAudioEmitter](../v1/server/gateway/gesture_audio.py)、[v1 MEMORY: MaAI/output lane](../v1/MEMORY.md)、[v1 README: Output Lanes](../v1/README.md)。

### 未完発話とshadow推論

- 日本語の助詞終わり、長い継続表現、終端句読点なしなどをruleで保守的にobserverへ落とし、早い通常replyを抑えた。しかしdropだけでは断片内容を次turnへ統合できない。pending user utterance bufferは別Phaseとして残した。
- stop/turn-takingはrule-first、曖昧なdeferだけ小型workerへ。workerは会話本文でなくenum/scoreを返し、失敗時はruleへ戻る。
- partial transcriptをPostgreSQLに保存しLISTEN/NOTIFYでshadow workerを起こす構成が入った。通知はwakeupであり、観測/advisory本体はDB rowにある。
- semantic_saturation / remaining_info_riskの2キーへ責務を縮小。compact promptでも採用条件は厳格にし、余分キー/非JSON/範囲外はrule fallbackにする。
- shadow-benchは固定VAD待ちだけでなく、partial時刻、stability、quiet、VAP p_yielding、semantic結果で先行開始を評価した。記録の「実効lead615ms、miss0」はベンチの設定/シミュレーション結果で、実会話で達成した値ではない。
- fusionは本番advisory/logへ配線済みでもlog-only。`_process_v2_advisory`の実コードは従来の`would_start_inference`で仮推論を起動していた。後続partialがstable prefixから乖離、またはsplit riskが上がるとprovisionalをdiscardする。
- prefill primitiveはMLXLMBackendに実装され、cache keyごとにprefix共有、差分token、trim不可時rebuild、dropを扱う。別processのshadow workerでprefillしてもsession側KV cacheは温まらない。呼出箇所は実際にpromptを作るinference processでなければならない。
- PLAN TT-v2.10dには、実会話1session以上のfusion記録確認と、予測との突合後のtrigger切替が未完で残る。Gemma mlx-vlmへのcache API対応も後続だった。

出典: [shadow worker](../v1/server/background/turn_taking_v2_worker.py)、[session advisory受信](../v1/server/session.py)、[MLXLM backend](../v1/server/shared/inference/backends/mlx_lm.py)、[shadow-bench](../v1/scripts/shadow_bench.py)、[v1 PLAN TT-v2.10](../v1/PLAN.md)。

<a id="knowledge-11"></a>

## v1: 調査、予定、タスク、通知、外界観測

### 調査operatorの境界

- ログイン済みChrome/Perplexity操作は隣repoのresearch operatorへ隔離した。Tomokoはsubprocess JSON-RPC MCP clientで呼び、operatorはTomoko DBへ書かない。
- research受付と待機発話が出てもsubprocess成功とは限らない。session transition schedule/finish、runner start/finish/ingestion、MCP subprocess start/timeout/failure/completionを追う必要がある。
- operator repoをcwdにして起動する必要があった。常駐make processが無いこと自体は故障ではなく、requestごとのsubprocess起動が正常だった。
- 完了結果を再度LLMに再構成させず、pending resultへの明示follow-upとして読み出す方式を導入した。topic付きfollow-upはpending queryと照合し、別調査の結果を誤読しない。
- short_answerはspeakable判定/通知/metadata、full_textは結果本文の発話、summaryはdeep contextと用途を分けた。full_textをoperatorが増やしてもTomoko側DTOが捨てていた接続漏れがあり、実smokeで45文字のshortに対し549文字のfollow-upまで届くことを確認した。
- 結果は短命cacheだけでなくPostgreSQL `research_results`へ保存し、再読・会話context用summaryを持つ。成功/失敗の短い通知も既存reply経路を使う。
- provider timeoutとMCP timeoutは別。world collectionは600秒へ拡大した記録があり、長時間jobは重複起動を避ける必要があった。
- rendered innerTextはMarkdown記法と同じではない。番号付きheading、引用符、provider UIの余分文字などを検証し、raw artifactを残してdeterministic fallback normalizerを用意した。

出典: [v1 MEMORY: Research / world observation](../v1/MEMORY.md)、[v1 README: Background Data Pipelines](../v1/README.md)、[informations](../v1/informations/)。

### Calendar / task / timerを別の正規データとして扱う

- iCalはbackground importしてDBから読む。private iCal URLは管理外configとし、promptや公開repoへ埋め込まない。
- `FREQ=YEARLY`等の未対応recurrenceをdailyへfallbackしたため毎日予定になる不具合があった。対応frequencyを明示し、未知はbase eventだけ。期間重複は半開区間 `start < window_end AND end > window_start` とし、前日終日予定を境界一致だけで含めない。
- 当初の過去1日+未来14日・max8件は月予定を落とし、今日から未来30日・max64件へ変更した。ただし大量contextを常に入れるとprefillと反復を悪化させる。
- 時計質問をcalendar cueにすると42件の予定が入り、calendar専用blockとlong-termにも重複して、反復長文→TTS巨大化→audio queue overflowになった。無音の原因はTTS engineではなく上流promptだった。
- taskはcreate/completeのみから始め、exact/normalized matchを先に使い、曖昧completeだけLLM候補id抽出+deterministic validator。unsupported update/cancelは勝手にrow変更しない。
- task prompt sliceはfast/normalで先頭10件、deep/reflectiveで多めという境界を作った。詳細記憶検索と残タスクの正確性は別問題。
- timer/alarmはDB scheduled rowをworkerがdue claimし、sessionへeventとして戻す。client setTimeoutやlocal assetに委ねない。
- dueは通常reply/initiative/research noticeより優先し、Tomoko自身が喋っていればcancel+stopして通知へ切替。ただし人間listening中は保持してidleで再投入する。
- output未接続ならfailed確定、繰り返し/ポモドーロ/連鎖は初段unsupported。通常候補のambient-only gateにtimerまで閉じ込めない。

出典: [v1 MEMORY: Calendar / task ledger / timer alarm](../v1/MEMORY.md)、[v1 README: Task Ledger](../v1/README.md)。

### thinker2: 生観測と解釈を分ける

- カメラ/画面/calendar/worldのbackground cognitionを、低遅延turn-taking shadow workerへ混ぜない。raw frame → observation → user context snapshot → candidateという別workerにした。
- raw画像は短命artifact、DBはstructured presence/activity/screen observations。retention初段はsourceごと最新100件のretained flagを残す方式で、古いfileの物理削除は別責務だった。
- presence/screenはlatest-frame優先。古いbacklogを全件推論すると「今いるか」が遅れるため、staleは推論せず古い未処理をskipする。
- present=falseとactivity labelの矛盾は、raw labelを捨てずsnapshot合成でawayへ丸める。raw observationとcoherent interpretationを別に残す。
- snapshot readinessはaway/do_not_disturb/low_intrusion_ok/chat_ok/needs_help_maybeのdeterministic ruleから始めた。LLM synthesisはoptionalで、観測欠落/失敗/elapsedをtraceに残す。
- screen/activity候補はsessionへ直接命令せず、既存candidate poolとfinal gateへ戻す。away/do_not_disturbから候補を出さない。
- calendar reminderは15分前/5分前/開始時のseed、source_key+windowでdedupe。timer/alarmの強い通知を同じ候補sourceで重複実装しない。
- 実camera初回frameがほぼ黒かった。30frame・各0.05秒のwarm-upで平均輝度約0.25から約60〜85へ改善し、実画像で確認した。capture API成功と見える画像を得たことは別。
- 当時Gemma VLMは空文字/特殊token/壊れたJSON、Qwen2.5-VLは一部JSONを返したが長いsummaryで再び崩れた。VLMをcoarse visual分類へ縮小し、OCR+front app/window title/browser metadataで文脈を補う二段構成にした。
- それでもJSON失敗があったため固定 `KEY=VALUE` 行の小schemaへ変更し、deterministic parserとJSON互換fallbackを置いた。3B/7Bともprocess完了を確認したが、画面意味の汎用精度は別の評価課題。
- LOGにあるOCR補正には `thinker2` という特定画面の文字証拠を優先する専用ruleも含む。inspection画面での成功を一般の作業理解能力と誤認しない。
- `perception_errors=none` はfallbackで処理継続した場合にも成立した。VLM parse成功・fallback利用・最終labelの正しさは別指標として残す必要がある。

出典: [v1 MEMORY: thinker2 T2.0〜9](../v1/MEMORY.md)、[v1 LOG: セッション56/57付近](../v1/LOG.md)、[camera capture](../v1/server/thinker/perception/os_capture.py)、[screen inference](../v1/server/thinker/perception/screen.py)。

<a id="knowledge-12"></a>

## v1: 構造変更と検証から得た知見

### packageを増やすことと責務を明確にすることは別

- v1は一枚sessionからfacade/state/reducer/effects/ReplyOrchestrator/dispatcher/event-runner/flow mapsへ分割し、読みづらさから一枚baselineへ戻した履歴を持つ。
- map-only guardがruntime moduleと並ぶと、実際には使われていないmap constantsがruntime wiringに見えた。知識の保存形式が、実装構造の複雑化を正当化するわけではない。
- `960be36` はpackage split前functional baseline、`b254d32` はsplit commitという記録がある。baselineの確認は377 unit pass+startup/GET200までで、実マイク品質調整済みではなかった。
- rollback時にはsplit後の実機能（ambient observer write、audio_control stop effects等）を失わないか個別に棚卸しした。古いcommitに戻せば「全部よくなる」とは限らない。
- 復旧後はlatency probe、retrieved carryover、payload coercion、request-id formatter、memory formatting等の小state/helperだけを1責務ずつ抽出した。final gate、DB write ordering、LLM/TTS ordering、audio hot pathは専用Phaseなしに動かさない方針だった。
- 「input→changer→state→demand→watcher→output→new input」は所有境界を読む語彙として有用だったが、各語を必ずclass/packageにすることとは別。
- 一方、最終sessionは4,012行、ContextSnapshotBuilderも1,058行のclassであり、「原則1file300行以下」との乖離が残る。再実装では、この矛盾を隠して機械的に旧ファイル構成をコピーしない。

出典: [v1 MEMORY: 10.12〜10.20 / monolith baseline](../v1/MEMORY.md)、[v1 ARCHITECTURE](../v1/ARCHITECTURE.md)、[session](../v1/server/session.py)、[context builder](../v1/server/gateway/context.py)。

### 競合と外部副作用

- state更新中のawaitを避け、短い同期的更新と外部I/Oを分ける。lock内ではstate確定だけを行い、WebSocket/DB/LLM/TTSは外へ出す。
- async結果にはsession/turn/chunk/context-build/request-idを持たせ、現在stateに合わない結果をowner側でdiscardする。runnerが古いsnapshotでspeakを判断しても到着時に再確認する。
- arrival/initiative/playback/transcriptの同時eventはsession内queue/drainで順序付けた。外部EventBusを増やす前に内部の所有と順序を明確にした。
- 複数edgeは観測音量最大、同値はrecency/device-idで正規発話元をdeterministicに選ぶ。duplicateは時間窓/device差/文字列類似度が主で、意味の近さだけで人間の繰り返し発話を消さない。hard interruptはduplicateより優先。
- edge/gateway protocolにはspeechのevent/transcript/device/timestampsを渡し音声bytesは渡さない。remote edgeのTTSはedge側。reconnect/backoff/heartbeat/health UI/長時間soakは未完として残った。

出典: [v1 MEMORY: concurrent hardening / stale / edge split](../v1/MEMORY.md)。

### 体験に近い測定と証拠

- backend単体のTTFT、最初の本文、first audio生成、WebSocket first binary、ブラウザdecode/再生開始、再生終了を混ぜない。
- `_tools/smoke_ws_voice_latency.py` はsay音声を16kHz mono float32/512samplesで実 `/ws` へ送り、最後に無音chunkでVAD endを起こす。基準はlast voice chunk sentからtranscript/reply text/first binaryまで。ブラウザdecode/playbackは含まない。
- 初期3turnのvoice-end→first binaryは7819.0/8030.0/9359.6ms。6/11の別条件では4348.9/4576.3/4664.3ms。改善はあるが、800msのE2E目標を達成した記録ではない。
- inter-turn1500msでもbarge-inが残ったため、完全に聞き終わった条件の比較には3000ms以上も試す必要があった。harnessの待ち方が測定結果を変える。
- `server-debug.log` は状態/transcript/reply/playback/gate、`backend-trace.jsonl` はbackend call、`system-metrics.jsonl` はGPU/ANE/power/memory/thermalと用途を分け、同一時刻で原因を突き合わせる。
- mactopはoptional providerとし、不在時にruntimeを壊さずavailable=falseを記録した。監視成功と会話成功は別。
- shared DB integrationはglobal top-Nにfixtureが現れる前提を捨て、挿入id/device/tagsで直接確認し、自己fixtureのみcleanupする。実データが増えるほど壊れるtestを機能不具合と混同しない。
- persona overlay本文の特定語をunitで固定すると調整のたび壊れるため、repo本文はUTF-8で読めること、injectionの挙動はtmp fixtureで検証する方針にした。
- say perfの800ms閾値越えは実行jitterもあり、traceと再測定で切り分けた。失敗したtestの期待値を機械的に緩めるのではなく、何を測ったかを確認する。
- 発話を繰り返して自発性thresholdを調整する負担を下げるため、DB会話+shadow log+candidate snapshotのoffline initiative simulationを作った。UI sliderは生成済みpressureの再加算だけでは効かず、event/paramsから再積分する必要があった。
- runtime clientへ判断を置かない原則と、offline分析UIでの再計算は用途が異なる。sandboxを本番接続済みと誤認しない。

出典: [WebSocket音声smoke](../v1/_tools/smoke_ws_voice_latency.py)、[v1 MEMORY: latency / integration / initiative simulation](../v1/MEMORY.md)、[v1 README: logs/tests](../v1/README.md)。

### 起動・停止・運用

- background常駐processをMakefileの直列dependencyに並べると後ろへ進まない。onceは直列、watchは別terminal/tmuxで起動する。
- LLM `/v1/models` とVOICEVOX `/version` のreadiness後にserverを起動し、startup warm-upの依存を先に満たす。windowがあるだけでreadyとしない。
- `tee`を含むpipelineはtmux killだけでは子processが残った。windowへCtrl-C後にsession停止し、別に起動したLLM runtimeも対応するstop処理で止める。
- background world一括処理はcollect→ingest→interpret→thinker順。collection timeoutが長いので定期運用では重複実行を防ぐ。
- モデル重みはrepo同梱せず取得手順を分けた。licenseの記録は当時の依存一覧であり、再配布時には選び直したartifactの条件を再確認する必要がある。
- 生成PNGが透明に見えてもRGB市松模様が焼き込まれていた。alpha channelの実在を確認する。ファイル拡張子・見た目・生成指示だけでは透過を保証しない。

出典: [v1 README: setup / common commands](../v1/README.md)、[v1 Makefile](../v1/Makefile)、[v1 MEMORY: tmux / assets](../v1/MEMORY.md)。

<a id="knowledge-13"></a>

## v1: 再実装時に参照する資産と未完事項

| 資産 | 参照先 | 再利用する価値 |
|---|---|---|
| 判断・実測・反証の履歴 | [MEMORY](../v1/MEMORY.md)、[LOG](../v1/LOG.md) | 閾値変更、採用撤回、実機不具合の理由 |
| 設計と完了条件 | [ARCHITECTURE](../v1/ARCHITECTURE.md)、[PLAN](../v1/PLAN.md) | 状態所有、DB原本、残課題。文書の時点差に注意 |
| DTOとschemaの境界 | [shared/models.py](../v1/server/shared/models.py) | turn/session/candidate/advisory/contextの対応付け |
| VAD/STT前処理 | [edge/pipeline](../v1/server/edge/pipeline/) | pre-roll、signal gate、transcript品質の責務分離 |
| 音声backend比較 | [inference](../v1/server/shared/inference/)、[bench_stt_backends](../v1/_tools/bench_stt_backends.py) | model API差、warm/cold、同時負荷比較 |
| WebSocket E2E測定 | [smoke_ws_voice_latency](../v1/_tools/smoke_ws_voice_latency.py) | 実音声+無音chunkを入れる3turn測定 |
| LLM prompt比較 | [bench_runtime_thinkfast_conversation](../v1/_tools/bench_runtime_thinkfast_conversation.py) | 実DB/実config/実thinking promptで比較 |
| 小型反応/LoRA | [bench_short_reaction_mlx](../v1/_tools/bench_short_reaction_mlx.py)、[loras](../v1/loras/) | 学習template、adapterとfuseの品質差 |
| 記憶の露出制御 | [MemoryGate](../v1/server/session_memory_gate.py)、[context](../v1/server/gateway/context.py) | retrieval/use分離、source quota、budget trace |
| 非turn相槌 | [gesture_audio](../v1/server/gateway/gesture_audio.py) | 通常playback/echo stateを汚さないlane |
| turn-taking解析 | [shadow_bench](../v1/scripts/shadow_bench.py)、[analyze_turn_taking_v2](../v1/server/tools/analyze_turn_taking_v2.py) | 予測値と実測値を区別するtimeline |
| 外界観測 | [thinker/perception](../v1/server/thinker/perception/)、[informations](../v1/informations/) | raw/observation/interpretation/候補の分離 |
| 回帰の例 | [unit tests](../v1/tests/unit/) | 非同期順序・stale・再生・メモリ・DB境界の具体的ケース |

未完・再検討として残す事項:

- 800ms E2E目標は、単体backendの100〜400ms測定から達成扱いにできない。full live promptのprefill、VAD待ち、同時負荷、TTS供給、ブラウザ実再生を含めた再測定が必要。
- TT-v2.10dのfusion実会話突合とtrigger切替、Gemma mlx-vlm側prefill API、実process間KV所有の統合は未完。
- 未完STT断片をobserverへ落とすだけでは内容が次segmentへ戻らない。pending utterance統合とノイズによるreply cancel誤爆は未解決の設計トレードオフ。
- 長寿命central Session、複数room/device/personの実運用、edgeのreconnect/backoff/heartbeat/soakは完了していない。
- MaAI channel2はserver send timestamp基準。正確な再生時刻との同期、相槌音の回り込み、gestureとfloor-takingの体験評価は継続課題。
- pregenerationのmulti-chunk schemaはあっても初期gateway消費はfirst chunk cache。schemaの存在を完全音声事前生成の実運用証拠にしない。
- thinker2の画面理解は特定画面向けOCR補正やfallbackを含む。全般的な人間活動推定精度、誤介入率、stale frame運用、raw画像物理cleanupは別に評価する。
- archive内には当時のroot相対パス、隣repo、モデルcache、DB、private URL、録音artifactへの依存がある。コードが保存されたことと、独立実行環境が保存されたことは別。

この節の知見は新設計の強制ではなく、過去に何が動き、何が壊れ、どの条件で改善したかを再検証するための材料である。

<a id="knowledge-14"></a>

## 現行実装（アーカイブ v2）の構造から得た知見

### 史料の読み方と、完成扱いしてはいけない部分

v2 は元々 repository root に作られた二度目の実装である。`ARCHITECTURE.md` 冒頭、`PLAN.old.md`、`PLAN.md` 前半、後日の追記では設計が変わっている。文書の先頭だけを読むと「main LLM は hot-path」「DB/NOTIFY が hot control」「STT は Apple」「scheduler が通常経路を裁定」と読めるが、その後それぞれ **main LLM は tomoko-process、会話 control は internal WS、既定 STT は WhisperKit、通常裁定は二段 gate**へ移った。過去の判断を消さず追記したために古い説明が残っている。新実装で継承する時は、日付、後続の否定・上書き、実コードを一組で読む。

`f.md` の 2026-07-03 時点の gap 表は出発点であり、後半の 7 月 4 日のチェック済み項目に改善の経緯がある。さらに 8 月の `PLANS/openai.md` と `repository-map.md` は、7 月の PASS が証明していないことを棚卸ししている。特に **マイク受信が継続すること、STOP order が出ること、生成途中の TTS を即時停止できること、ブラウザで可聴音が停止することは別の事実**である。

`_docs/v2.md`、`_docs/v2-alpha.md`、`_docs/v2-2.md`、`_docs/thinkerv2.md` の「v2」は、v1 の turn-taking/thinker を拡張する設計の名称でもある。`TomoroSession`、`utterance_candidates`、`arrival_candidates` を記述する箇所を、今回アーカイブした root-v2 の `TomokoConversationCore` や `v2_candidates` の実装と混同しない。shadow lane、offline sandbox、floor-holding の構想には未実装の案が含まれる。

根拠: [ARCHITECTURE](../v2/ARCHITECTURE.md)、[PLAN](../v2/PLAN.md)、[旧 PLAN](../v2/PLAN.old.md)、[f.md](../v2/f.md)、[8 月の実装棚卸し](../v2/_docs/openai-plan/repository-map.md)、[旧 turn-taking v2 構想](../v2/_docs/v2.md)。

### 役割と状態所有者を分ける

実装で安定した方向は、「音声を扱う身体」と「人格・会話判断」を分離することであった。

| 部分 | 所有するもの | 他へ渡すもの・持たないもの |
|---|---|---|
| hot-path-process | public `/ws`、mic bytes、VAD/STT、短命バッファ、TTS、audio chunk、physical playback generation、append queue | STT observation と TurnMaterials を Tomoko に渡す。人格、記憶、話してよいかの最終判断を持たない |
| tomoko-process | final の採用、session 境界、context、persona 材料、pressure、gate、main LLM 本文生成、speech-order、initiative、sense followup | 音声の物理実行を hot-path に命じる。LLM 出力をそのまま制御命令にしない |
| think-process | summary/world/calendar 等を CandidateSeed/CandidateRecord に正規化すること | 発話命令は出さず、候補とスコアを DB に置く |
| info-aquire-process | world/calendar の取り込み、検索 sense の処理、構造化 world rows | raw 外部情報を直結せず、filter と candidate 化を経由する |
| user-status-aquire-process | screenshot/OCR/OS metadata/camera presence の観測 | observation を渡す。floor や割り込みの最終判断はしない |
| summary-process | ended session から索引 summary/embedding を作る処理 | 原会話を書き換えない。hot-path に要約処理を入れない |
| browser | microphone capture、入力/出力 device routing、WAV 再生、音量、物理 stop/fade、JSON 表示 | 発話可否、人格、reconcile、retry、session 判断は持たない |
| PostgreSQL | durable な記録、audit/replay、session/context/candidate/world の永続化 | 高頻度 VAP/MaAI 生フレームの配送路にしない |

public endpoint は `/ws` 一本を維持し、機能は message type を増やして表現した。`/client/*` は static delivery であり、runtime 用 REST endpoint を増やすこととは別である。内部 `/internal/hot-path` WS は process 間制御線で、ブラウザの endpoint 制約とは区別している。

ここで「クライアントにロジックを置かない」は Web Audio の物理停止まで禁止する意味ではない。既に受信・予約した `AudioBufferSourceNode` は server から消せないため、source を保持して `stop()` し、再生スケジュールを reset する責務は browser に必要だった。入力と出力の device selector も client-only の配線であり、会話判断とは分けられる。

実装はこの役割分離を完全に達成したわけではない。`TomokoConversationCore` は 1,600 行規模に成長し、重い LLM と sense、state 更新が一つの class に集まり、`RemoteTomokoWsCore` の lock と internal WS receive loop に直列 await が残った。**ディレクトリや process を分けただけでは、遅い仕事から制御が独立するわけではない**ことが次の作り直しに残る重要な教訓である。

根拠: [README](../v2/README.md)、[責務の追記](../v2/ARCHITECTURE.md)、[会話 core](../v2/server/tomoko/conversation.py)、[internal WS](../v2/server/tomoko/realtime.py)、[proxy](../v2/server/hot_path/ws_control.py)、[client](../v2/client/main.js)。

### DTO 境界と hot loop の例外

層間契約は `server/shared/models.py` に集約した。STT observation、durable utterance、session/context、pressure/gate input/output、SpeechOrder、audio output を DTO にし、`trace_id` で観測起点を追う。`str`、`bytes`、`ndarray` を場当たり的に層間へ流すことを避ける一方、VAD の高頻度 hot loop だけは primitive のままにした。31 回/秒程度で dataclass、UUID、`datetime.now()` を生成する必要はなく、発話終了などの境界で DTO に包む。大量に流れる token/audio event DTO には `slots=True` を使う方針である。

新しい機能の意味を判定値と transport payload に混在させないことが重要だった。例えば partial は捨てられる観測、final は採用候補、durable utterance は採用された記録であり、同じ「認識文字列」でも保存上の意味が異なる。

根拠: [DTO](../v2/server/shared/models.py)、[開発規約](../v2/AGENTS.md)、[final 採用境界](../v2/server/tomoko/main.py)。

### Materials → Pressures → Gates は意味の違う三段階

当初は `TurnOpportunitySnapshot` / `TurnSignalAggregator` / `SpeechScheduler` を中心に整理したが、raw input と判断を混同しないよう 2026-06-20 に語彙を整理した。

- **Materials**: 観測と既知の材料。RMS、silence_ms、user_speaking、speech_probability、playback_active、VAP の p_yielding、MaAI react/emo、STT partial、外部情報、予定、記憶関連性、persona 傾向。
- **Pressures**: 材料から決定論的に計算した中間量。返せそうか、話し込みたいか、自然か、外界情報を今届けやすいか。
- **Gates**: 生成を始めるか、生成済みの候補を実際に発話させるかを決める裁定器。

同じ silence_ms や p_yielding を複数 pressure が読むことは正しい。silence は DialogueTurnPressure では「相手が譲ったか」、NaturalSpeechPressure では「間を埋めて自然か」、WorldPressure では「外部情報を差し込めるか」を意味する。値を共有しても中間量の意味を一つに潰さない。

現在の pressure 実装は以下の性質を持つ。

1. `DialogueTurnPressureModel`: semantic saturation、text の存在、final bonus、yielding/silence から readiness と opportunity を作る。p_yielding 欠損は **0** 扱いであり、「観測できないから譲った」とはしない。`turn_opportunity = clamp(max(yielding, silence_ms/1200) - interruption_risk*0.4)`。user_speaking 時の interruption risk は speech_probability と `(1-yielding)` から作る。yielding 起因と silence 起因は breakdown に別々に残す。
2. `NaturalSpeechPressureModel`: MaAI backchannel、無音、persona の empathy/restraint 等を軽い反応の自然さへ変換する。固定相槌を main LLM の通常回答と混同しない。
3. `MotivationPressureModel`: talkativeness/curiosity/restraint/interrupt_tolerance に conversation_heat と topic_continuity を加える。`threshold_shift` は最大 0.22。**この shift を score へ足すのではなく fire/emit/append 閾値と replace margin を下げる**。initiative_desire 等そのものが gate score に寄与することとは別の話である。
4. `WorldPressureModel`: calendar urgency、external result、candidate、followup、memory を importance/urgency/relevance/deliverability に落とす。user absent なら importance/urgency/deliverability/candidate_pressure を 0 にし、自発通知を抑える。ユーザーが STT で直接話しかけた返答は DialoguePressure で成立できるため、presence 判定だけで直接応答まで停止しない。

通常の経路は `Materials → pressure models → LlmFireGate → LLM → PreparedSpeechCandidate → SpeechEmissionGate → SpeechOrder` である。古い `SpeechScheduler` は互換 DTO/テストとして残るが、通常発話で `SpeechScheduler.decide()` を呼ぶことが主契約ではない。LLM 前に main_reply/initiative/world_summary のような intent enum を gate の判定として混ぜず、`fire / do_not_fire / cancel_or_replace_pending` だけを返す。どの圧力が効いたかは `score_breakdown` に残す。

`SpeechEmissionGate` は生成済み候補の priority/freshness/semantic confidence、turn opportunity/naturalness/motivation/world deliverability と、interruption/misunderstanding/rejection/fatigue/restraint を競合させる。出力は `emit_now / append_after_current / replace_current / hold / suppress / stop`。stop が最優先で、現在発話がある場合は current score + margin を超えたら replace、append threshold 以上なら append、それ以外は hold とする。

ただし **gate が二段存在することと、LLM 待ちの後に最新材料で再判断することは別**である。8 月の棚卸しでは、fire 前に捕捉した材料と pressure を emission に再利用し、LLM 待ち中のユーザー再発話を反映しない gap が確認された。最新 snapshot、age、generation を送出直前に読む提案は O2 であり、実装完了扱いしない。

根拠: [pressure 実装](../v2/server/tomoko/pressures.py)、[二段 gate](../v2/server/tomoko/gates.py)、[MEMORY の G5/G6](../v2/MEMORY.md)、[送出時再評価の未達](../v2/PLANS/openai.md)。

### early reply、誤認識、reconcile の経験

partial を早く使うと速くなるが、同じ VAD segment 内で誤った partial、修正 partial、final がそれぞれ追加発話になると会話が破綻する。実際に一つのユーザー発話から複数 append が生じた。対策は次のように段階的に変化した。

- partial の semantic saturation だけでなく、pressure 合成 score も開始確認への入口にした。両方が低ければ待つ。
- 一般の partial は似た high partial が 2 回連続した時に通す。後に request-complete partial は初回 confirmation で通す fast-start を追加した。2 回確認を全入力への絶対規則にしない。
- user_speaking が強く、p_yielding が低く、silence が短い間は full reply を止める yield guard を追加した。early feedback と完全回答を分ける方が安全だった。
- incomplete topic partial は固定 acknowledgement を返し、final の本回答で replace できるよう acknowledgement を active partial reply の内容 reconcile 対象から外した。
- high motivation、low semantic の場合も full answer にせず、短い撤回可能 interjection に限定した。記録上の条件は threshold_shift >= 0.12、p_yielding >= 0.7。これは意味を理解していない段階で断定的な回答を始めないためである。
- partial 起点の substantive reply と final が同内容なら、final を durable 保存しつつ二重応答は抑える。wake word、filler、表記揺れの正規化はここで効く。
- final が active partial と大きく乖離したら、既存返答を前提に suppress せず `replace_current` で訂正する。古い active partial を clear し、後着の stale partial を final と照合する。

STT 結果は UI で見えたことと会話採用されたことを分けた。空 final や既知 hallucination 辞書に当たる final は observation として残せるが、`TomokoProcessCore.adopt_final_observation()` が durable utterance/prompt への昇格を拒否する。空文字を通常ユーザー発話として採用すると generic reply が発生し、自己応答ループに見える挙動になった。

この問題に対し一度「TTS WAV duration + grace 中は mic を捨てる」echo suppression を入れたが、ヘッドセット前提では実音の回り込みがなく、mic を捨てる方が barge-in/同時発話を壊すと判断して否定した。**発話ループを見ただけで音響 echo と断定せず、blank STT → durable → prompt という因果を調べる必要がある**。

append dedupe は「Tomoko が発話中/queue active な時の重複追撃」を抑えるために使う。duplicate_score >= 0.85、continuation_score <= 0.45、new_intent_score <= 0.45、time_delta <= 5 秒が記録上の条件で、LLM 前に判定する。suppressed final 自体は durable observation として残し、LLM/TTS と in-memory prompt history に進めない。後の修正で **idle 時は suppress しない**ことを固定した。ユーザーが待機中に自然に言い直す機会まで削るのは、この guard の目的を超える。artifact 欠損/無効化時は fail-open。

根拠: [MEMORY の partial/reconcile/dedupe 判断](../v2/MEMORY.md)、[会話 core](../v2/server/tomoko/conversation.py)、[実装経緯と回帰](../v2/f.md)、[yield guard](../v2/_docs/260704.md)、[speech flow テスト](../v2/tests/unit/test_v2_speech_order_flow.py)。

### SpeechOrder、cancel、replace、append の契約

hot-path が受けるのは「この prompt を LLM に掛けよ」ではなく「この text をこの mode で声にせよ」という `SpeechOrder`。通常返答、自発発話、予定通知、言い直し、停止を同じ契約にしたことは再利用しやすい。

- `replace_current`: 保護条件に当たらない通常経路では generation を進め、append queue を消し、新 text の TTS を開始する。ただし runtime factory は `protect_inflight_replace=True` を指定しており、current order がある間の REPLACE は generation を変えず `queued=True` で返る。この分岐では order を append queue にも保存しない。したがって REPLACE の受領だけで即時置換・後続実行を保証できない。ブラウザ側の置換処理は既存音声を fade/cut し、記録上 80ms fade + 150ms gap を使った。
- `append_after_current`: 現在 order が active なら投入順の `append_queue` list へ保持する。ただしこの list を pop/drain して自動実行する処理は現行 server 内に確認できない。通常 deferred path の単一 sender が結果を逐次処理することと、この list の消化は別である。priority による再順序付けや後続再生を保証する queue とみなさない。
- `stop`: generation を進め、current order/current score/append queue を clear する。
- TTS backend が後から chunk を返しても、取得時点で captured generation が current かを確認し、古ければ discard する。
- complete WAV chunk contract を維持し、不完全 WAV/生 PCM を browser へ流さない。

UI Stop には二つの停止が必要だった。browser の予約済み音声 source を止めることと、server の generation を無効化してこれから来る旧音声を捨てること。ACK だけ返す Stop は停止の実装ではない。

別 process では `cancel_order` がそのままでは executor に渡らず、実行可能な STOP `SpeechOrder` に変換する helper が必要になった。また playback 終了後も `current_speech_order` が残ると次の普通の返答が append になるため、`playback_state(False)` で core を clear する回帰を入れた。

しかし現行 executor は **TTS generator task 自体を cancel しない**。さらに public WS の単一 result sender が一つの `execute_stream()` を最後まで await するため、音声由来 STOP/REPLACE が同じ result queue に入っても、前の TTS 完了まで executor に届かない。UI Stop が即時であっても音声 STOP が即時とは限らない。generation guard は「無効化できた後の chunk」の防波堤であり、「無効化要求が速やかに到達する」保証ではない。

旧 overlap scenario は最初の `tts_result` を待ってから次発話を入れていた。`tts_result` は生成/送信完了後なので、PASS は in-flight preemption を証明しない。binary audio が履歴に一件あるだけの overlap 判定も、実再生中の割り込み保証にならない。再実装時の改善案は、独立した高優先 control mailbox、generation ごとの cancellable TTS task、single ordered writer の enqueue と送信直前双方での validity check、backend が cancel を無視する場合の guard である。これらは `PLANS/openai.plan.md` の O1a 以降の **未実装提案**として保存されている。

根拠: [executor](../v2/server/hot_path/speech_executor.py)、[WS sender](../v2/server/hot_path/app.py)、[STOP 変換](../v2/server/hot_path/ws_control.py)、[client playback](../v2/client/main.js)、[現行 cancel chain](../v2/_docs/openai-plan/repository-map.md)、[preemption の課題](../v2/PLANS/openai.md)。

### origin、判断世代、再生世代を一つにしない

O0A-01/O0A-02 は実装済みだが、O0A-00 の repository map/contract には追加前の状態がそのまま残る。最新 source と LOG で補正する必要がある。

- `trace_id`: observation → SpeechOrder → AudioChunkOut の因果起点。既存の意味が origin_trace_id と一致していたため重複 field を増やさなかった。
- `decision_generation_id`: Tomoko 所有の判断サイクルの monotonic integer。`handle_observation` と `handle_initiative_tick` 冒頭で instance field を bump する。hot-path は opaque として扱い、書き換えない。
- `playback_generation_id`: hot-path executor 所有。STOP/通常合成の execution result に入る。まだ queue に入っただけの append result は `None`。SpeechOrder に playback generation を持たせない。
- `ResponseKind`: `backchannel / acknowledgement / content / correction / followup`。非 STOP かつ text 非空の SpeechOrder では必須。本文を後から LLM で分類するのではなく producer で付ける。通常回答/予定付加/continuation は content、乖離 final 訂正は correction、sense kick/partial ack/interjection は acknowledgement、sense の後続結果は followup。

残る不完全な点も保存する。fixed backchannel は SpeechOrder を経由せず hot-path の `BackchannelEmission` から直接流れるため、backchannel の分類 metadata は O0A-03 に持ち越された。非同期 sense followup は kick 元で捕捉した decision generation ではなく、**order 構築時点の core の current generation**を付ける。stale/supersede に使うには因果意味を再設計する必要がある。DB の generation column もこの段階では追加しておらず、row parser が無い値を `None` として受けるだけである。internal `cancel_order` の STOP 再構築では decision generation が保持されず `None` になる。field があるだけで generation に基づく cancellation/freshness 全体が完成したとみなさない。

根拠: [LOG の O0A-01/O0A-02](../v2/LOG.md)、[SpeechOrder DTO](../v2/server/shared/models.py)、[世代テスト](../v2/tests/unit/test_v2_generation_owner.py)、[種別テスト](../v2/tests/unit/test_v2_response_kind.py)、[未実装後続計画](../v2/PLANS/openai.plan.md)。

### DB origin から WS origin へ移した理由と限界

初期 v2 は PostgreSQL row が本体、LISTEN/NOTIFY は UUID 一個の wakeup という形にした。JSON 本文を NOTIFY payload に詰めず、許可 channel/UUID を `v2_notify_id()` で制約した。schema は v1 を破壊せず `100_v2_core.sql` に additive で作った。DB split では hot-path が observation を保存/notify、tomoko-db worker が判断/order を保存/notify、hot-path が order を読み実行する。NOTIFY 欠落には同じ trace の未実行 order の短時間 polling を備えた。

最初は発話ごと・通知ごとに LISTEN/write/read/work connection を開き、不要な接続コストを積んでいた。process-lifetime connection を warm してから使う形に修正した。2026-06-18 の fake DB split 内部 total 15.8ms、notify→order 13.5ms、order→audio 2.3ms はその条件の実測である。DB 単体 microbench（300 samples、20 warmup）の insert+notify+select p95 は 1.049ms。これは **実 STT/LLM/TTS を含む会話 latency ではない**し、当時の live DB split が partial を送っていた証明でもない（hot-path は final を選んでいた）。

次に 200ms 程度で latest TurnMaterials を internal WS に流し、最終的に STT/control も WS origin へ寄せた。VAP/MaAI の生フレームは hot-path 内に置き、平滑化・集約した materials だけを渡す。WS では `stt_observation / turn_materials / playback_state / speech_order / cancel_order / ack` を扱い、DB split は fallback/比較用として残した。fake process smoke の total 46.0ms 対 DB split 60.5ms は制御線比較としてのみ扱う。

現行 WS path は DB 失敗を捕捉して `persist_failed` を記録し、会話 WS を落とさない。しかし **fail-open と nonblocking は同義ではない**。`hot_path_realtime` は `handle_observation` の後、`await _persist_result_if_enabled(result)` を完了してから ack/order を返す。候補・summary refresh も前で await する。DB が成功しつつ 5 秒遅い場合、その遅さは音声送出を gate する。さらに現行 WS の `_persist_result_if_enabled()` は結果ごとに `AsyncConnection.connect(..., autocommit=True)` を開く。DB split で process-lifetime connection にした過去の改善が、WS persistence 経路まで自動的に継承されたわけではない。将来の canonical persistence は durable/idempotent retry と live delivery を分ける提案であり、単なる fire-and-forget や best-effort 破棄へ変えてよいという知見ではない。

根拠: [core schema](../v2/docker/postgres/init/100_v2_core.sql)、[NOTIFY helper](../v2/server/shared/notify.py)、[DB worker](../v2/server/tomoko/db_worker.py)、[WS realtime](../v2/server/tomoko/realtime.py)、[MEMORY の DB/WS 比較](../v2/MEMORY.md)。

### bounded queue だけでは重要イベントを守れない

8 月棚卸しの現行 overload policy は次の通りだった。

| lane | 容量 | 実挙動と注意 |
|---|---:|---|
| partial lane | 256 | oldest を drop、同 generation の更新を coalesce |
| final lane | 4 | full 時 oldest final を drop。重要 final を守る契約ではない |
| connection result queue | 無制限 | queue age/size/drop 観測なしで TTS 待ちを積める |
| executor append queue | 無制限 list | 投入順で保持するが、自動 drain の実装はない |
| TurnMaterials | latest value | latest-wins。古い全フレームの保存は不要 |

partial/final worker を public receive loop の外へ逃がしたことは入力継続に有効だったが、final worker は partial idle を最大 1.5 秒待ち、internal proxy は単一 lock、Tomoko receive loop は LLM/DB を直列 await、結果送信は TTS を直列 await する。「async を使っている」だけでは head-of-line blocking を除けなかった。

再実装で検討すべき分類は、latest-wins にできる materials、同 utterance revision を coalesce できる partial、drop してはいけない final/STOP/session boundary/canonical work、期限切れで捨ててよい sense result/candidate を分けることである。これは後続 O1b/O1c の計画であり、既に no-drop/backpressure が達成されたとは記録しない。

根拠: [queue/lock/await map](../v2/_docs/openai-plan/repository-map.md)、[baseline contract](../v2/_docs/openai-plan/o0a-contract.md)、[hot-path lanes](../v2/server/hot_path/app.py)。

### session、history、summary を別物として保存する

session ID は websocket 接続 ID や一発話の trace ID と異なる。DB split では **採用可能な final**を durable にする時に Tomoko が open session を読み、無ければ作る。last_activity_at から idle gap を超えていれば旧 session を `close_reason='idle_gap'` で閉じ新 session を作る。継続中は新規 session を作らず last_activity_at の UPDATE だけ行う。session の境界を保存しないと、後の要約、記憶、評価の単位を安定して復元できない。

会話履歴には user だけでなく Tomoko 発話も speaker 付きで残す。current utterance を過去履歴にも追加すると prompt 内で二重になるため、現在発話は末尾の一箇所に置く。`ContextSnapshot` は recent history、summary、calendar、user-status、candidate をまとめた派生読取りモデルであり、原本ではない。calendar は builder instance 内で session ごとに 60 秒 TTL cache を持つ。main prompt の recent history は直近 8 件で、当時「active prompt が無制限に伸びている」という評価は不正確だった。

summary runner は `ended_at IS NOT NULL` で未要約の session を読み、utterances を時系列に集める。summary と embedding に同じ summary UUID を使い、同一 DTO/ID の再 INSERT は `ON CONFLICT DO NOTHING` で重複しない。ただし `summarize_session()` は生成ごとに新 UUID を払い出し、未要約の選択は NOT EXISTS、DB の session_id に UNIQUE 制約もない。別 worker の同時生成や別 UUID での再生成を防ぐ保証ではない。summary_text は `keyword: conclusion`。summary は検索・candidate 化のための **索引**であり、原会話を置換・削除するものではない。

ただし、現行の summary は高品質な LLM 要約ではない。`summarize_session()` は全 utterance を空白結合し、keyword を先頭空白区切り語、conclusion を先頭 80 文字として切り出す。`embed_text()` は CRC32 で文字 bigram を 64 bucket に入れ L2 正規化する、字面/話題語重なり用の軽量ベクトルである。Python `hash()` の process ごとの乱数 seed を避けた点は再現性に有用だが、**意味 embedding ではない**。related summary top-3 の recall は実装されていても、semantic memory 理解や 31B summary モデル利用が完成したとは言えない。現行 `rank_summaries_by_relevance()` は cosine >= 0.05 の候補を降順に取り、関連が一件もなければ入力の先頭 top-3（新しい順）へ fallback する。旧 embedding と次元が合わない行は score 0 にするため、migration 中の混在で例外にしない一方、「返った記憶は全て query に関連する」という保証もない。

人格・用語集の versioned JSONB snapshot はプロジェクト全体の原則だが、root-v2 で確認できるのは `PersonalityMaterials` と pressure/fixture の境界である。v1 の成熟した人格・用語集機構が名前を変えて自動移植済みと推定しない。active session の richer context、compaction、canonical source reference、version/parent、request 境界の atomic cutover は O6 の提案であり未実装。

根拠: [session assignment](../v2/server/tomoko/db_worker.py)、[context](../v2/server/tomoko/context.py)、[summary の実体](../v2/server/summary/main.py)、[recall の実体](../v2/server/tomoko/recall.py)、[summary/recall の計画と実績](../v2/_docs/260704.md)、[core schema](../v2/docker/postgres/init/100_v2_core.sql)、[active context 提案](../v2/PLANS/openai.plan.md)。

### planned text を「実際に話した原本」にしない

現行 `_persist_result()` は STT、session、user utterance、saturation、scheduler decision、prompt request、speech order を保存し、durable user utterance と speech text がある場合、Tomoko の planned text も assistant durable utterance として保存する。followup も同様である。これが browser playback 前に走るため、**未送信/未再生/途中 cancel の全文が「話した」履歴に入る可能性**がある。

browser は chunk/order/generation に対応した `playback_started / playback_ended / buffered_until / client_event_seq` を server へ返していない。現在の playback_active は server executor の current_order 由来なので、server 合成が終わっても browser の future schedule が再生中というずれを表現できない。

次の作り直しで重要な分離案は、更新可能な live view（utterance ID + revision）、final user canonical record、assistant delivery segment（planned/sent/started/completed/cancelled）、raw observation/emission audit である。summary/memory は canonical からのみ導出し、固定相槌と cancel 済み全文を独立した実発話として覚えない。client は観測だけ返し、どの delivered range を canonical に採るかは server が所有する。これは O3 の未実装設計であり、このアーカイブの DB が既にこの品質を持つとは扱わない。

根拠: [実際の保存順](../v2/server/tomoko/realtime.py)、[baseline persistence contract](../v2/_docs/openai-plan/o0a-contract.md)、[live/canonical 分離提案](../v2/PLANS/openai.md)。

### candidate、外界情報、presence の結線

think-process の最小責務を「CandidateSeed へ正規化して CandidateRecord にする」と決めた点は境界として有用である。summary/calendar/world の source/source_key を冪等 key とし、DB では priority/urgency/intrusion/maturity/candidate_score/lifecycle/context_tags を upsert する。runtime は active かつ未期限切れを candidate_score、urgency、priority、created_at の順で読む。

候補には二つの使い道がある。本文は prompt の `VOLATILE_RECALL` に入り、最大 candidate_score は `WorldMaterials.candidate_pressure` となる。「context に入れたから発話する」のでも「pressure が高いから本文を知らずに発話する」のでもなく、内容と発話動機へ別々に反映する。

world info は confidence、stale、sensitive、private、do_not_speak を filter してから candidate にする。補助候補の DB refresh 失敗では会話を止めず、`candidate_refresh_failed` を残して直前 candidate を保持する。ただし「保持しているから今も新鮮」とは別問題で、TTL/freshness の送出時検証が必要になる。

無音中の自発発話は空文字 STT を偽造せず、`TomokoConversationCore.handle_initiative_tick()` を呼ぶ。active candidate、silence、speech_probability、presence、current speech/playback を見て suppress できる。発話可能な時だけ initiative prompt、既存二段 gate、SpeechOrder へ進める。tick の判断所有者は Tomoko であり、browser ではない。

calendar は最初 provider fixture で reply → calendar append の契約を検証した。通知済み key を dedupe するため、real scenario は reset を先に行い一時 calendar provider を挿す必要があった。user absent なら calendar followup append は出さない。

周辺 chain の確認済み範囲は区別する。7 月の integration は **fake world/calendar → v2_world_* → think materializer → v2_candidates → Tomoko refresh → initiative** の DB chain と、実 OCR fixture image の動作を証明した。これは live Google Calendar/あらゆる外部 world source が常時運用できることの証明ではない。info fixture の deterministic UUID と source/source_key upsert は heartbeat の繰り返しで row を増殖させないために使った。

local DB は smoke を繰り返すと candidate が育つ。integration が最新上位12件だけ見て今回の summary を探すと、既存候補に押し出され flaky になった。テストは今回追加した source/identity を広い取得範囲で確認するよう修正された。world source_key が interpretation row UUID になる境界も、入力 source_key をそのまま期待すると見誤る。

根拠: [candidate builder/store](../v2/server/think/main.py)、[DB bridge](../v2/server/tomoko/db_bridge.py)、[world info](../v2/server/info/main.py)、[MEMORY の Step7](../v2/MEMORY.md)、[integration](../v2/tests/integration/test_v2_db_schema.py)。

### sense：短い受領反応と遅い仕事を分ける

`_docs/260704.md` の取り組みでは、定期的な push だけでなく会話からの pull を追加した。`v2_sense_requests` に kind/query/trace/status/result/requested/claimed/completed を保存し、worker が `FOR UPDATE SKIP LOCKED` で claim する。screenshot/world_search の cue で sense を kick し、短い acknowledgement order を返し、結果は background task が受けて `poll_orders` 経由の followup append で届ける。

screenshot 結果は UserStatusObservation へ、world search 結果は最大3件の CandidateRecord へ変換して context に入れる。空検索結果/検索失敗には短い failure followup を返す。検索 backend 未設定の場合は kick 自体を無効にし、「調べる」と言ったまま無音にならないようにした。

world search backend は任意コマンドに query を渡し JSON items を返させる境界で、実装記録には `world_search_perplexity.py` が sibling の tomoko-research-operator、CDP Chrome :9000、ログイン済み Perplexity を包む adapter がある。1 回 retry、sense 待ち120秒、worker subprocess150秒、followup poll140秒など複数 timeout があった。real E2E の一例は acknowledgement3.9秒、調査開始発話5.3秒、検索17.4秒、followup28.2秒である。これは過去の特定 setup の実測であり、現在の接続/ログイン/外部サービス性能を保証しない。

存在する非同期経路にはまだ弱点がある。結果が来るまで話題が変わっていないか、deadline 内か、kick 元の generation が有効か、最新 materials で今話せるかの共通 revalidation は未完成。現行 followup は生成後に append list へ入れ、構築時の generation を付ける。**background task にしただけでは stale result の発話を防げない**。O4 の deadline/expiry、supersede key、milestone、exactly-once followup は改善提案として残る。

根拠: [sense 実装と進捗](../v2/_docs/260704.md)、[sense record/claim](../v2/server/tomoko/sense.py)、[sense schema](../v2/docker/postgres/init/101_v2_sense.sql)、[会話 followup](../v2/server/tomoko/conversation.py)、[research adapter](../v2/scripts/world_search_perplexity.py)、[O4 提案](../v2/PLANS/openai.plan.md)。

### OCR、画面、カメラは観測の由来を残す

v2 初期 OCR は `screencapture + tesseract + osascript` で作り、その後 Vision.framework sidecar 優先、tesseract fallback にした。front app、window title、browser title/URL という OS metadata を補助証拠にし、raw screenshot を online prompt へそのまま入れない。VLM に画面全体を JSON 解釈させるより、OCR と具体的 metadata を主材料にする判断を v1 から継承した。

`observation_from_ocr_artifact()` は image path → OCR → UserStatusObservation を一つの境界として扱い、artifact_path/visible_text/activity の出自を残す。unit fake OCR と real fixture image integration を分ける。画面収録権限は子 Python だけでなく tmux を起動した terminal app に必要で、そこが未許可だと capture が失敗して followup が薄くなる。camera sidecar は AVFoundation で一枚取得、Vision 顔検出で presence を返す。未許可時は graceful failure し、観測を書かない。

「観測できなかった」「不在だった」「現在は在席とみなす」は異なる。camera が使えるという事実をスクリーンショット結果の default presence と混同しない。user absent は world initiative を止める材料だが、STT で現に話しているユーザーへの返答拒否にはしない。

根拠: [user-status](../v2/server/user_status/)、[Vision sidecar](../v2/scripts/vision_ocr/)、[camera sidecar](../v2/scripts/camera_presence/)、[実機権限メモ](../v2/_docs/260704.md)、[OCR integration](../v2/tests/integration/test_v2_user_status_ocr.py)。

### prompt cache は文字列の見た目では判断できない

安定 persona/system と会話 role history を前に、変わりやすい runtime context/recall を後ろに置くことが効いた。初期は `SYSTEM / INSTRUCTION / SESSION_TRANSCRIPT` を一つの user message として送り、raw prompt が append-only に見えても prefix cache が hit しなかった。chat template 後の token 列では前 request の assistant 生成位置と次 request の user 継続位置が一致しなかったためである。

`SESSION_TRANSCRIPT` を OpenAI chat messages の user/assistant role 列へ分けて送ると、2ターン目以降に dflash prefix cache hit が出た。次に summary/calendar/user_status を system から外して `RUNTIME_CONTEXT` にし、candidate を `VOLATILE_RECALL` にして最後の user message 末尾へ置いた。さらに partial だけ concise instruction を変えると prefix 系列を割るため、partial/final とも main instruction を同じにした。2026-06-20 の5-turn smokeでは instruction hash が全turn一致、dflash 5 request 全て hit、prefill_tokens_saved332→568という記録がある。

cache 調査では raw `prompt_text` だけでなく、**実送信 `sent_messages`**、section の chars/hash、transcript turn数、dflash の hit/miss/eviction/prefill saved を同じ run の時間窓で見る。cache_shape は形の観測であり、単独で KV hit や affinity の証明にはならない。hit を増やすために人格や必要履歴を削る前に、volatile な部分を先頭へ混ぜていないか、instruction が route ごとに変わっていないかを点検する。

根拠: [prompt builder](../v2/server/tomoko/prompt.py)、[MEMORY の cache 判断](../v2/MEMORY.md)、[5-turn smoke](../v2/scripts/v2_five_turn_smoke.py)、[cache/affinity の限界](../v2/PLANS/openai.md)。

### AttentionMode は別の人格状態機械にしない

`conversation / ambient` は Tomoko 所有の gate threshold profile として入れた。wake cue で conversation、長い silence または stop intent で ambient に戻る。ambient の低 saturation 独り言は suppress するが、request-like final は会話へ戻す。wake/idle/monologue suppress/wake recovery を fake replay で固定した。

`話` のような語は partial acknowledgement cue には有用でも、ambient の final request cue に入れると独り言を会話復帰扱いしすぎる。cue を共通辞書へ無条件に集約せず、用途ごとの false participation を検証する必要がある。profile は score_breakdown の attention_mode_conversation/ambient/ambient_min_saturation で観測し、hot-path/client は判定しない。

根拠: [MEMORY の AttentionMode](../v2/MEMORY.md)、[会話 core](../v2/server/tomoko/conversation.py)、[scenario 群](../v2/scripts/scenarios/)。

### 観測と評価で残すべき区別

runtime の一次観測は `[tomoko:<process>] event key=value` の console-visible log、後追い分析は JSONL/artifact に分けた。主要境界のログを静かに削らず、観測起点、decision、order、request、generation を join できるようにする。readiness は起動時 snapshot と後の transition を分ける。

計測は平均だけでなく p50/p95、no-audio、partial/final origin、reconcile、false-early、stage breakdown を残す。no-audio を成功サンプルから除くと「速い成功だけ」の指標になるため、必ず失敗として扱う。各 latency run 前に Tomoko state に加え VAD、streaming STT、active trace も reset する。first-audio で run を切った後の遅延 STT が次 run に混ざる問題が実際にあった。

初回の可聴反応には固定相槌・ack が含まれ得る。partial-origin の first audio が負値なら「人間が話し終わる前に反応できた」証拠だが、依頼への content が早い証拠ではない。O0A-01 の ResponseKind はこの分離の準備であり、first_feedback/first_content/correction/cancel_to_server_stop/cancel_to_client_playback_stop/invalid-generation chunk の milestone 集計は O0A-03 以降の未実装事項である。

client/server の monotonic clock を直接引き算しない。各 process 内 duration、因果 ID、server 受信時刻を組み合わせる。browser telemetry でも物理可聴停止を直接は証明せず、人間の耳または loopback 計測を別に置く。

LLM-as-judge は自然さ/duplicate/missed/awkward の補助観測に使い、regression gate にしない。31B unavailable は skipped を記録する。最終的な自然さ、記憶の役立ち方、割り込みの不快さは人間評価と join する。`_docs/evaluation.md` は responsiveness、attended_feeling、turn-taking、interruption、memory、persona、recovery を別軸に分ける設計メモであり、その全ての instrumentation が現 runtime に完成しているという意味ではない。

根拠: [評価設計](../v2/_docs/evaluation.md)、[latency suite](../v2/scripts/v2_latency_suite.py)、[f.md の reset/no-audio 経緯](../v2/f.md)、[milestone 未達](../v2/PLANS/openai.md)。

### runtime 運用と検証の知見

- tmux window や process の存在は readiness の証明ではない。dflash launcher は `/v1/models` を確認し、既に ready なら再起動せず、stale window なら respawn する。31B cold load が dflash 内の300秒待ちに負ける、retry 後に既に ready の port へ二重起動して Address already in use になる問題があった。
- hot-path startup 必須依存は main 26B と VOICEVOX に絞り、31B summary/background は optional probe にした。補助モデルの失敗で会話入口まで閉じない。
- internal WS :8765 は uvicorn 起動前 guard で listener PID/command/address を示して止める。tmux window が消えても古い uvicorn PID だけ残ることがある。port を勝手に毎回ずらして原因を隠さない。
- stale internal WS は closed socket を捨て、一度 reconnect する。reconnect と stale replay/canonical idempotency の完成は別に検証する。
- PostgreSQL init SQL は新しい volume 作成時しか走らない。既存開発 DB に `101_v2_sense.sql` を追加する場合、ファイルを置いただけでは schema 更新されない。
- `make autopilot` は常時 `make check`、local DB integration、fake scenario suite を実行し、runtime ready なら real overlap/calendar/latency も実行する。runtime 未起動の real skipped を PASS 実測に言い換えない。
- fake contract、real synthesized-audio `/ws` replay、人間の実会話を区別する。fake DB smoke の数十 ms は real LLM/TTS の性能ではなく、実 OCR fixture は日常の画面内容理解の精度ではない。
- 「Phase 完了」のチェックはその当時の受け入れ項目の達成であり、後の広い保証ではない。長時間 soak、slow LLM/DB/tool、queue overflow、通常切断の残 task、RSS、reconnect/restore、config drift は O7 等の計画に残った。

根拠: [runtime launcher](../v2/scripts/run_llm.sh)、[dflash wrapper](../v2/scripts/run_dflash_server.sh)、[runtime process](../v2/server/runtime.py)、[autopilot](../v2/scripts/v2_autopilot.py)、[LOG](../v2/LOG.md)、[OpenAI plan](../v2/PLANS/openai.plan.md)。

### 作り直しで持ち越す問い（未決定）

以下は過去実装から明らかになった論点であり、新版での採用を確定するものではない。

1. single-owner の会話状態更新を短く保ち、LLM/tool/TTS を cancellation 可能な child task にするとき、世代の発行・失効・exactly-once emission をどこで一元化するか。
2. STOP/REPLACE の要求到達、server の無効化、旧 byte 最終送信、browser source stop、実耳の無音をどの ID/時刻で結ぶか。
3. canonical assistant record の条件を「送信済み」「再生開始」「完了」のどこに置き、途中 cancel の delivered range をどう保存するか。
4. DB 保存を live path から外しても final/session/canonical event を失わない durable retry/idempotency をどう作るか。
5. partial と materials は coalesce できる一方、final/control は overload 時に何を backpressure し、何を優先するか。
6. sense の late result、candidate expiry、user absent、topic change、attention change を発話直前にどの材料で再検証するか。
7. first feedback と first substantive content、誤開始率と false wait、p95 と自然さを同じ replay/corpus でどう評価するか。
8. 直近8件履歴＋字面 recall から豊かな記憶へ進む必要性と、意味 embedding/LLM summary の実測 benefit をどう確かめるか。
9. active context compaction が必要になった場合、原本を保った versioned snapshot と request 境界 cutover をどう設計するか。
10. 多数の cue/閾値/例外を足し続けた 1,600行級会話 core から、説明可能な小さい policy と実行 owner をどう分離するか。

再利用価値が高いのは、単一 public WS、DTO、明示 owner、原本と索引、観測と判断、生成と送出の分離、replay artifact、反証可能なテストである。一方、既存の数値・閾値・cue・完成チェックは、その backend/時点/fixture の証拠として扱い、新版の正解として無条件に引き継がない。

<a id="knowledge-15"></a>

## 音声と性能評価から得た知見

### 測定値の意味を先に固定する

Tomokoでは「速くなった」という記録が多数あるが、同じ指標ではない。再実装時に引き継ぐべきなのは最小値だけでなく、起点・終点・対象の違いである。

| 指標・境界 | 実際にわかること | そこからはわからないこと |
|---|---|---|
| WebSocket echo round trip | transport/serializationの一部のコスト | マイク取得、STT、判断、TTS、実再生までの体感 |
| VAD segment end → first audio | VADが終端を確定した後の処理 | 発声終了からVAD無音窓を待った時間 |
| replayのvoice end → first binary audio | 最後の有声音を送ってからクライアントが音声bytesを受ける時間 | スピーカーから実音が出た時刻、本回答開始、回答完了 |
| STT file transcription | 完成済みファイルを処理するthroughput | 話しながらのfirst partial、発声終了後のfinal確定 |
| 音声入力開始 → first partial/final | 入力ペーシングを含むストリーム時の出力時刻 | STT計算そのものの所要時間だけ |
| LLM first content/token | 推論の初動 | 文の完成、TTS投入、再生開始、返答内容の良否 |
| first acknowledgement / backchannel audio | 聞いていることの表現、早期反応 | substantive answerの開始、内容の正確さ、完了 |
| 常駐scorerのpredict | メモリにロード済みの1回推論 | Python/uv起動、モデルロード、HTTP、音声全体 |
| fake runtime縦切りsmoke | DTO・制御・配送契約の成立 | 実STT/LLM/TTSの速度、実マイクでの自然さ |

とくに2026-07-04のG1合格は、未完成partialへの短いacknowledgementと時刻質問の直接応答を含む。`partial-origin p50 < 0ms`は人間の入力完了前に何かの音声が返ったことを表し、負の計算時間でも本回答の完了でもない。`runs_no_audio == 0`を必須にし、無音runを分母から落として高速化に見せない。`first_audio`だけを目標にすると相槌による改善と本回答改善が混ざるため、後のO0a計画ではresponse kindとmilestoneを明示する方向に進んだ。ただし計画されたmilestone集計・browser再生観測まで実装済みとは扱わない。

証拠: [latency記録](../v2/_docs/latency.md)、[G1と測定境界の判断](../v2/MEMORY.md)、[O0a変更前契約](../v2/_docs/openai-plan/o0a-contract.md)。O0a文書はbaselineであり、ResponseKind等の後続変更については後のLOGを優先する。

### v2音声経路で実際に効いたもの、効かなかったもの

以下は各時点の実runtimeでの履歴であり、モデル・prompt・speedScale・履歴・partial/final条件が変わっている。全行を統制されたA/B比較として扱わない。原則としてmacOS `say`/Kyoko合成音声を16kHz mono float32、128-sampleの実時間ペーシングで公開`/ws`へ投入し、当時のApple Speech、dflash 26B、VOICEVOXを使った。実マイクでの自然さを保証する測定ではない。

| 日付・条件 | 記録された値 | 引き継ぐ解釈 |
|---|---:|---|
| 6/18 初期real `/ws`、VOICEVOX 2.0倍 | voice end→first bytes 2875.8ms | transcript/TTS結果/audioがほぼ同時にクライアントへ見えた。イベント観測順だけで内部stage時間は推定できない |
| 6/18 DB split、接続をprocess lifetimeへ | first audio 2153.8ms、server STT開始→audio ready 1733.9ms | 発話ごとの接続確立を除いた。以前の単発2982.3msより828.5ms短いが単発比較 |
| 6/18 同一WSの5ターン初期smoke | 平均3491.2ms、p95 4387.7ms | turn別2505.4/2869.6/3511.1/4182.1/4387.7ms。履歴増大・cache形状を調べる起点 |
| 6/18 chat messagesをroleごとに分解後の5ターン | 平均2354.5ms、p95 3073.2ms | raw文字列のappend-onlyより、chat template後token prefixの一致が重要 |
| 6/18 pseudo partial + E2Bをreceive loop内でawait | partial orderがfinalより5108.4ms早いが、first audioはvoice end+4492.5ms | finalより先に判断できても、受信とVAD自体を詰まらせる構造では遅い |
| 6/18 partial/finalを非同期laneへ | best 860.5ms、final確認run 1515.6ms | キュー投入だけをreceive loopに残す効果。一方finalは5123.1ms、重複応答が残り、bestだけで完成扱いできない |
| 6/18 partial/final reconcile | first audio 1736.5ms、final 2095.1ms | speech_order/TTS/audioが各1件に減り、二重回答を除去。速度以外の改善 |
| 6/20 WS-originの5ターン | 平均1667.2ms、p95 2130.4ms | internal WS、実STT/LLM/TTS、DB監査保存を通した。partialで早期終了したturnのfinal=nullは失敗とは限らない |
| 7/04 G1初期3 seed | final p50 7298.7ms、p95 14026.4ms、partial 0/3 | 全runでaudioはあったが、目標に明確に失敗 |
| 7/04 internal stage追加、1 seed | first audio 7938.6ms、STT337.3 / Tomoko3480.7 / TTS3676.3ms | このrunの支配要因はSTTではなくLLMとTTS。測定してから改善対象を決めた |
| 7/04 conversation reset後、1 seed | first audio 6310.7ms、stage total5864.7ms | 繰り返したseedが履歴に入り回答が長文化する汚染を減らした。それでも目標未達 |
| 7/04 deferred TTS + 文分割 + LLM第一文cutoff | 3 seed final p50 3122.4ms / p95 5143.9ms、partial 0/3 | 単発best2627.4ms、STT334.3 / Tomoko742.4 / order→first audio1114.1ms。第一文までの先送りだけではG1未達 |
| 7/04 partial ACK + 時計直接応答 | 3 seed×3回連続PASS、partial 2/3、final約1320ms、partial p50約376ms | 不完全topicへ本回答を捏造せず短いACK、finalで本回答へreplace。ACKと本回答の指標を分ける必要が残る |
| 7/04 最終full autopilot | 10 seed×3反復、no audio 0、final p50 1339.7 / p95 1343.2ms、partial p50 -163.8ms | fakeだけでなくreal overlap/STOP/calendar appendも実施した当時の回帰ゲート。全時代・全backendでの恒常保証ではない |

各runのartifact名は`latency.md`に列挙されている。主要なものは`logs/say-latency-20260618-160201.json`、`logs/five-turn-smoke-20260620-234842.json`、`logs/latency-suite-20260704-010457.json`、`logs/latency-suite-20260704-013026.json`、`logs/latency-suite-20260704-044312.json`、`logs/autopilot-20260704-044545.json`。ログには会話本文やpromptを含み得るので、この知見集には私的内容を転載しない。

### 音声受信・VAD・partial STTの落とし穴

- 16kHz/512 samplesなら1chunkは32msで、無音閾値300/400/500msの検出は合成scorerで320/416/512msになった。閾値と観測時間はchunk粒度で量子化される。この初期測定は実Sileroや実マイクの精度評価ではない。
- STT/semantic/LLM/TTSをWebSocket受信loopでawaitすると、入力音声受信そのものとVAD終端検出が遅れる。v2では`AudioPartialLane`と`AudioFinalLane`を分け、receive loopをVADとqueue投入へ限定した。partialは溜まったchunkをcoalesceし、毎chunkの再推論を防いだ。finalはpartial laneの短いidle待ちでApple Speechの取り合いを避けた。
- Apple Speechの旧pseudo streamingは、累積音声を繰り返しfinal transcriptionし、その結果を`is_final=False`で出す方式だった。ネイティブな逐次partialと同じではない。呼出周期・最小音声長・同文抑制・モデル実行時間の積み上がりを含めて評価する必要がある。
- 6/20のログsnapshotでは`vad_segment=574`に対しApple Speech空final=492。VADが反応しただけで意味のある音声とは限らない。完成した`AudioSpeechSegment`境界にduration/RMSの`SegmentSttGate`を置き、低エネルギーを`vad_segment_dropped`として記録した。VAD hot loop内にDTOや時計呼出しを増やしていない。
- 空finalをdurable user utterance/promptへ入れると、本人が話していないのに会話が回り得る。空文字は無音tickの代用にも使わない。
- TTS echo対策として「送信WAV長+800msの間micを止める」修正を一度入れたが、ヘッドセット前提ではbarge-inを殺すため撤回した。実際の入力装置・回り込みを確認せず、発話中の入力を全遮断しない。
- pseudo partial誤認識が後から別の意味へ伸びると、文字類似度だけのreconcileでは同一発話のpartial/finalが何度もappendされる。同一trace/VAD segmentの更新として扱う境界と、最終発話をdurableに保存する境界を分ける必要があった。一方、常にfinalを抑制すると誤partialの訂正も消えるため、後続のdivergence/ACK置換契約までセットで残す。
- 測定前resetは人格側の履歴だけでは不足する。hot-path VAD buffer、streaming STT、active trace、短期履歴もresetし、first-audioで打ち切った前runの遅延partial/finalを次runへ混ぜない。
- m4aの`afconvert`出力にPython 3.11の`wave`が読めないWAVE_FORMAT_EXTENSIBLEが出る場合があった。録音replayはffmpegがある環境で16kHz/mono/s16 PCMへ変換した。これはデコード経路の問題で、STT自体の精度不足と区別する。

証拠: [MEMORYの6/18〜7/04判断](../v2/MEMORY.md)、[測定条件と失敗run](../v2/_docs/latency.md)、[STT実装](../v2/server/audio/stt.py)。

### 2026-09-03のSpeechAnalyzer比較をどう読むか

当日の環境はmacOS 26.6.1/Xcode 26.6。Apple fallback sidecarを`SpeechAnalyzer`/`SpeechTranscriber`へ移行し、`AssetInventory`でlocale asset、`AnalysisContext`で用語を渡し、既存JSON/JSONL contractを維持した。async `@main`を使うSwiftコンパイルには`-parse-as-library`が必要だった。既定STTは7/05に移行したWhisperKit/Argmax large-v3-turboのままである。

最初の約3.8秒合成音声probeは、file転記468.2ms、3,200 bytesを100msごとに送る疑似リアルタイムでfirst partial1077.1ms/final3986.2ms。高速pipeではfirst partial439.0ms/final478.8msだった。`ffmpeg -re`だけに頼った初期probeはpipe bufferingで入力がまとめて届き、first partialを約3.26秒と誤って解釈しかけた。入力ペーシングを実際に固定することが重要だった。

比較は同じKyoko合成5文（3.22〜4.53秒）の音声を一度生成し、各backendで10反復、warmup1回を除外、Apple旧新はcase/runごとAB/BA交互順、on-device、ja-JP、同じcontextual stringsで実施した。

| backend | n | mean | p50 | p95 | SD | 品質上の観察 |
|---|---:|---:|---:|---:|---:|---|
| SFSpeechRecognizer |50|215.6ms|212.3ms|239.7ms|16.3ms|2/5文で末尾欠落、固有名詞表記誤り |
| SpeechAnalyzer |50|202.1ms|202.7ms|209.9ms|6.2ms|5/5文を末尾まで返したが固有名詞誤りは残る |
| faster-whisper small / CPU int8_float32 |50|1968.3ms|1983.6ms|2102.6ms|101.3ms|語の取り違えが反復して発生。モデル初期化694.9msは別計上 |

Apple新APIはpaired平均で13.5ms/6.3%短く44/50組で速い。近似95%区間は9.6〜17.4msの短縮。cold単発468.2msから旧APIより遅いとは言えない。各caseの出力は各backendで10/10同じだったが、5文しかないので日本語全般の正確性の証明にはならない。

faster-whisper 1.2.1は同一corpusだがApple測定とのinterleaveはしていない。初期promptで名前のひらがな表記は得られたが、合成文の「歯医者」を全10回「会社」と誤認識。空白・句読点除去、10/十正規化後のCERは5.66%、SpeechAnalyzerは3.77%。現Macで低遅延fallbackへ採用する根拠にはならず、v1 wrapper smoke成功だけを残しv2 factoryへは配線しなかった。

WhisperKitは当日65秒以内に結果を返さず、paired比較から除外された。timeoutを遅延65秒の有効標本やモデル一般の性能評価へ置き換えない。WhisperKitの既定維持と、その日の比較失敗は別事実である。実マイク同一corpusでのfirst partial/final・partial-final乖離・固有語精度の比較は残課題だった。

証拠: [Apple paired JSON](../v2/_docs/benchmarks/speech-analyzer-paired-20260903.json)、[faster-whisper JSON](../v2/_docs/benchmarks/faster-whisper-small-say-20260903.json)、[9/03 LOG](../v2/LOG.md)、[Swift sidecar](../v2/scripts/apple_speech_stt/AppleSpeechSTT.swift)。

### TTSはasync iteratorを名乗るだけではstreamingにならない

`VoicevoxChunkedTtsBackend`がasync iteratorでも、上流の`SpeechOrderExecutor.execute()`が全chunkを集め終えてからresultを返し、senderがその後まとめて送ればfirst audioは前倒しされない。7/04にaudio conversationのspeech orderを`deferred_tts_orders`で先にresult queueへ戻し、senderが`speech_order` JSONを先行送信、`execute_stream()`のchunk callbackからbinaryを逐次送る配線へ変えた。

この変更後、audio pathの`tts_result`はbinary送信後のsummaryになった。text-prompt pathの従来順序とは異なる。また`latency_stage.tts_ms == 0`はTTSがゼロ時間になった意味ではなく、計測対象のresult生成後へTTSを移した意味である。測定境界の変更を性能改善と混同しない。

第一文/読点等で短いTTS単位に切ること、LLM streamを最初の完全文で打ち切ること、先行ACKを出すことはそれぞれ違う施策。第一文cutoffは音声開始を早めたが、長い回答の内容を後段で届ける設計が別に必要になる。VOICEVOX speedScaleは2.0へ上げた後、ユーザーの「速すぎる」という聴感で1.5に戻した。発話速度を上げるだけで自然さと遅延を解けるわけではない。2.0時点の2875.8msを1.5既定の値として引用しない。

O0a baselineで確認された未解決点は、deferred TTSを単一senderがawaitしており、STOP/REPLACEで後続旧chunkをgenerationで捨ててもbackend合成task自体はcancelしていなかったこと、browserから実再生start/endやbuffer horizonの観測がなかったこと。サーバー送信済みと人間が聞いた内容は一致しない可能性が残った。generation guard・cancellable synthesis・優先制御配送・再生観測を区別して検証する必要があった。

証拠: [7/04 MEMORY](../v2/MEMORY.md)、[TTS実測](../v2/_docs/latency.md)、[O0a baseline](../v2/_docs/openai-plan/o0a-contract.md)。

### VAP/MaAIは値の存在と意味を実際に確認する

- AudioWorkletから128 samplesで届くchunkを、そのまま160-sample前提の`MaaiInput.Chunk`へ渡していたのが問題だった。hot-pathで余りを保持して160-sample frameへ組み直すと連続resultが出た。ライブラリとブラウザのframe単位の不一致をモデル不調と誤認しない。
- `mode="bc_2type"`は`p_bc_react`/`p_bc_emo`を返し、`p_yielding`を返さなかった。相槌モデルが動いていることはyieldingの観測成功ではない。
- 6/22から`bc_2type`と`vap`を別instanceで起動し、同じuser/silence frameを両方へfan-outした。`vap`の`p_future[1]`を`TurnMaterials.p_yielding`へ写した。両laneは別時刻に結果を返すため、aggregatorは受信resultに含まれるfieldだけを更新し、未指定fieldをNoneで消さない。
- 実smokeで`maai_vap_started`、連続`maai_vap_result`、WS snapshot、DB observation partial0.2587/final0.2306、紐づくscheduler scoreを確認した。これで信号経路が通ったといえるが、1発話の値から人間らしいyielding検出精度は主張できない。
- 7/04に`p_yielding=None`を1.0へ補完しないと明文化した。欠損と高いyieldingは別。`yielding_opportunity`/`silence_opportunity`とどちら由来かをscore_breakdownに分離した。
- G5回帰は`p_yielding`をscripted replayへ明示したfake検証である。partialでyielding0.92/silence0、finalでyielding0/silence1を通したことはgate配線の検証であり、say音声から実VAPが安定検出した証拠ではない。
- MaAIの`get_result(timeout=...)`が現実装に合わずpoll errorを起こした。`result_dict_queue`/`output_queue`を優先してtimeout付きread、非対応時fallback、エラー種別別の1/10/100/1000回等の間引きログとfail-openを採用した。ログを消すのではなく、エラー件数と機能低下を残した。
- fixed backchannelは再生中なら抑制、react/emo thresholdとcooldownを満たしたときに事前音声assetを出す。会話LLMによる本回答と同じlaneではなく、後のResponseKind計画でもhot-pathが固定分類を持つ案を残した。

証拠: [MaaiBackchannelDetector](../v2/server/hot_path/backchannel.py)、[6/21・6/22・G5判断](../v2/MEMORY.md)、[実測](../v2/_docs/latency.md)。

### prompt cacheの改善は送信token列と実カウンタで確認する

v2のraw promptをappend-only文字列にしても、単一user messageとして送るとchat template後のassistant位置が次turnで一致せずhitしなかった。履歴を`user`/`assistant`のmessagesへ分解した後、2turn目以降でprefix hitを確認した。人格/固定instructionは先頭に、summary/calendar/user_statusは`RUNTIME_CONTEXT`、candidateは`VOLATILE_RECALL`として最終user message側へ寄せた。

6/20 21:38の5turnではsystem hashは安定していたが、partialだけinstructionを変えるため5request中2hit/3miss、entries8/8のevictionが続いた。partial/finalでinstructionを統一した21:54の5turnは全5requestでprefix hit、missカウンタ増分0になった。first audio平均は1393.7→1396.5msとほぼ同じで、p95は2179.2→1776.9msだった。hit改善だけからE2E平均の改善を捏造しない。

direct probeでは初回missのfirst content764.9ms、同prompt repeat211.7ms/72 of 76 token hit、append518.6ms/72 of 98 hit。cacheは実在した。`prompt_text`だけでなく実送信`sent_messages`、section hash/chars、request窓内のhit/miss/eviction/prefill_tokens_savedを同時に保存したことが切り分けに効いた。prompt/historyを削る前に、partial/final置換で過去prefixを再構成していないか、動的時刻やvolatile contextが先頭を変えていないかを見る。

証拠: [cache形状の判断](../v2/MEMORY.md)、[5turn/直接probeの記録](../v2/_docs/latency.md)。

<a id="knowledge-16"></a>

## 意味飽和度モデル・蒸留・教師比較から得た知見

### 意味飽和度の目的と実装上の境界

semantic saturationは「音として発話が終わったか」ではなく「会話相手が今返し始めてよい度合い」を0〜1で表す専用scoreである。言い淀み、逆接、文脈依存の省略、言い直しを含む。VAD終端や句点の有無だけを教師正解にしてはいけない。たとえば「〜を防ぐには」「〜してくれるのは」は人工的に切ったprefixでも、省略された疑問として返答可能な場合がある。

`SemanticSaturationJudge`はdistilled backendがあれば優先し、失敗時はdeterministic fallbackへ落ちる。distilledがなくLLM backendがあればpromptを送って`SATURATION=...`をparseし、これも失敗時はfallback。sourceに`deterministic_fallback_partial`等を残し、同じ浮動小数点値でも由来を追跡できる。fallbackを教師ラベルと混ぜる場合には`label_source`を保存する。教師だけの純粋比較ではfallbackで穴埋めせず形式失敗として残した。

学生は日本語文字1〜4gramをsigned hashし、L2正規化したベクトルと5補助特徴（長さ、疑問末尾、is_final、抑制接頭辞、逆接末尾）からRidge回帰する。公開artifactの実体はhash8192+補助5列=8197 weights、λ0.01、bias、config、metadataのJSON。生成LLMではなく、語彙辞書や元発話本文をartifactに格納しない小さなscorerで、予測後に0〜1へclipする。学習は正規方程式を解く処理なのでepoch待ち・収束待ちのモデルではない。教師の交換でlabel品質は変わるが、同じX/次元ならfitの計算量はほぼ変わらない。

生のscoreは確率として較正されたものではない。runtimeではshort acknowledgementの補正、STT final/partial、VAP/MaAI、motivation、fire/emit gate、reconcileと組み合わせていた。軽量scorer単独の一致率から会話自然さ、割り込み耐性、汎用言語理解を主張しない。

証拠: [SemanticSaturationJudge](../v2/server/tomoko/semantic.py)、[hash-ridge実装](../v2/make-model/make_model/model.py)、[Model Card](../v2/make-model/MODEL_CARD.public-synthetic.md)。

### 初期のE2B生成判定から常駐scorerへの経験

当初、Gemma E2Bへ説明文だけのpromptを送ると明確な依頼でも低いscoreになった。compact few-shotを加えると同一入力で0.95、判定約290〜440msを得た。ただし既存dflash endpointはrequestの`model`を書き換えても起動中31B/26Bを返すため、E2Bの実測には別の`mlx_lm.server` endpointが必要だった。API上のmodel文字列と実ロードモデルは同じとは限らない。

6/18 prefix-window replayは3000ms分の音声でE2B判断281.3ms、推定判断時刻3281.3ms、full final STT3634.0msに対し352.7ms先行した。別runではearly OKがなかった。これは擬似partialの推定であり、本回答音声が352.7ms前倒しされた測定ではない。6/20のdistilled版prefix-windowではfirst OK1600.8ms、final STT3600.1ms、先行1999.3msというrunがあるが、やはり判断器とprefix replayの結果である。

常駐scorerは6/19 JDD1000版でmodel load0.4254ms、warmup1000/測定10000回、mean0.0744ms/p950.0878ms。高速でも明確な依頼に0.1294を出したため、速度だけでは役に立たない。6/20の公開synthetic8192版はload1.4594ms、同じwarmup/反復数、mean0.2820ms/p950.4306ms/max13.5771ms、該当完了依頼0.9253。これらは同一短文のresident predictであり、HTTPやSTT、モデルloadを毎回含む数字ではない。

証拠: [6/18〜6/20 MEMORY](../v2/MEMORY.md)、[latency](../v2/_docs/latency.md)。

### JDD初期実験・anchor追加の功罪

make-model初期はJapanese Daily Dialogueから1文字刻みprefixを作りGemma26B教師を付けた。文書ではJDDをCC BY-NC-ND 4.0/非商用研究目的として扱い、raw/変換corpus/labels/artifactはignored領域へ分離した。これは当時の取り扱い記録であり、公開権利の新しい法的判断ではない。公開syntheticモデルとJDD系を混ぜない。

最初の`--limit 1000`は先頭から43発話分に偏り、同じ1000件でtrain/evaluateした結果がbinary accuracy81.7%、MAE0.1347/RMSE0.1777、教師作成約19分だった。同じtrainでの一致であり、未知会話の正確性ではない。その後seed付きランダム抽出へ変更し、10000件を8000train/2000evalに分けたが、splitは行shuffleであり同一発話の別prefixが両側に入る可能性がある。後の9/27比較はこのリーク懸念を受けてgroup splitへ改めた。

| 初期条件 | 当時のJDD eval教師一致 | 別に観察された挙動 |
|---|---:|---|
| 8000教師+一般anchor1000 |82.65%、MAE0.1802|anchor自身は98.9%。同一依頼がpartial0.4986/final0.9313 |
| さらに逆接anchor1000 |81.35%、MAE0.1877|逆接anchor100%。「〜けど/しかし」で低くなる一方、一般evalは悪化 |
| `contrastive_tail`明示特徴を追加 |82.10%、MAE0.1833|低ラベルが共有前半へ漏れる問題を緩和したが、完了した指示語発話は0.6226程度で待ちやすい |
| さらに照応positive anchor1000 |82.15%、MAE0.1818|照応anchor自身も76.9%。「その方向でいい」等が0.66〜0.74にとどまる |

この経験は「特定例を直すanchorを増やすと全体も良くなる」という仮定を否定した。文字n-gramは前半を共有する完成文と途中文を混同し得る。末尾特徴は一部を分離するが、閾値較正・データ構成・未知表現への汎化は別課題だった。CLIの既定`is_final=False`と`--final`でもscoreが大きく変わるので、比較時に条件を揃える。

証拠: [make-model READMEの旧結果と再現手順](../v2/make-model/README.md)、[後日のリーク注意](../v2/MEMORY.md)。

### 公開synthetic系統の保全対象と限界

公開系はリポジトリ所有テンプレート由来の人工日本語だけを入力とし、Gemma26Bラベル200件を160train/40eval、general/contrastive/referential/life-command anchor各1000件、合計4160trainで作成した。JDD、BSD、Common Voice等をこの系統へ入れていない。既存artifactは`public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json`。

artifact metadataのtrain一致99.9519%、MAE0.003823、RMSE0.007923は学習内適合である。6/20に記録したheld-out40件の教師一致90%も、小規模人工例での教師再現性であって人間goldではない。後述の独立した人工評価76件では既存公開モデルのraw scorerは42/76だった。評価集合の変更で値が大きく変わる事実を残す。

ラベル生成に1件0.7〜1秒台かかり10000件は長時間になるため、`--incremental --progress-every N`でJSONLに途中保存した。公開する際に再利用価値があるのはweightsだけでなく、generator、seed、教師prompt/条件、label_source、anchor種別、train IDs、model card、provenance、評価条件の組である。`PUBLIC_SYNTHETIC_PROVENANCE.md`内の意図されたartifact名には`-200-`を省いた表記もあるため、再実行時は実ファイルとmetadataを確認する。

append dedupeも別の小型hash-ridge系で、previous/current text、時間差、Tomoko発話中/queue active/finalを入力しduplicate/continuation/new_intentを採点した。320人工anchor pairのh2048/λ0.05版、1000warmup/10000測定で平均約0.41ms/p95約0.44〜0.58ms。runtimeへ入れた後は**出力発話中またはqueue activeに限定**してLLM prompt構築前にduplicateを抑制した。idleでの自然な言い直しまで削ってはいけないためである。同じ学習labelでの評価と常駐速度だけでは未知の訂正を消さない保証にならない。private-log seed抽出機能の出力先は別のignored領域とし、public syntheticへ混入させなかった。

証拠: [provenance](../v2/make-model/PUBLIC_SYNTHETIC_PROVENANCE.md)、[Model Card](../v2/make-model/MODEL_CARD.public-synthetic.md)、[実artifact](../v2/make-model/artifacts/public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json)、[dedupe手順](../v2/make-model/README.md)、[latency](../v2/_docs/latency.md)。artifact/dataがGitに含まれるかは個別に異なり、ローカル保全と公開を同一視しない。

### 2026-09-27 Gemma/Codex教師交換比較

結論は「今回Codex教師はAI参照へよく一致したが、同一hash-ridge学生へ移した改善は最大1/76件、採点も速くならず、runtimeを変更しなかった」である。教師が優秀なら学生が自動で大幅改善するという見込みを支持しなかった。

条件はApple M4 Max/128GiB、新規人工日本語240件/120場面group、160train/80eval、group/本文重複なし。曖昧4件を除く76eval（ready38/wait38）を二値評価。参照は別Codex担当が教師出力を見る前に作り点検したAI期待値で、人間goldではなく同系統モデルへの偏りが残る。教師に参照label/category/未来の続きを渡さず、同じprompt・順序・短ID・20件×12batchを使った。

Gemmaは既存fused Gemma4 26B-A4BのMLXローカルserverで、base checkpointそのものでもDFlashでもない。CodexはCLI0.154.0/gpt-6-astra/reasoning medium。クラウド採点には人工例のみを使い、実会話・私的ログ・JDDを入れなかった。学生は同じ160件、anchor追加なし、全件`is_final=True`、閾値0.75、設定事前固定。

| 条件 | 参照一致 /76 | 待つべき例で開始 /38 | 開始可の例で待つ /38 |
|---|---:|---:|---:|
| Gemma教師 |72 (94.7%)|1|3|
| Codex教師 |76 (100%)|0|0|
| Gemma学生 h2048/λ1 |45 (59.2%)|0|31|
| Codex学生 h2048/λ1 |45 (59.2%)|0|31|
| Gemma学生 h8192/λ0.01 |61 (80.3%)|0|15|
| Codex学生 h8192/λ0.01 |62 (81.6%)|0|14|
| 既存公開モデルraw scorer（別train） |42 (55.3%)|5|29|

h8192学生は3例改善・2例悪化で差は1例。教師のscoreが改善した例でも学生では逆方向へ動く例があった。既存公開モデルは4000anchor等を含む別trainなので教師交換の因果比較には使えない。

| 時間の対象 | Gemma系 | Codex系 |
|---|---:|---:|
| 全240件の教師採点wall |129.302秒|136.843秒|
| train160件のbatch時間合計 |77.222秒|94.907秒|
| h2048/λ1 fit中央値（交互各3回） |48.943ms|48.830ms|
| h8192/λ0.01 fit中央値（交互各3回） |2.211秒|2.028秒|
| h8192 resident predict平均/p95（100warmup/1000回） |0.2266/0.2603ms|0.2257/0.2603ms|

採点はCLI/HTTP込みのbatch workflow時間で、1件で割ってオンライン応答レイテンシにしない。両教師を並行収集した1試行であり普遍的モデル速度比較ではない。fitの分布はGemma1.75〜3.92秒/Codex1.79〜2.69秒と重なり、教師交換でfit高速化した根拠はない。

最初のGemma20件batchは長hex IDを1文字落とし、約14.977秒で形式失敗した。推測修正もfallbackもせず全batch不採用、rawを保存し、両教師を短いbatch内IDへ揃えて全240件やり直した。報告表は再実行のみ。Gemma warmup4.667秒や失敗時間を分離したこと、成功結果だけで開発全体を速く見せなかったことも再利用すべき手順である。

保存物はprompt、raw、ID対応、usage、時間、label、学生モデル、全予測、集計、条件/環境。別担当が全240件×両教師の対応とprompt同一性を監査。299 unit passed。runtime/default artifactの置換は行っていない。

証拠: [教師比較REPORT](../v2/_docs/benchmarks/teacher-comparison-20260927/REPORT.md)、[事前protocol](../v2/_docs/benchmarks/teacher-comparison-20260927/PROTOCOL.md)、[summary](../v2/_docs/benchmarks/teacher-comparison-20260927/paired-short-ids/summary.json)、[入力一致検証](../v2/_docs/benchmarks/teacher-comparison-20260927/paired-short-ids/validation.json)。

### 2026-09-27 苦手例追加・特徴ablation

前の80evalは弱点分析に使ったのでdevelopmentへ格下げし、新しい評価を作った。baseは前のCodex160trainであり、稼働中公開モデルではない。新trainは苦手320/一般320、各160場面のready/wait対、8カテゴリ。苦手80/160/320の各段階でカテゴリと期待ready/waitを均衡化。独立担当の新規240eval/120場面から曖昧17件を除き223件（ready116/wait107）、pair順位107組、group bootstrap116場面で評価した。

hash8192、1〜4gram、L2正規化、λ0.01、閾値0.75、全件is_final=Trueを固定。8条件を新教師/学生出力を見る前に固定。char-onlyは5補助特徴をまとめて0にした条件であり、「長さだけを外す実験」ではない。

| 条件 | train n | 一致 /223 | balanced accuracy | 誤開始 /107 | 待ちすぎ /116 |
|---|---:|---:|---:|---:|---:|
| base |160|160 (71.7%)|72.6%|7|56|
| 苦手+80 |240|156 (70.0%)|70.9%|7|60|
| 苦手+160 |320|158 (70.9%)|71.8%|6|59|
| 苦手+320 |480|156 (70.0%)|70.9%|5|62|
| 一般+320 |480|160 (71.7%)|72.3%|14|49|
| 苦手+一般640追加 |800|158 (70.9%)|71.7%|8|57|
| base、char-only |160|154 (69.1%)|70.0%|7|62|
| 800件、char-only |800|170 (76.2%)|76.8%|9|44|

前回別evalで81.6%だった同じbaseが今回は71.7%。重み/bias/旧予測の再現を確認しておりモデルが変わったためではない。学習件数を増やしても単調改善せず、targeted320のbaseとの差95%group-bootstrap区間は−5.50〜+1.80pt、combinedは−4.82〜+2.73pt。char-only800はbase比+4.48pt（+0.44〜+8.81pt）だったが、この人工集合内の区間で多条件補正なし、実会話優位とは言えない。

学習内教師二値一致は約99.8〜100%、800件MAEは全特徴0.00790/char-only0.00983。学習内の表現容量不足より、未知例への汎化と点数の較正が課題として残った。ただし文字Ridgeの原理的限界を証明した実験でもない。

重要な逆転はpair順位である。readyを同場面のwaitより高くする成績はbase全特徴95/107、targeted320も95/107、800全特徴90/107、800char-only89/107。char-only baseは97/107だった。固定閾値accuracyが改善しても意味順位の識別が改善したとは言えない。新規省略疑問カテゴリは主な条件すべて15/30、既知の取りこぼし14件もtargeted320で2件、char-only800で4件しか直らず、別例の悪化が増えた。

結果後の診断では、追加trainのready/wait長さ差が評価と大きく異なった。教師labelでまとめた800trainの平均長さは28.42/19.75字、新evalのAI参照は22.06/21.54字。長さ係数は1.673→3.144、biasは0.032→−0.371。長い文をreadyとする近道への依存が疑われるが、5特徴同時ablationなので長さだけの因果効果は断定できない。反対label間の完全特徴衝突0、eval n-gram被覆38.2→52.5%でも全特徴accuracyは改善しなかった。pair cosineもtrain苦手0.824/一般0.771に対しeval0.463で、データ生成法そのものの分布差があった。

閾値は別分析とした。旧developmentだけで選ぶとbase閾値0.61でblind173/223（77.6%）へ上がるが誤開始は7→18。800char-onlyは0.58で169/223、誤開始24/待ちすぎ30。全体一致率だけで閾値を下げれば自然な割り込み品質が良くなるわけではない。

追加640教師採点は346.403秒（人工例作成/点検時間除外）。fit中央値はbase2.215秒、targeted2.239秒、general2.292秒、combined2.307秒、char-only2.409秒、各3回条件順反転。resident推論は約0.227〜0.228ms/p95約0.256〜0.259ms、100warmup後240文を1000回巡回。char-onlyも同じpredictで補助特徴を計算してからゼロ重みを掛けるため、専用最適化版の速度ではない。

品質管理上は、教師出力を見る前に「切断したが省略疑問として成立する」wait104行を修正し、blindは本文を変えず曖昧9行をnullへ移した（元8と計17除外）。変更履歴/SHAを保持。点検担当へ途中で2学習断片と省略疑問の問題を伝えたため完全な情報隔離とは主張しなかった。新evalも結果を見た後は未見でなくなる。次の設計検証には別評価が必要である。

独立監査は1120例×8条件=8960予測をweightsから再計算し一致、学習IDs/label/分母/category/pair/dev閾値を確認。312 unit passed。**runtimeモデルは置換していない**。長さを揃えたtrain、長さ単独ablation、末尾位置付き文字特徴や文脈表現比較は候補として残り、実施済みではない。

証拠: [feature-learning REPORT](../v2/_docs/benchmarks/feature-learning-20260927/REPORT.md)、[PROTOCOL](../v2/_docs/benchmarks/feature-learning-20260927/PROTOCOL.md)、[summary](../v2/_docs/benchmarks/feature-learning-20260927/results/summary.json)、[diagnostics](../v2/_docs/benchmarks/feature-learning-20260927/results/diagnostics.json)、[独立監査](../v2/_docs/benchmarks/feature-learning-20260927/results/independent-audit.json)。

### 2026-10-02 single-forward/logits・JevMLXの独立比較

この結果は`comparisons/semantic-forward-20261002/`にある**未追跡のローカル比較資料**に由来し、今回v2へローカル保全したもの。Git cloneだけでは同じ資料・モデル・仮想環境が得られるとは限らない。以下は当時確認した候補と実測であり、外部モデルの現行仕様や対応状況を新たに確認した記録ではない。

「意味フォワード」は音響finalを絶対条件にせず、partialの意味が十分確定すれば生成を先行し、実発話は別のgateで許可する考え方である。比較では返答準備Pと実発話開始Sを分け、人工20場面（未完、発話継続中の意味完了、短い無音、否定、言い直し、長い無音でも未完、停止要求）と擬似音響値を使った。**実STT/VAD/TTS、runtime接続、音声E2Eを測った実験ではない**。

環境はMacBook Pro/M4 Max/128GB/Metal、Python3.14.4、生成・自作logitsはmlx-lm0.31.2。Qwen3 4B公開MLX4bit約2.26GBとTomokoの既存fused Gemma4 26B-A4B約14GiBを、別process、GPU同時実行なしで測定した。Gemmaは総26B/active約4BのMoEで、一般の4B denseと同一ではない。またfused configは既定4bitだが120モジュールに8bit overrideがあり「全層一律4bit」ではない。公開baseとTomoko融合重みの違いも品質差へ影響し得る。

Layaは当時、英語版ModernBERT421M、日本語を含むmultilingual版mmBERT322M/独自decision head、Transformers/Safetensors/PyTorch/ONNX系だった。公開MLX checkpoint/手順を確認できず、生成LLM用mlx-lmへ無理に入れなかった。Apple GPU対応という表現だけではMLX形式の存在を意味しない。JevMLXは重みではなく推論コードであり、名前から小型専用モデルと誤認しない。

同じ20場面を2反復した40標本の結果は以下。期待labelは人間未確認の仮基準。生成では共通prompt、greedy、最大28token、実出力7tokenの`P=0;S=0`。初回Qwenが全件0だったため4つの一般例を追加して同じ20件を再測定しており、品質値は**評価例を見てpromptを変えた探索結果**である。

| 方法 | warm中央値 / p95 | P一致 /40 | S一致 /40 | 誤発話開始 / 見逃し |
|---|---:|---:|---:|---:|
| Qwen3 4B、短い文字列を生成 |806.5 /868.0ms|30|36|0 /4|
| Tomoko Gemma26B-A4B、短い文字列を生成 |961.9 /1027.9ms|38|40|0 /0|
| Qwen3 4B、自作A/B/C次token logits |463.7 /543.3ms|24|24|0 /16|
| Gemma26B-A4B、自作A/B/C次token logits |634.3 /690.0ms|38|40|0 /0|
| JevMLX+Qwen3 4B、WAIT/PREPARE/SPEAK |390.4 /479.2ms|28|24|0 /16|

生成のloadはQwen2935ms/Gemma5880ms、初回判定672/1287msを別計上。入力361〜371token/324〜335tokenで、prompt整形後から文字列取得までtokenize/prefill/generation込み。自作logitsは入力276〜286/251〜262token、初回295/444ms、chat template/tokenize/1forward/logits取得込み。OSファイルcacheが温かい可能性があるためloadを電源投入直後のcoldと呼ばない。HTTP・ネットワーク・queue・STT・TTSはすべて除外している。

自作logitsは`A=待つ/B=準備のみ/C=声を出す`の制限softmax。Qwenは40/40でBを選び、誤準備16/発話見逃し16。Gemmaの不一致はstop要求へのP=1が2件だけだが、停止時に準備可かどうかという仮labelにも解釈余地がある。制限候補内softmaxは未較正相対値で、正解確率ではない。自作probeはJevMLXのsuffix trie/KV broadcast/補正を再現したものではない。

JevMLXはcommit`7e0d746081b88412ccd7d84a5ffdcf9d61b36904`、0.1.0/mlx-lm0.32.0を隔離環境へ入れ、ローカルQwen重みをoffline指定で使用。当時の既存TomokoサービスとPython環境を変更しなかった。`decide()`既定labels採点、温度1.0、較正/prior補正なし。初回2721msはmodel load/Metal warmup/drift canary込み。40件すべてでSPEAKを選ばずWAIT26/PREPARE14となり、最速でも意味終端の発話判定器としてそのまま採用できない結果だった。restricted_probability中央値0.777/margin0.555は正解確率を示さない。

JevMLXの共通prompt prefillをKV cacheとして候補へ展開し、少数候補をまとめてforwardする仕組みでは、**prefill自体は別forward**である。候補分割・僅差再採点・補正で追加passもあり、リクエスト全体が必ず1forwardとは言えない。Qwen3 LM-head adapterは当時engine未配線の注記、Gemma4は対応adapterなしでJevMLX測定対象外。生成比較とはprompt/採点法/mlx-lm版が違うので差をJevMLX単体の因果効果へ帰せない。

初期全0の失敗Qwen、修正後、raw logits、JevMLXをすべて残したことに価値がある。今回の判断器をruntimeへ採用した記録はない。私的会話・外部APIは使わず人工日本語だけで比較した。

証拠（ローカル保全資料）: [RESULTS](../v2/comparisons/semantic-forward-20261002/RESULTS.md)、[Qwen生成summary](../v2/comparisons/semantic-forward-20261002/qwen3-4b-v2-results.summary.json)、[Gemma生成summary](../v2/comparisons/semantic-forward-20261002/gemma4-26b-v2-results.summary.json)、[Qwen logits](../v2/comparisons/semantic-forward-20261002/qwen3-4b-logits.summary.json)、[Gemma logits](../v2/comparisons/semantic-forward-20261002/gemma4-26b-logits.summary.json)、[JevMLX summary](../v2/comparisons/semantic-forward-20261002/jevmlx-qwen3-4b-results.summary.json)。再現コマンド中の旧絶対pathや`/tmp`環境は実在を再確認して使う。

<a id="knowledge-17"></a>

## 評価をやり直すときに失わないこと

### speed、品質、構造テストを分ける

1. **契約の検証**: DTO、所有者、generation、event順序、supersede/STOP/APPEND、fake replay。これはモデル品質の証明ではない。
2. **部品の実測**: DB insert/notify/select、STT file/stream、LLM first content、TTS first chunk、resident scorer。cold/load/warmを分け、環境/版/モデル/入力/件数を保存する。
3. **実runtimeの縦切り**: 同一公開`/ws`契約でpaced音声を入れ、STT→判断→order→audioを同一traceで追う。listenしていることとrequestが完了すること、audio bytesと実再生を分ける。
4. **会話としての評価**: 実入力機器、前後文脈、返答の内容、言い直し、沈黙、割り込み、誤参加、記憶を含めて人間が評価する。最小latencyだけで完了にしない。

同じseedを繰り返す測定ではresetの有無を明示する。cold単発を別backendのwarm平均と比べない。5文×10反復を50種類の独立日本語例と呼ばない。p95の小標本、同場面pair、複数条件の探索、teacher/model/version/量子化の違いを残す。実験で一度見たevalを調整へ使ったらdevelopmentに格下げし、新しい未見評価を作る。

### 品質軸は人間評価と機械ログを対応させる

`_docs/evaluation.md`は将来の評価設計で、完成済みgold datasetや最適重みを報告したものではない。残す価値がある評価軸は以下。

| 人間の評価軸 | 対応させる観測例 |
|---|---|
| responsiveness | 発声終了→最初の本回答text/audio、実再生start、turn全体、各stage |
| attended feeling | attendedへの応答率、engaged中の無視、ambient誤参加、自然なfollow-up |
| turn-taking naturalness | 言い淀みの誤切断、不自然な分割、待ちすぎ、相槌過剰応答 |
| interruption robustness | STOP検出遅延、誤停止、soft/hard interrupt、古いchunk残留 |
| memory naturalness | 検索precision/relevance、採用した記憶、古さ、不自然な持出し |
| persona consistency | 人格/語調の逸脱、emotion整合、用語正確性、snapshot version |
| recovery quality | 誤認識・割り込み後の復帰、stale discard、degraded context、人間の修正発話 |

turn_id/session_idで人間1〜5評価と機械ログをjoinし、対象外はnullにするという設計だった。単純な総合点の重みを確定せず、相関だけでなく失敗例・交互作用を見る。VAD閾値を短くすれば分割誤り、memory top-Kを増やせば混入、contextを厚くすればprefill、hard interruptを強くすれば相槌で止まるというtrade-offを同時に観察する。

自発発話はcandidateが読まれた件数ではなく、人間が返したくなるか、唐突でないか、主語と話題がわかるか、聞き返されても保持できるか、会話2turn以上へ続くかで評価する。DBの候補fetched/spokenとセッション開始、後続turnを同じ因果として追う設計だった。LLM-as-judgeのnaturalness1.0やduplicate0という記録もチューニングの観測であり、人間goldや自動採用gateとは扱わない。

証拠: [評価設計](../v2/_docs/evaluation.md)、[S23/autopilot/LLM judge](../v2/PLAN.md)、[実施LOG](../v2/LOG.md)。

### 実装結果と未採用・未確認を同じ場所に残す

- no-audio/timeout/format failureを隠さず、成功latencyの分母と別に報告する。
- scoreが良くても既定を替えていない比較（9/03 STT、9/27学生、10/02判定器）を明示する。
- 同一発話のprefix漏洩、AI参照を人間正解と呼ぶ問題、人工切断labelの曖昧さ、train/evalの長さ差を記録する。
- parse成功、ready/wait全体一致、false-start、false-wait、pair順位、較正、runtime会話品質をそれぞれ残す。全部WAITなら誤開始0でも会話として失敗する。
- backendのstreaming対応、上流が逐次配送する実装、browserで実再生される観測を別に検証する。
- ベンチマークのための固定ACK、直接時計応答、短文cutoffが何を短縮したのかを残す。内容生成全体が速くなったと一般化しない。
- 一連の知見は再実装の比較基準であり、新構成・モデル・閾値・クラウド利用を確定する指示ではない。

<a id="knowledge-18"></a>

## 再構築に向けた横断的な確認項目

ここは上記の実装経験から導いた**確認候補**であり、新アーキテクチャの採用決定ではない。

### 最小の縦断経路を先に成立させる

1. マイク float32 → `/ws` → VAD/pre-roll → STT observation → 決定 → SpeechOrder → TTS → 実再生、を一つの trace で追えるようにする。
2. その最小経路へ、stop / replace / append、途中の STT 修正、外部情報の遅延、切断を注入する。
3. 「古い候補を再生しない」「停止後に音が復活しない」「二重応答しない」「何も返らなかった測定を成功にしない」を固定する。
4. その上で記憶・人格・candidate・自発発話を戻す。境界未確定のまま周辺機能だけを増やさない。

この順序の根拠は、v1 の多機能化と v2 の再分割を経ても、v2 の後期レビューに
逐次 await、送信待ち、古い snapshot、生成全文の保存という核心課題が残ったこと。
根拠: [v2 設計レビュー](../v2/PLANS/openai.md)、[repository-map](../v2/_docs/openai-plan/repository-map.md)。

### 結果の種類と時刻を分けて記録する

最低限、入力 audio の開始/終端、VAD endpoint、STT first partial/final、
判断、LLM first token/完了、TTS first chunk/完了、WS send、browser receive、
playback start/end、cancel intent/適用/実際の停止を別イベントにする。
実装済みの `trace_id`・decision generation・playback generation が何を同定するかを保ち、
名前があるだけでキャンセルが保証されると判断しない。
これらを全て既に測定済みとするものではなく、現行で欠けた区別を明示する確認項目である。

| ケース | 単に通ったとみなしてはいけない結果 | 合わせて確認するもの |
|---|---|---|
| 相槌が即座に出る | first audio が目標以下 | 本回答開始、誤割り込み、同じ相槌の連発 |
| stop に ACK が返る | handler が成功した | 予約済み source の停止、古い TTS の破棄、再開の有無 |
| replace が届く | order が更新された | 進行中 synthesis と送信 queue が失効したか |
| LLM が文を作る | planned text が保存された | 実際にユーザーが聞いた範囲との差 |
| 無音区間を扱う | 空文字 final でテストが通る | 本物の silence tick・presence・initiative と同じ経路か |
| partial で開始する | 平均 latency が改善した | final 訂正時の撤回、追加情報、意味の不一致 |
| API/モデルが起動する | process/tmux window がある | 実際の readiness、正しい model、first request の成功 |

根拠: [MEMORY](../v2/MEMORY.md)、[O0A contract](../v2/_docs/openai-plan/o0a-contract.md)、
[評価設計](../v2/_docs/evaluation.md)。

### 再利用を急がず、その資産が担っていた役割を保つ

- DTO・protocol は比較的移しやすいが、旧世代の巨大 DTO ファイルを全部持ち込む必要はない。採用する境界と所有権を先に決める。
- VAD pre-roll、音声 chunk framing、停止時のブラウザ source 管理は、失敗を直した理由とテストを一緒に読む。
- v1 の session/summary/persona/lexicon は、テーブル名だけではなく「索引か原本か」「いつ版が変わるか」「何を provenance とするか」を持ち込む。
- 学習済み軽量モデルは feature extraction、閾値、校正、fallback、評価データの位置付けをまとめて資産とする。JSON weight だけでは再現できない。
- fake provider は外部モデルの品質評価用に使わない。テストの責務境界を文書化して使う。
- Makefile の互換 alias は機能の存在証明にしない。v2 には旧コマンド名を残して no-op/smoke hook としたものがある。
- 最新の README が古い境界を要約している場合もあるため、MEMORY の後日追記と実装を突き合わせる。

根拠: [v1 ARCHITECTURE](../v1/ARCHITECTURE.md)、[v2 Makefile](../v2/Makefile)、
[v2 README](../v2/README.md)、[v2 MEMORY](../v2/MEMORY.md)。

### 原型の実装から引き継がない負担

[_reference](../v2/_reference/README.md) の Unity/WebGL 版では、マイク取得制約のため
OGG を作り、Base64 で REST に載せ、サーバーで MP3 に変換し、LLM/TTS の完了後に一括返却していた。
無音待ちも `gaman=1.5秒` で、変換・一括処理・固定待ちが応答を遅くしていた。
float32 の直接送信、サーバー VAD、ストリーム化を選んだのはこの負担を除くためだった。
再構築時に UI/framework の都合から同じ変換列を無意識に復活させない。
ただし「streaming」という名称だけでは、内部で全文/全音声を待つ問題は解決しない。

<a id="knowledge-19"></a>

## プライバシーと実験データの境界

### リポジトリで確認できる境界

`make-model/data/private-log-seeds/` と public synthetic の分離、
公開用 JSON artifact だけを選択的に追跡する ignore、
9月の実験が実会話・JDD・private log を使わなかったことは、保存された資料で確認できる。
私的な失敗例から問題の型を学ぶことと、その本文を公開/クラウド教師データへ入れることは別に扱う。
人工例を作る場合も、実会話を少し言い換えただけで匿名化済みとしない。

根拠: [make-model README](../v2/make-model/README.md)、[v2 .gitignore](../v2/.gitignore)、
[教師比較 REPORT](../v2/_docs/benchmarks/teacher-comparison-20260927/REPORT.md)、
[特徴学習 REPORT](../v2/_docs/benchmarks/feature-learning-20260927/REPORT.md)。

### 過去の別会話から補足する境界（記憶由来）

2026-10-12 の Colab/Gemini 検討の保存記録では、実会話・private log・JDD・記憶・画面情報を
クラウド教師/推論へ送らず、公開情報・人工例のバッチ処理から検討する方針が整理されていた。
これはこの作業で API の現行規約を再確認した結果ではなく、過去の会話の記憶からの補足である。
規約や料金の数字は引き継がず、採用時に公式情報で確認する。

そこで得た設計上の教訓は、モデルを自分で動かしても、外部 GPU 上へ入力を送ればデータの所在はローカルではなくなること、
資源・寿命が変動する実験環境を常時稼働の会話入口の前提にしないこと、
課金区分や「学習に使わない」という説明だけで外部に痕跡が残らないと推定しないこと。
今後比較する際は、生成速度だけでなく通信待ち・失敗時の復帰・データの所在も比較対象にする。

当時参照した公式ページ: [Colab FAQ](https://research.google.com/colaboratory/faq.html)、
[Gemini API terms](https://ai.google.dev/gemini-api/terms)、[Gemma guide](https://ai.google.dev/gemma/docs/get_started)。
記憶記録の識別子: `01a12e04-c0c9-78b1-974f-69eeaece3dae`（2026-10-15 保存）。

<a id="knowledge-20"></a>

## 今回の保管と再実行時の注意

### ディレクトリの状態

```text
tomoko/
  .git/                   同じ履歴・master を維持
  .gitignore              v1 の既存除外を保ち、ローカル比較実験を保護
  AGENTS.md               既存本文を維持し、配置変更の説明のみ追記
  README.md               世代別資料への入口
  _docs/knowlaedge.md      この知見集
  v1/                     元の v1。内容を変更していない
  v2/                     今回まで root にあった現行プロジェクト
    _docs/                既存の全資料・benchmark
    LOG.md / MEMORY.md    既存本文を維持し、今回の作業だけ追記
    PLAN.md               既存本文を維持し、保管の完了条件を追記
    .venv/ logs/ models/  ローカル環境・成果物も移動（Git 管理外）
    comparisons/          既存の未追跡比較実験も移動（Git 管理外）
    v1 -> ../v1           既存の v1/loras/... 参照を保つ互換リンク
```

移動前は追跡ファイル 795 件（v1 399 件、現行 396 件）。
現行の top-level 44 項目を、同じ filesystem 上の rename で v2 へ移した。
削除して再生成する手順は採っていない。`.git` を移して別リポジトリにすることもしていない。
root の AGENTS と .gitignore は管理のため保持し、旧版そのものも v2 内に保管した。
旧 README は v2 に置き、新しい root README を入口として作成した。

### ファイル保全の確認

- 移動前の追跡 795 ファイルの SHA-256 を取得。移動先で、今回追記した LOG/MEMORY/PLAN/latency を除く内容一致を確認した。
- v1 の追跡 399 ファイルはすべて一致。v1 を移動・編集していない。
- ignored/untracked を含む 50,852 エントリについて、移動先の存在と同じ inode での保管を確認した。欠落・inode変更は 0。
- `.venv`、実ログ、モデル、private seed、カレンダー URL、既存の比較環境を新規追跡していない。
- ベースライン unit は移動前 312 passed / 7 deselected（2.52秒）。移動後も同じ検証を実施し、最終結果は [LOG](../v2/LOG.md) に記録した。
- 今回は実機会話・音声性能・外部サービス連携の再測定をしていない。この知見集の latency 値は過去の条件付き実測。

### 保存と起動可能性を区別する

1. **既存 venv の絶対パス**: `.venv/bin/pytest` 等の shebang は旧 root を含む。
   同じ Mac での確認には `cd v2 && .venv/bin/python -m pytest -m unit -q` を使った。
   新しい環境では `cd v2 && uv sync --frozen`。移動済み venv の console script を使う場合は、
   古い環境を別名で保管して環境を再構築するか、パッケージを再インストールして entry point を作り直す。
   今回は大規模な環境再作成を行わず、元環境の保存を優先した。
2. **v1 のモデル依存**: `DFLASH_26B_MODEL`、`config/v2.toml`、教師比較コードの `v1/loras/...` は
   `v2/v1 -> ../v1` で解決する。大型モデル自体は Git 外なので別マシンでは別途用意が必要。
3. **Docker DB は外部状態**: [Compose](../v2/docker/docker-compose.yml) は `postgres-data` を named volume に持つ。
   v2 に移って既定 project 名が変わると別 volume を選ぶ可能性がある。
   既存 container の Compose label と volume を先に確認し、旧 project 名を明示して再利用する。
   当時の root 名がそのまま project 名だった場合の例は `COMPOSE_PROJECT_NAME=tomoko` だが、
   明示指定や別名があった環境へ無確認で適用しない。今回 DB の停止・移行・削除・dump はしていない。
4. **外部プロセス/パス**: async-voicevox、dflash、Argmax CLI、OS権限、外部 research operator、
   Hugging Face cache 等はこの archive の中に全て入るものではない。旧 Makefile・config・launcher を確認する。
5. **計測資料の可搬性**: benchmark report の内部に古い絶対パスや command が残っていても、実験原記録を一括置換していない。
   追試時は v2 を基準に読み替え、元の測定条件自体は書き換えない。

<a id="knowledge-21"></a>

## 次の設計時に明示的に決めること

- 新 runtime の最小構成と、判断 owner / 身体実行 owner / durable memory owner。
- 「返答が始まった」と「本回答が始まった」の測定定義、遅延目標、許容する誤開始・待ちすぎ。
- cancel/replace と generation の失効規則、遅いモデル・queue・DB に対する上限。
- ユーザーが聞いていない planned text を会話原本へ入れない保存契約。
- STT の実マイク corpus、partial/final 差、固有語、同一条件での backend 比較。
- v1 の記憶資産と v2 の process 境界の、どこを再利用するか。
- semantic 判定の人間ラベル、曖昧例の扱い、新しい未見評価、fallback と閾値の決め方。
- 会話/画面/予定をローカルに保つ範囲と、公開人工実験の配布範囲。

これらは今回のアーカイブ作業で決定していない。
過去の不具合と実測を選択の材料にし、古い Phase の続きがそのまま新実装の要件になると考えない。
