# OpenAI Plan O0A-00 Repository Map

調査時点: 2026-08-12

- 対象: Tomoko v2 root（`v1/` は対象外）
- HEAD: `6071e50b0246707e23e69a38796530153a048218`
- 開始時 worktree: tracked file の未コミット差分なし
- branch: `master`（`origin/master` より 1 commit ahead）
- 調査方法: codebase-memory-mcp の既存 index
  `Users-seijiro-Sync-sync_work-by-llms-tomoko` を主に使用し、文字列・設定・
  non-code の確認だけ `rg` を使用した。

## 必須 component の一意な対応

| logical component | exact path | exact symbol | owner | current tests | queue / lock / await | current IDs / logs |
|---|---|---|---|---|---|---|
| shared speech order DTO | `server/shared/models.py` | `SpeechOrder` | tomoko-process が生成、hot-path が実行 | `tests/unit/test_v2_models.py`, `tests/unit/test_v2_speech_order_flow.py`, `tests/unit/test_v2_internal_ws.py` | queue なし | `id`, `supersedes_order_id`, `scheduler_decision_id`, `trace_id`, `created_at`; `speech_order_created`, `speech_order`, `speech_order_received` |
| hot-path public WS | `server/hot_path/app.py` | `websocket_endpoint` | hot-path-process | `tests/unit/test_v2_runtime_foundation.py`, `tests/unit/test_v2_audio_tomoko_prompt.py` | receive loop と別 task の result sender。session `result_queue` は unbounded | `observation_id`, `order_id`, `request_id`, `trace_id`; `ws_connected`, `client_event`, `audio_chunk` |
| partial lane | `server/hot_path/app.py` | `AudioPartialLane` | hot-path-process | `tests/unit/test_v2_audio_tomoko_prompt.py` | `_queue maxsize=256`; full 時 oldest drop、同 generation coalesce。`result_queue.put` は await | private integer `_generation`; dedicated log なし |
| final lane | `server/hot_path/app.py` | `AudioFinalLane` | hot-path-process | `tests/unit/test_v2_audio_tomoko_prompt.py` | `_queue maxsize=4`; full 時 oldest final drop。partial idle を最大 1.5 s await 後、`process_segment` と `result_queue.put` を await | segment DTO の IDs; dedicated log なし |
| TurnMaterials latest value | `server/hot_path/turn_materials.py`, `server/tomoko/turn_state.py` | `TurnMaterialAggregator`, `TurnMaterialState` | 集約は hot-path、latest read model は tomoko-process | `tests/unit/test_v2_internal_ws.py`, `tests/unit/test_v2_speech_order_flow.py` | `TurnMaterialState._lock` で update/get。hot-path client 側は latest-wins | `TurnMaterials.id`, `trace_id`; `turn_materials_snapshot`, `turn_materials`, `turn_materials_cached/send/ack` |
| internal WS client | `server/hot_path/ws_control.py` | `RemoteTomokoWsCore` | hot-path-process | `tests/unit/test_v2_internal_ws.py` | 単一 `_lock` で request/response を直列化。`ws.send`, ack と order `ws.recv` を await | observation `id/trace_id`, order IDs; `ws_connecting/connected/reconnecting` |
| internal WS server | `server/tomoko/realtime.py` | `hot_path_realtime` | tomoko-process | `tests/unit/test_v2_internal_ws.py`, `tests/unit/test_v2_calendar_append.py` | connection receive loop 内で core、DB persistence、ack/order send を順番に await | observation/material/order IDs; `stt_observation`, `turn_materials`, `initiative_tick` |
| remote Tomoko core | `server/hot_path/ws_control.py` | `RemoteTomokoWsCore` | hot-path-process proxy | `tests/unit/test_v2_internal_ws.py` | 上記 `_lock`; followup poll は `shield` して protocol cycle 完走 | `trace_id` を observation から STOP/order へ維持 |
| conversation core | `server/tomoko/conversation.py` | `TomokoConversationCore.handle_observation` | tomoko-process | `tests/unit/test_v2_speech_order_flow.py`, `tests/unit/test_v2_semantic_scheduler.py` | core 内の重い LLM/sense path を await。単一 class に会話 state を保持 | observation/session/order/decision/trace IDs; `speech_order_created`, gate logs |
| LLM fire gate | `server/tomoko/gates.py` | `LlmFireGate` | tomoko-process | `tests/unit/test_v2_semantic_scheduler.py` | queue/lock なし、pure decision | gate output `trace_id`; score/reason/breakdown |
| speech emission gate | `server/tomoko/gates.py` | `SpeechEmissionGate` | tomoko-process | `tests/unit/test_v2_semantic_scheduler.py` | queue/lock なし、pure decision | gate output `trace_id`; score/reason/breakdown |
| speech executor | `server/hot_path/speech_executor.py` | `SpeechOrderExecutor` | hot-path-process | `tests/unit/test_v2_speech_order_flow.py`, `tests/unit/test_v2_audio_tomoko_prompt.py` | `append_queue` は unbounded list。TTS async generator と `on_chunk` を同 task で await | `current_generation` は process-local int; order `id/trace_id`; `speech_order_received/queued/stopped/audio_ready` |
| result queue | `server/hot_path/app.py` | local `result_queue` in `websocket_endpoint` | hot-path-process / browser WS connection | indirect coverage in audio/runtime tests | `asyncio.Queue()` で maxsize=0（unbounded）。single `_send_audio_result_queue` consumer | result DTO 内 IDs; queue age/size/drop log なし |
| deferred TTS path | `server/hot_path/app.py`, `server/hot_path/speech_executor.py` | `_send_audio_conversation_result`, `_send_prompt_execution_result`, `SpeechOrderExecutor.execute_stream` | hot-path-process | `tests/unit/test_v2_audio_tomoko_prompt.py` | ordered sender が order ごとに `execute_stream` を awaitし、各 chunk の `websocket.send_bytes` も await | `request_id=order.id`, `trace_id`; `prompt_result_deferred`, `audio_chunk`, `tts_result` |
| browser audio scheduler | `client/main.js` | `playAudioChunk`, `stopLocalPlayback`, `fadeOutAndCutPlayback` | browser（物理再生のみ） | static contract in `tests/unit/test_v2_runtime_foundation.py` | `playbackTime` と `activeAudioSources`; Web Audio scheduling。server telemetry queue なし | order/generation association なし; console `audio_play/audio_stop/audio_replace_fade` のみ |
| sense request record | `server/tomoko/sense.py` | `SenseRequestRecord` | tomoko-process creates; info-acquire completes DB record | `tests/unit/test_v2_sense_requests.py` | DB polling path は 0.5 s sleep、kind ごとの timeout まで await | `id`, `trace_id`, requested/completed timestamps |
| canonical utterance store | `server/shared/models.py`, `server/tomoko/db_bridge.py`, `server/tomoko/realtime.py` | `DurableUtterance`, `insert_utterance_sql`, `_persist_result` | tomoko-process / PostgreSQL | `tests/unit/test_v2_semantic_scheduler.py`, `tests/unit/test_v2_speech_order_flow.py` | internal WS receive loop が `_persist_result_if_enabled` を await | `id`, `session_id`, `stt_observation_id`, `trace_id`, `created_at`; `durable_utterance`, `persist_failed` |
| context snapshot | `server/shared/models.py`, `server/tomoko/context.py` | `ContextSnapshot`, `ContextSnapshotBuilderV2` | tomoko-process | `tests/unit/test_v2_models.py`, `tests/unit/test_v2_speech_order_flow.py` | calendar cache is mutable per builder; build is synchronous | `id`, `session_id`, `trace_id`, `created_at`; explicit build log なし |
| closed-session summary | `server/summary/main.py` | `materialize_summaries_from_db`, `summarize_session` | summary-process / PostgreSQL | `tests/unit/test_v2_background_models.py` | closed sessions を DB から load、summary/embedding inserts を await | `SessionSummary.id/session_id/trace_id`; process logs |
| scenario runner | `scripts/v2_scenario_replay.py`, `scripts/v2_autopilot.py` | `run_scenario`, `main` | evaluation tooling | `tests/unit/test_v2_scenario_replay.py` | scenario watchdog/task cancellation を所有 | artifact 内 event/trace fields |
| latency artifact writer | `scripts/v2_latency_suite.py` と各 smoke script | `main`, `append_latency_log` family | evaluation tooling | `tests/unit/test_v2_latency_suite.py` | runtime queue なし | current metrics は feedback/content taxonomy 未分離 |

