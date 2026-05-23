# MEMORY.md

セッションをまたいで有効な判断・気づき・未解決疑問を記録する。
LOG.md が時系列なのに対して、こちらはトピックごとに整理する。

---

## 確定した判断

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

### 感情表現プロトコル
- 自前プロトコル採用（Phase 6a）
  ```
  EMOTION:happy
  本文テキスト
  ```
- partial JSON parser 方式（Phase 6c）は品質が不安定な場合のみ移行

### Git 運用
- コミットは自由、origin への push は人間のみ
- テストが通る単位でコミット

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
