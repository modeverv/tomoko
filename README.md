# Tomoko v2

Tomoko v2 is a rebuild of the local voice conversation runtime based on the
lessons preserved in `v1/`. The v2 runtime keeps the browser thin, keeps
PostgreSQL as the source of truth, and separates hot audio/model execution from
the process that owns personality, floor control, and session boundaries.

## Process Map

- `hot-path-process`: owns `/ws`, microphone audio, VAD/STT observations, model
  execution, TTS chunks, and browser delivery. It does not decide whether Tomoko
  should speak.
- `tomoko-process`: owns durable utterances, conversation sessions, prompt
  requests, floor state, and deterministic speech decisions.
- `think-process`: turns summaries, world information, calendar facts, and user
  status into candidate records.
- `info-aquire-process`: imports calendar and world information into v2 DB
  tables.
- `user-status-aquire-process`: captures screen/OCR/OS metadata and writes
  structured observations.
- `summary-process`: creates index-like summaries and embeddings outside the hot
  path.
- `evaluation-process`: records latency and quality evidence for later reports.

## Running

```bash
make check
make db-up
make v2-runtime
make v2-report-latest
make v2-stop
```

`v1/` is reference-only. When a v1 implementation detail is needed, move the
smallest required idea or file into the v2 root explicitly and keep the new v2
contract in root docs/tests.

## Development Rules

- `/ws` remains the only browser communication endpoint.
- The browser only sends microphone audio, plays audio chunks, and renders JSON
  events.
- Cross-layer values use DTOs from `server/shared/models.py`; audio hot loops are
  the explicit exception.
- `LISTEN/NOTIFY` payloads are UUID strings only.
- Deterministic models own floor control; LLMs do not decide whether Tomoko may
  speak.
