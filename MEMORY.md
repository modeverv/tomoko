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
