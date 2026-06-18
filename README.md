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
make tmux-runtime
make v2-report-latest
make tmux-stop
```

`make tmux-runtime` is the v2 equivalent of the v1 runtime launcher. It starts
dflash LLM windows, the sibling VOICEVOX streaming runtime, the hot-path server,
and the separated v2 background processes. The short aliases from v1 are kept:

```bash
make run    # alias for make tmux-runtime
make a      # attach tmux
make stop   # stop dflash and the tmux runtime
```

Useful focused runtime commands:

```bash
make llm-run
make llm-stop
make voicevox-run
make v2-runtime-ready
make v2-ocr-smoke
make v2-llm-tts-smoke
make v2-conversation-smoke
make v2-scheduler-conversation-smoke
make v2-scheduler-say-latency-smoke
make v2-scheduler-report
```

The default main LLM path follows the v1 measured route: dflash on
`127.0.0.1:8082` with `v1/loras/lora/fused_model` and
`z-lab/gemma-4-26B-A4B-it-DFlash`. Summary/background LLM defaults to the 31B
dflash route on `127.0.0.1:8081`. VOICEVOX defaults to the sibling
`async-voicevox` streaming command and `127.0.0.1:50122`. Tomoko's VOICEVOX
speech speed defaults to `TOMOKO_V2_VOICEVOX_SPEED=1.5`.

STT defaults to the root Apple Speech sidecar under
`.cache/tomoko/AppleSpeechSTT.app`, built from `scripts/apple_speech_stt/` on
first use. OCR prefers the root Vision.framework sidecar from
`scripts/vision_ocr/` and falls back to `tesseract`. `make v2-conversation-smoke`
starts a local hot-path server plus the tomoko heartbeat process with fake
runtime providers, then sends float32 audio bytes over `/ws` to verify the
VAD pre-roll -> STT -> tomoko adoption -> prompt -> binary WAV response path.

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
