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
- 無音閾値: 400ms を基準に実測で調整
  （300ms だと「えーっと」で誤検出する可能性）
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
