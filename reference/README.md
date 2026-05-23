# reference/

過去の実装経験から得た参考コードを置くディレクトリ。
**そのまま使うのではなく、設計の参考として読む。**

## 何のためにあるか

今回の設計が解決しようとしている課題が、ここに記録されている。
同じ苦労を繰り返さないために、実装前に読む。

## ファイル一覧

### unity/MyAIRoomScript.cs
- VRM + WebGL + マイク入力 + AI 会話の Unity 実装
- 音量閾値 VAD（gaman=1.5秒の無音待機）
- OGG エンコード → Base64 → REST POST の苦肉の策
- FrostweepGames/MicrophonePro が必要だった（WebGL でマイクが取れなかった）

**今回との対比**:
- gaman → Silero VAD（300〜400ms）
- OGG/Base64 → float32 を WebSocket で直接流す
- REST 一括 → ストリーミングパイプライン

### server/api.py
- 旧サーバー実装（FastAPI）
- OGG → MP3 変換（pydub）を挟んでから Whisper に渡していた
- LLM + TTS を一括処理して Base64 で返していた
- get_response_wave_from_text がボトルネックだった

**今回との対比**:
- convert_ogg_to_mp3 → 不要（float32 をそのまま扱う）
- 一括返却 → ストリーミング
- Base64 → バイナリ WebSocket
