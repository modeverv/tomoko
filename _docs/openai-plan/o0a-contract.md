# O0a Current Contract Baseline

Task O0A-00 で確認した、変更前の契約である。新契約を実装した記録ではない。

## Ownership

- tomoko-process owns interpretation, pressures, `LlmFireGate`, LLM content,
  `SpeechEmissionGate`, `SpeechOrder`, session/context and persistence decisions.
- hot-path-process owns public `/ws`, VAD/STT physical handling, internal WS proxy,
  speech execution, TTS, playback generation integer and ordered browser delivery.
- browser owns microphone capture and physical Web Audio scheduling/cut only. It does
  not own floor, retry, freshness or canonical state.
- PostgreSQL is the durable store, but current realtime persistence is synchronously
  awaited before speech-order delivery.

## Current DTO and ID contract

`SpeechOrder` currently has `text`, `mode`, `reason`, `priority`, `id`,
`supersedes_order_id`, `scheduler_decision_id`, `trace_id`, and `created_at`.

- `trace_id` already propagates origin causality across observation/order/audio and is
  the candidate for the proposed `origin_trace_id` semantic role.
- There is no `ResponseKind`.
- There is no tomoko-owned `decision_generation_id`.
- `SpeechOrderExecutor.current_generation` is a hot-path-local integer, but is not
  exposed as a `playback_generation_id`.
- Browser audio chunks are not associated with order/generation in client state.

## Current overload contract

| lane | capacity | current full policy |
|---|---:|---|
| partial | 256 | drop oldest queued item, then coalesce same generation |
| final | 4 | drop oldest final silently |
| connection result queue | unbounded | no overload policy or queue age metric |
| executor append queue | unbounded list | FIFO after current order |
| TurnMaterials | latest cached value | latest-wins |

This table describes current behavior, not the target safety policy.

## Current emission and playback contract

- The server emits `speech_order` JSON before binary chunks for a result.
- Deferred TTS is executed by the single result sender; each chunk is sent with an
  awaited `websocket.send_bytes`.
- STOP/REPLACE increments the executor generation and filters later old chunks, but
  does not cancel the backend synthesis task.
- Client `playAudioChunk` schedules decoded WAVs against `playbackTime`.
- Client STOP cuts active sources; REPLACE fades/cuts and resets the schedule.
- Client sends no playback observation. Therefore actual playback start/end and
  buffered horizon are unknown to the server.

## Current persistence contract

The internal WS server awaits persistence before ack/order delivery. Planned Tomoko
text can be inserted as a durable assistant utterance before browser playback is
observed. Since playback telemetry does not exist, current durable assistant records
cannot distinguish planned, sent, started, completed, or cancelled speech.

## Gaps to be addressed by later Task IDs

- deterministic `ResponseKind` assignment at each producer
- explicit decision/playback generations and emission-time freshness checks
- milestone events and pure latency aggregation
- browser playback observation on the existing public `/ws`
- bounded result/control queues with no-drop policies for final/control/canonical work
- cancellable TTS and preemptible ordered output
- canonical assistant persistence based on observed substantive playback

No runtime behavior, test, config, schema, or client code was changed in O0A-00.
