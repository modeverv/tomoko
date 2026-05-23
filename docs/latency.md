# Latency Measurements

M1 Phase 0 creates this log before the first measured audio path exists.

| Date | Phase | Target | Result | Notes |
|---|---|---:|---:|---|
| 2026-05-23 | M1 Phase 0 | unit tests pass | pass | `pytest -m unit`: 3 passed in 0.01s. Runtime setup only; E2E measurement starts in Phase 1. |
| 2026-05-23 | M1 Phase 1 | local `/ws` float32 echo round trip | avg 0.13ms / p95 0.15ms | 100 chunks, 512 float32 samples per chunk, `uvicorn server.edge.main:app --host 127.0.0.1 --port 8000`. Browser mic/audio output latency still requires manual Chrome check. |
| 2026-05-23 | M1 Phase 2 | VAD silence threshold detection | 300ms -> 320ms / 400ms -> 416ms / 500ms -> 512ms | Synthetic scorer, 512 samples per chunk at 16kHz. Detection is quantized to 32ms chunks; real microphone + Silero model timing still requires manual Chrome check. |
| 2026-05-23 | M1 Phase 3 | WakeWordJudge + ambient_logs write | judge avg 0.0006ms / p95 0.0006ms; DB avg 4.74ms / p95 15.69ms | Wake word measured 1000 calls in-process. DB measured 20 async inserts to local PostgreSQL, then deleted marker rows. STT model latency still needs real utterance measurement with faster-whisper. |
| 2026-05-23 | M1 Phase 3 | no-echo WebSocket regression | pass | `pytest -m unit`: 14 passed in 0.11s. Echo playback removed after Phase 2 manual Chrome check; no audio round trip is measured in Phase 3 because mic input is no longer returned to the client. |