## 現在の通常 call chain

```text
browser startMicrophoneCapture / audio-worklet
  -> public /ws websocket_endpoint.receive (binary float32)
  -> process_audio_samples
     -> AudioPartialLane.submit -> process_streaming_partial
     -> AudioFinalLane.submit -> process_segment
  -> RemoteTomokoWsCore.handle_observation
     -> internal WS: turn_materials + playback_state + stt_observation
  -> server.tomoko.realtime.hot_path_realtime
  -> TomokoConversationCore.handle_observation
  -> pressure models -> LlmFireGate
  -> chat backend / LLM
  -> SpeechEmissionGate
  -> SpeechOrder
  -> _persist_result_if_enabled (awaited before internal WS ack/order delivery)
  -> internal WS ack then speech_order
  -> RemoteTomokoWsCore returns TomokoConversationResult
  -> partial/final lane -> unbounded result_queue
  -> single _send_audio_result_queue
  -> _send_audio_conversation_result
  -> SpeechOrderExecutor.execute_stream / TTS
  -> _send_streamed_audio_chunk -> public websocket.send_bytes
  -> client playAudioChunk -> Web Audio playbackTime schedule
```

## STOP / REPLACE の現在の call chain

```text
STOP text observation
  -> TomokoConversationCore detects stop intent
  -> SpeechOrder(mode=stop)
  -> realtime persists result
  -> internal WS cancel_order
  -> RemoteTomokoWsCore.stop_order_from_cancel_event
  -> result_queue / ordered sender
  -> public speech_order(mode=stop) event
  -> SpeechOrderExecutor.stop_playback: generation++, current clear, append clear
  -> browser stopLocalPlayback: acceptingAudio=false, active sources stop

REPLACE candidate
  -> SpeechEmissionGate / TomokoConversationCore
  -> SpeechOrder(mode=replace_current)
  -> realtime persists result
  -> internal WS speech_order
  -> result_queue / ordered sender
  -> public speech_order(mode=replace_current)
  -> browser fadeOutAndCutPlayback
  -> SpeechOrderExecutor.replace_generation + append clear
  -> new TTS stream; old generation chunks are discarded at executor check
```

