# Summary

## Wake Word Detection Issue

### 原因と修正内容
1. **「ともく」や「聞こえますか？」が別々に記録される問題**
   - **原因**: `config/central_realtime.toml` での `vad_silence_ms` の設定が `400`ms と短く、「ともこ、」の後の読点のポーズで音声が分割されてしまっていました。
   - **修正**: `vad_silence_ms` を `800`ms に増やし、ポーズで途切れないようにしました。また、エッジサーバー (`server/edge/main.py`) が `create_vad_processor` を呼び出す際に、設定ファイルの値ではなくデフォルトの 400ms を固定で使ってしまうバグがあったため、設定値を正しく注入するように修正しました。

2. **UI上で `participation:called` が確認できない問題**
   - **原因**: サーバーはウェイクワードを正しく検知して `participation:called` イベントをクライアントに送信していましたが、その直後にVADが `idle` 状態に遷移したことを知らせる `state:idle` イベントも送信するため、UIが `participation:called` を即座に上書きしてしまっていました。（「ともく」はウェイクワード辞書に含まれていたため、実は検知自体は成功していました）
   - **修正**: クライアント (`client/main.js`) が `state:idle` イベントを受け取っても、現在の状態が `participation:` で始まる場合はUIを上書きせずに維持するように変更しました。これにより、次にユーザーが話し始めて `listening` に遷移するまで、UI上で `participation:called` を確認できるようになります。

### 確認事項
- 設定ファイル `vad_silence_ms` を読み込むように修正
- `ruff check .` および `pytest tests/unit` が正常に通過することを確認
- `MEMORY.md` の VAD 無音閾値の記録を 800ms に更新

## 2026-05-23 M1 Phase 0

Final state:

- Added Python/uv project setup, pytest markers, ruff configuration, and `uv.lock`.
- Added `config/central_realtime.toml` with Ollama `qwen2.5:7b`, MLX Qwen 7B fallback metadata, faster-whisper small, Silero VAD, and macOS `say` Kyoko TTS settings.
- Added PostgreSQL Docker setup with PGroonga base image and pgvector installation.
- Added minimal `NodeConfig` loader and unit tests for Phase 0 configuration.
- Verified PostgreSQL container is healthy and has `pgroonga` / `vector` extensions enabled.
- Verified Ollama `qwen2.5:7b`, MLX Qwen 7B load, faster-whisper small load, Silero VAD load, and `say -v Kyoko` output.
- `irodori-tts` remains human-confirmation pending because it is not available via Homebrew/PyPI and appears to be a separate GitHub project.

Verification:

- `mise exec -- uv run pytest -m unit` -> 3 passed
- `mise exec -- uv run ruff check .` -> passed
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -Atc "SELECT extname FROM pg_extension WHERE extname IN ('vector','pgroonga') ORDER BY extname;"` -> `pgroonga`, `vector`
