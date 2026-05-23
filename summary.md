# Summary

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