STOP/REPLACE とも TTS synthesis task 自体を cancel しない。generation invalidation 後も
backend generator は走り得て、executor が得た旧 chunk を discard する。さらに ordered sender が
`execute_stream` を await 中は、同じ `result_queue` の後続 STOP/REPLACE を consume できない。

## DB persistence と order delivery の順序

現行 internal WS 主経路では `hot_path_realtime` が
`conversation_core.handle_observation()` の後、`await _persist_result_if_enabled(result)` を完了してから
`stt_observation_ack` と `speech_order` / `cancel_order` を送る。DB connect/insert failure は
`persist_failed` として捕捉され fail-open するが、成功時の DB latency は order delivery を直接 gate する。

`_persist_result` は STT observation、session、user durable utterance、saturation、scheduler decision、
prompt request、speech order、さらに Tomoko の planned text を assistant durable utterance として順に
await insert する。現在は browser playback 開始/完了の観測がなく、未再生または cancel 予定の全文でも
delivery 前に canonical utterance として保存され得る。

## queue / lock / await graph

```text
public WS receive task
  -> partial queue (256, oldest drop/coalesce) -> partial worker
  -> final queue (4, oldest drop) -> final worker (partial idle <=1.5s await)
  -> result_queue (unbounded)
       -> single result sender
          -> execute_stream
             -> TTS async generator
             -> websocket.send_bytes (await per chunk)

RemoteTomokoWsCore._lock
  -> latest materials send/ack
  -> playback state send/ack
  -> observation send/ack
  -> N order recv

tomoko internal WS receive loop
  -> optional DB candidate/summary refresh await
  -> conversation/LLM/sense await
  -> persistence DB await
  -> ack/order send await
```

## `openai.md` との差分

- 提案が指摘した unbounded session `result_queue`、final oldest-drop、再生 telemetry 不在、
  persistence-before-delivery は現コードでも確認できた。
- `SpeechOrder` の origin identity は現在 `trace_id` が担う。同じ因果意味として再利用可能だが、
  `decision_generation_id`, `response_kind`, `deadline_at`, hot-path 所有の
  `playback_generation_id` は未実装。
- browser は STOP/REPLACE の物理制御を既に持つが、`playback_started`, `playback_ended`,
  `buffered_until`, `client_event_seq` を server へ返していない。
- `openai.md` の行番号は snapshot 固有なので、後続 Task は本 map の exact path/symbol を使う。

## O0A-01 の exact allowlist 候補

人間が O0A-01 を許可した場合、repository map から一意に決まる runtime/test path は次である。

- `server/shared/models.py` — `ResponseKind` と `SpeechOrder.response_kind`
- `server/tomoko/conversation.py` — すべての `SpeechOrder` producer mapping
- `server/hot_path/backchannel.py` または current backchannel result construction path
- `tests/unit/test_v2_models.py`
- `tests/unit/test_v2_speech_order_flow.py`
- `tests/unit/test_v2_hot_path_backchannel.py`

実際の O0A-01 allowlist は人間 unlock 後、その Task 開始時にこの候補を最小化して確定する。
