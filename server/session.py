from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import numpy as np

from server.edge.participation.base import ParticipationJudge
from server.edge.pipeline.stt import SpeechTranscriber, supports_streaming
from server.edge.pipeline.stt_filter import TranscriptFilter
from server.edge.pipeline.stt_gate import SttAudioFrontend, SttSignalGate
from server.edge.pipeline.vad import VADProcessor
from server.gateway.audio_turn import AudioTurnController
from server.gateway.context import ContextSnapshotBuilder
from server.gateway.initiative_feedback import (
    CandidateFeedbackStore,
    classify_feedback,
    feedback_scope_from_candidate,
)
from server.gateway.reply import ReplyPipeline
from server.gateway.reply.speech_normalizer import ReplySpeechNormalizer
from server.gateway.stop_ack import StopAckAudioProvider
from server.gateway.stop_intent import (
    StopIntentStore,
    build_stop_observation,
    should_adopt_stop_signal,
    should_record_stop_intent_candidate,
)
from server.gateway.thinking.base import ThinkingMode
from server.gateway.thinking.selector import has_deep_memory_cue, should_use_deep_memory
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.gateway.turn_taking.judge import RuleFirstTurnTakingJudge, TurnTakingJudge
from server.session_carryover import (
    RetrievedContextCarryoverState,
    retrieved_context_key,
)
from server.session_latency import LatencyProbeState, elapsed_ms
from server.shared.candidate import ArrivalCandidate, UtteranceCandidate
from server.shared.db import AmbientLogWriter, ConversationLogWriter, ConversationSessionStore
from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.inference.router import InferenceRouter
from server.shared.inference.tts.base import TTSBackend
from server.shared.memory import ConversationMemoryStore, ConversationSessionSummaryStore
from server.shared.models import (
    AttentionMode,
    AudioChunkOut,
    BargeInContext,
    BargeInDecision,
    CandidateFeedbackScope,
    CandidateSpeakDecision,
    ConnectedOutputState,
    ContextBuildPolicy,
    ContextDepth,
    ConversationLogStatus,
    ConversationTurn,
    MemoryHit,
    ParticipationContext,
    ParticipationMode,
    PlaybackTelemetry,
    SessionCommand,
    SessionEvent,
    SessionSummaryHit,
    SpeechSegment,
    StartReason,
    StateEmission,
    ThinkingInput,
    TomokoContextSnapshot,
    TomoroRuntimeState,
    Transcript,
    TransitionResult,
    TTSInput,
    TurnTakingAudioMetrics,
    TurnTakingInput,
)
from server.shared.persona import PersonaSnapshotStore

SessionState = Literal["idle", "listening", "processing"]

logger = logging.getLogger(__name__)

class TomoroSession:
    def __init__(
        self,
        *,
        vad_processor: VADProcessor,
        send_event: Callable[[dict[str, Any]], Any],
        send_audio: Callable[[bytes], Any] | None = None,
        transcriber: SpeechTranscriber | None = None,
        participation_judge: ParticipationJudge | None = None,
        ambient_log_writer: AmbientLogWriter | None = None,
        conversation_log_writer: ConversationLogWriter | None = None,
        conversation_session_store: ConversationSessionStore | None = None,
        router: InferenceRouter | None = None,
        thinking_mode: ThinkingMode | None = None,
        deep_thinking_mode: ThinkingMode | None = None,
        tts_backend: TTSBackend | None = None,
        embedding_backend: EmbeddingBackend | None = None,
        memory_store: ConversationMemoryStore | None = None,
        session_summary_store: ConversationSessionSummaryStore | None = None,
        persona_store: PersonaSnapshotStore | None = None,
        context_snapshot_builder: ContextSnapshotBuilder | None = None,
        speech_normalizer: ReplySpeechNormalizer | None = None,
        barge_in_detector: BargeInDetector | None = None,
        turn_taking_judge: TurnTakingJudge | None = None,
        transcript_filter: TranscriptFilter | None = None,
        stt_audio_frontend: SttAudioFrontend | None = None,
        stt_signal_gate: SttSignalGate | None = None,
        candidate_feedback_store: CandidateFeedbackStore | None = None,
        stop_intent_store: StopIntentStore | None = None,
        stop_ack_audio_provider: StopAckAudioProvider | None = None,
        connected_output_state: ConnectedOutputState | None = None,
        engaged_timeout_ms: int = 8000,
        cooldown_timeout_ms: int = 8000,
        playback_echo_grace_ms: int = 1200,
    ) -> None:
        self.vad_processor = vad_processor
        self.send_event = send_event
        self.send_audio = send_audio
        self.transcriber = transcriber
        self.participation_judge = participation_judge
        self.ambient_log_writer = ambient_log_writer
        self.conversation_log_writer = conversation_log_writer
        self.conversation_session_store = conversation_session_store
        self.router = router
        self.thinking_mode = thinking_mode
        self.deep_thinking_mode = deep_thinking_mode
        self.tts_backend = tts_backend
        self.embedding_backend = embedding_backend
        self.memory_store = memory_store
        self.session_summary_store = session_summary_store
        self.persona_store = persona_store
        self.context_snapshot_builder = context_snapshot_builder
        self.speech_normalizer = speech_normalizer
        self.barge_in_detector = barge_in_detector
        self.turn_taking_judge = turn_taking_judge or RuleFirstTurnTakingJudge()
        self.transcript_filter = transcript_filter
        self.stt_audio_frontend = stt_audio_frontend or SttAudioFrontend(
            sample_rate=getattr(vad_processor, "sample_rate", 16000),
            signal_gate=stt_signal_gate,
        )
        self.candidate_feedback_store = candidate_feedback_store
        self.stop_intent_store = stop_intent_store
        self.stop_ack_audio_provider = stop_ack_audio_provider or StopAckAudioProvider()
        self.state: SessionState = "idle"
        self.attention_mode: AttentionMode = "ambient"
        self.latest_segment: SpeechSegment | None = None
        self._attention_idle_ms = 0.0
        self._engaged_timeout_ms = engaged_timeout_ms
        self._cooldown_timeout_ms = cooldown_timeout_ms
        self.audio_turns = AudioTurnController(
            playback_echo_grace_ms=playback_echo_grace_ms
        )
        self._send_lock = asyncio.Lock()
        self._turn_taking_control_lock = asyncio.Lock()
        self._reply_task: asyncio.Task[None] | None = None
        self._tts_worker_task: asyncio.Task[None] | None = None
        self._tts_queue: asyncio.Queue[tuple[str, str] | None] | None = None
        self._latency_probe = LatencyProbeState()
        self._turn_taking_stop_suppress_until: float | None = None
        self._reply_cancel_status: ConversationLogStatus | None = None
        self._last_turn_taking_audio_metrics: TurnTakingAudioMetrics | None = None
        self.active_conversation_session_id: UUID | None = None
        self._context_build_id: UUID | None = None
        self._event_queue: asyncio.Queue[
            tuple[SessionEvent, asyncio.Future[TransitionResult]]
        ] = asyncio.Queue()
        self._event_drain_lock = asyncio.Lock()
        self._candidate_request_sequence = 0
        self._active_initiative_request_id: str | None = None
        self._active_arrival_request_id: str | None = None
        self._last_start_reason: StartReason | None = None
        self._connected_output_state = connected_output_state or ConnectedOutputState.empty()
        self._active_initiative_feedback_scope: CandidateFeedbackScope | None = None
        self._last_precomputed_reply_text: str | None = None
        self._last_precomputed_reply_reason: str | None = None
        self._last_precomputed_reply_source: str | None = None
        self._last_precomputed_reply_candidate_id: str | None = None
        self._last_precomputed_reply_at: datetime | None = None
        self._retrieved_context_carryover = RetrievedContextCarryoverState()

    @property
    def _playback_echo_grace_ms(self) -> int:
        return self.audio_turns.playback_echo_grace_ms

    async def process_audio_chunk(self, chunk_bytes: bytes) -> SpeechSegment | None:
        chunk = np.frombuffer(chunk_bytes, dtype=np.float32)
        result = self.vad_processor.process_chunk(chunk)
        if result.state_changed_to is not None:
            await self._transition(result.state_changed_to)
        if result.segment is None and self.state == "listening":
            await self._maybe_emit_partial_transcript(chunk)
        if (
            result.segment is None
            and self.state == "idle"
            and self.audio_turns.playback_state == "idle"
        ):
            await self._advance_attention_idle(len(chunk))
        if result.segment is not None:
            self.latest_segment = result.segment
            await self._handle_finished_speech(result.segment)
        return result.segment

    def get_now_state(self) -> TomoroRuntimeState:
        """Return a read-only snapshot of the authoritative runtime state."""
        return TomoroRuntimeState(
            attention_mode=self.attention_mode,
            vad_state=self.state,
            playback_state=self.audio_turns.playback_state,  # type: ignore[arg-type]
            active_session_id=self.active_conversation_session_id,
            active_turn_id=self.audio_turns.active_turn_id,
            speaking_turn_id=self.audio_turns.speaking_turn_id,
            context_build_id=self._context_build_id,
            last_start_reason=self._last_start_reason,
            output_state=self._connected_output_state,
        )

    async def post_event(self, event: SessionEvent) -> TransitionResult:
        """Queue a session event and apply queued events in TomoroSession order."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TransitionResult] = loop.create_future()
        self._event_queue.put_nowait((event, future))
        async with self._event_drain_lock:
            await self._drain_events()
        return await future

    async def _drain_events(self) -> None:
        while not self._event_queue.empty():
            event, future = self._event_queue.get_nowait()
            if future.cancelled():
                continue
            try:
                future.set_result(await self._process_event(event))
            except Exception as exc:
                future.set_exception(exc)

    async def _process_event(self, event: SessionEvent) -> TransitionResult:
        result = self._reduce(event)
        for command in result.commands:
            if command.type == "record_playback_telemetry":
                telemetry = command.payload.get("telemetry")
                if isinstance(telemetry, PlaybackTelemetry):
                    await self.audio_turns.handle_playback_telemetry(telemetry)
        if event.type in {"playback_started", "playback_ended"}:
            return self._transition_result(
                event.type,
                payload=_playback_payload(event),
                commands=result.commands,
            )
        return result

    def _reduce(self, event: SessionEvent) -> TransitionResult:
        if event.type in {"playback_started", "playback_ended"}:
            telemetry = _playback_telemetry_from_event(event)
            return self._transition_result(
                event.type,
                payload=_playback_payload(event),
                commands=[
                    SessionCommand(
                        type="record_playback_telemetry",
                        payload={"telemetry": telemetry},
                    )
                ],
            )
        if event.type == "connected_output_state_changed":
            return self._reduce_connected_output_state_changed(event)
        if event.type == "client_stop_requested":
            return self._reduce_client_stop_requested(event)
        if event.type == "transcript_finalized":
            return self._reduce_transcript_finalized(event)
        if event.type == "idle_timer_elapsed":
            return self._reduce_idle_timer_elapsed(event)
        if event.type == "session_started":
            return self._reduce_session_started(event)
        if event.type == "initiative_candidate_loaded":
            return self._reduce_initiative_candidate_loaded(event)
        if event.type == "arrival_candidate_loaded":
            return self._reduce_arrival_candidate_loaded(event)
        if event.type == "stop_intent_classified":
            return self._reduce_stop_intent_classified(event)
        if event.type == "candidate_command_failed":
            return self._transition_result(
                "candidate_command_failed",
                payload=dict(event.payload),
            )
        return TransitionResult(state=self.get_now_state())

    def _reduce_idle_timer_elapsed(self, event: SessionEvent) -> TransitionResult:
        gate_reason = self._candidate_reply_gate_reason()
        if gate_reason is not None:
            return self._transition_result(
                "initiative_skipped",
                payload={
                    "reason": "not_speakable",
                    "event": event.type,
                    **self._candidate_reply_gate_payload(gate_reason),
                },
            )
        return self._transition_result(
            "initiative_fetch_requested",
            payload={"reason": "idle_timer_elapsed"},
            commands=[
                SessionCommand(
                    type="fetch_initiative_candidate",
                    payload={
                        "reason": "initiative",
                        "start_reason": "initiative",
                        "request_id": self._new_candidate_request_id("initiative"),
                    },
                )
            ],
        )

    def _reduce_session_started(self, event: SessionEvent) -> TransitionResult:
        gate_reason = self._candidate_reply_gate_reason()
        if gate_reason is not None:
            return self._transition_result(
                "arrival_skipped",
                payload={
                    "reason": "not_speakable",
                    "event": event.type,
                    **self._candidate_reply_gate_payload(gate_reason),
                },
            )
        return self._transition_result(
            "arrival_fetch_requested",
            payload={"reason": "session_started"},
            commands=[
                SessionCommand(
                    type="fetch_arrival_candidate",
                    payload={
                        "reason": "arrival",
                        "start_reason": "arrival",
                        "device_id": event.payload.get("device_id"),
                        "request_id": self._new_candidate_request_id("arrival"),
                    },
                )
            ],
        )

    def _reduce_initiative_candidate_loaded(
        self,
        event: SessionEvent,
    ) -> TransitionResult:
        if self._is_stale_candidate_result(
            "initiative",
            event.payload.get("request_id"),
        ):
            return self._transition_result(
                "initiative_skipped",
                payload={
                    "reason": "stale_result",
                    "request_id": event.payload.get("request_id"),
                },
            )
        candidate = event.payload.get("candidate")
        if candidate is None:
            self._active_initiative_request_id = None
            return self._transition_result(
                "initiative_skipped",
                payload={"reason": "candidate_not_found"},
            )
        if not isinstance(candidate, UtteranceCandidate):
            self._active_initiative_request_id = None
            return self._transition_result(
                "initiative_skipped",
                payload={"reason": "invalid_candidate_payload"},
            )
        gate_reason = self._candidate_reply_gate_reason()
        if gate_reason is not None:
            self._active_initiative_request_id = None
            logger.info(
                "initiative candidate blocked by session final gate "
                "candidate_id=%s gate_reason=%s policy_decision=%s",
                candidate.id,
                gate_reason,
                getattr(event.payload.get("policy_decision"), "decision", None),
            )
            return self._transition_result(
                "initiative_skipped",
                payload={
                    "reason": "not_speakable",
                    "candidate_id": candidate.id,
                    "policy": _candidate_policy_payload(event),
                    **self._candidate_reply_gate_payload(gate_reason),
                },
            )
        if candidate.maturity < 1 or candidate.generated_text is None:
            self._active_initiative_request_id = None
            return self._transition_result(
                "initiative_skipped",
                payload={
                    "reason": "not_text_ready",
                    "candidate_id": candidate.id,
                    "maturity": candidate.maturity,
                },
                commands=[
                    SessionCommand(
                        type="dismiss_utterance_candidate",
                        payload={
                            "candidate_id": candidate.id,
                            "reason": "not_text_ready",
                        },
                    )
                ],
            )
        policy_decision = event.payload.get("policy_decision")
        if isinstance(policy_decision, CandidateSpeakDecision):
            if policy_decision.decision == "wait":
                self._active_initiative_request_id = None
                return self._transition_result(
                    "initiative_skipped",
                    payload={
                        "reason": "policy_wait",
                        "candidate_id": candidate.id,
                        "policy": policy_decision.to_json(),
                    },
                )
            if policy_decision.decision == "needs_llm_judge":
                return self._transition_result(
                    "initiative_llm_judge_requested",
                    payload={
                        "candidate_id": candidate.id,
                        "policy": policy_decision.to_json(),
                    },
                    commands=[
                        SessionCommand(
                            type="judge_initiative_candidate",
                            payload={
                                "candidate": candidate,
                                "request_id": event.payload.get("request_id"),
                                "policy_decision": policy_decision,
                            },
                        )
                    ],
                )

        self._active_initiative_request_id = None
        self._set_start_reason("initiative")
        return self._transition_result(
            "initiative_reply_requested",
            payload={"candidate_id": candidate.id},
            commands=[
                SessionCommand(
                    type="start_initiative_reply",
                    payload={
                        "candidate_id": candidate.id,
                        "text": candidate.generated_text,
                        "generated_audio": candidate.generated_audio,
                        "candidate_source": candidate.source,
                        "feedback_scope": feedback_scope_from_candidate(candidate),
                        "reason": "initiative",
                        "start_reason": "initiative",
                        "started_by": "initiative",
                    },
                ),
                SessionCommand(
                    type="mark_utterance_spoken",
                    payload={
                        "candidate_id": candidate.id,
                        "spoken_at": event.occurred_at,
                        "reason": "initiative",
                        "start_reason": "initiative",
                    },
                ),
            ],
        )

    def _reduce_arrival_candidate_loaded(self, event: SessionEvent) -> TransitionResult:
        if self._is_stale_candidate_result(
            "arrival",
            event.payload.get("request_id"),
        ):
            return self._transition_result(
                "arrival_skipped",
                payload={
                    "reason": "stale_result",
                    "request_id": event.payload.get("request_id"),
                },
            )
        self._active_arrival_request_id = None
        candidate = event.payload.get("candidate")
        if candidate is None:
            return self._transition_result(
                "arrival_skipped",
                payload={"reason": "candidate_not_found"},
            )
        if not isinstance(candidate, ArrivalCandidate):
            return self._transition_result(
                "arrival_skipped",
                payload={"reason": "invalid_candidate_payload"},
            )
        gate_reason = self._candidate_reply_gate_reason()
        if gate_reason is not None:
            logger.info(
                "arrival candidate blocked by session final gate "
                "arrival_candidate_id=%s gate_reason=%s",
                candidate.id,
                gate_reason,
            )
            return self._transition_result(
                "arrival_skipped",
                payload={
                    "reason": "not_speakable",
                    "arrival_candidate_id": candidate.id,
                    **self._candidate_reply_gate_payload(gate_reason),
                },
            )

        mark_used = SessionCommand(
            type="mark_arrival_used",
            payload={
                "arrival_candidate_id": candidate.id,
                "used_at": event.occurred_at,
                "reason": "arrival",
                "start_reason": "arrival",
            },
        )
        if candidate.behavior == "wait_silent":
            return self._transition_result(
                "arrival_wait_silent",
                payload={"arrival_candidate_id": candidate.id},
                commands=[mark_used],
            )
        if candidate.behavior == "subtle_react":
            return self._transition_result(
                "arrival_subtle_react",
                payload={"arrival_candidate_id": candidate.id},
                commands=[mark_used],
            )
        if candidate.utterance_text is None:
            return self._transition_result(
                "arrival_skipped",
                payload={
                    "reason": "missing_utterance_text",
                    "arrival_candidate_id": candidate.id,
                },
                commands=[mark_used],
            )

        self._set_start_reason("arrival")
        return self._transition_result(
            "arrival_reply_requested",
            payload={"arrival_candidate_id": candidate.id},
            commands=[
                SessionCommand(
                    type="start_arrival_reply",
                    payload={
                        "arrival_candidate_id": candidate.id,
                        "text": candidate.utterance_text,
                        "generated_audio": candidate.utterance_audio,
                        "candidate_source": "arrival",
                        "reason": "arrival",
                        "start_reason": "arrival",
                        "started_by": "arrival",
                    },
                ),
                mark_used,
            ],
        )

    def _can_start_candidate_reply(self) -> bool:
        return self._candidate_reply_gate_reason() is None

    def _candidate_reply_gate_reason(self) -> str | None:
        if self.attention_mode != "ambient":
            return "attention_not_ambient"
        if self.state != "idle":
            return "vad_not_idle"
        if self.audio_turns.playback_state != "idle":
            return "playback_not_idle"
        if not self._connected_output_state.audio_target_available:
            return "audio_target_unavailable"
        return None

    def _candidate_reply_gate_payload(self, gate_reason: str) -> dict[str, object]:
        return {
            "gate_reason": gate_reason,
            "attention_mode": self.attention_mode,
            "vad_state": self.state,
            "playback_state": self.audio_turns.playback_state,
            "audio_target_available": (
                self._connected_output_state.audio_target_available
            ),
        }

    def _reduce_connected_output_state_changed(
        self,
        event: SessionEvent,
    ) -> TransitionResult:
        output_state = event.payload.get("output_state")
        if not isinstance(output_state, ConnectedOutputState):
            return self._transition_result(
                "connected_output_state_ignored",
                payload={"reason": "invalid_output_state"},
            )
        self._connected_output_state = output_state
        commands: list[SessionCommand] = []
        if (
            self.active_conversation_session_id is not None
            and output_state.connected_connection_count == 0
        ):
            commands.append(
                SessionCommand(
                    type="close_conversation_session",
                    payload={"end_reason": "client_disconnect"},
                )
            )
        return self._transition_result(
            "connected_output_state_changed",
            payload={
                "active_device_id": output_state.active_device_id,
                "audio_target_available": output_state.audio_target_available,
                "display_target_available": output_state.display_target_available,
                "connected_device_count": output_state.connected_device_count,
                "connected_connection_count": output_state.connected_connection_count,
            },
            commands=commands,
        )

    def _reduce_client_stop_requested(self, event: SessionEvent) -> TransitionResult:
        reason = str(event.payload.get("reason") or "ui_stop")
        commands: list[SessionCommand] = []
        if self.active_conversation_session_id is not None:
            commands.append(
                SessionCommand(
                    type="close_conversation_session",
                    payload={"end_reason": reason},
                )
            )
        return self._transition_result(
            "client_stop_requested",
            payload={
                "reason": reason,
                "active_conversation_session_id": (
                    str(self.active_conversation_session_id)
                    if self.active_conversation_session_id is not None
                    else None
                ),
            },
            commands=commands,
        )

    def _new_candidate_request_id(self, kind: Literal["initiative", "arrival"]) -> str:
        self._candidate_request_sequence += 1
        request_id = f"{kind}-{self._candidate_request_sequence}"
        if kind == "initiative":
            self._active_initiative_request_id = request_id
        else:
            self._active_arrival_request_id = request_id
        return request_id

    def _is_stale_candidate_result(
        self,
        kind: Literal["initiative", "arrival"],
        request_id: object,
    ) -> bool:
        if request_id is None:
            return False
        active_request_id = (
            self._active_initiative_request_id
            if kind == "initiative"
            else self._active_arrival_request_id
        )
        return str(request_id) != active_request_id

    def _set_start_reason(self, reason: StartReason) -> None:
        self._last_start_reason = reason

    def _reduce_transcript_finalized(self, event: SessionEvent) -> TransitionResult:
        transcript = event.payload.get("transcript")
        if not isinstance(transcript, Transcript):
            return TransitionResult(state=self.get_now_state())
        decision = self._classify_barge_in(transcript)
        if decision is None:
            return self._transition_result(
                "transcript_finalized",
                payload={"text": transcript.text},
            )
        commands: list[SessionCommand] = []
        if decision.action == "continue_speaking":
            commands.append(
                SessionCommand(
                    type="write_ambient_observer",
                    payload={
                        "transcript": transcript,
                        "reason": decision.reason,
                    },
                )
            )
        elif decision.action == "restart_turn":
            commands.extend(
                [
                    SessionCommand(
                        type="cancel_reply_generation",
                        payload={"status": "interrupted"},
                    ),
                    SessionCommand(type="send_audio_control_stop"),
                    SessionCommand(
                        type="save_tomoko_turn",
                        payload={"status": "interrupted"},
                    ),
                    SessionCommand(
                        type="start_reply_generation",
                        payload={"transcript": transcript},
                    ),
                ]
            )
        return self._transition_result(
            "barge_in_resolved",
            payload={
                "kind": decision.kind,
                "action": decision.action,
                "reason": decision.reason,
            },
            commands=commands,
        )

    def _reduce_stop_intent_classified(self, event: SessionEvent) -> TransitionResult:
        turn_id = _optional_str_payload(event.payload.get("turn_id"))
        active_turn_id = self.audio_turns.active_turn_id
        if turn_id is None or turn_id != active_turn_id:
            return self._transition_result(
                "stale_stop_intent",
                payload={
                    "reason": "missing_turn_id" if turn_id is None else "turn_mismatch",
                    "observation_id": event.payload.get("observation_id"),
                    "turn_id": turn_id,
                    "active_turn_id": active_turn_id,
                },
            )
        predicted_kind = str(event.payload.get("predicted_kind", "none"))
        confidence = _optional_float_payload(event.payload.get("confidence")) or 0.0
        if not should_adopt_stop_signal(predicted_kind, confidence):
            return self._transition_result(
                "stop_intent_observed",
                payload={
                    "reason": "low_confidence_or_non_stop",
                    "observation_id": event.payload.get("observation_id"),
                    "predicted_kind": predicted_kind,
                    "confidence": confidence,
                },
            )
        return self._transition_result(
            "stop_intent_adopted",
            payload={
                "observation_id": event.payload.get("observation_id"),
                "transcript_id": event.payload.get("transcript_id"),
                "method": event.payload.get("method"),
                "predicted_kind": predicted_kind,
                "confidence": confidence,
                "latency_ms": event.payload.get("latency_ms"),
            },
            commands=[
                SessionCommand(
                    type="apply_stop_intent_ack",
                    payload={
                        "status": "interrupted",
                        "predicted_kind": predicted_kind,
                    },
                )
            ],
        )

    def _transition_result(
        self,
        emission_type: str,
        *,
        payload: dict[str, Any] | None = None,
        commands: list[SessionCommand] | None = None,
    ) -> TransitionResult:
        state = self.get_now_state()
        return TransitionResult(
            state=state,
            emissions=[
                StateEmission(
                    type=emission_type,
                    payload=payload or {},
                    state_snapshot=state,
                )
            ],
            commands=commands or [],
        )

    async def _handle_finished_speech(self, segment: SpeechSegment) -> None:
        if self.transcriber is None:
            return

        self._reset_latency_probe()
        self._latency_probe.mark_speech_end()
        frontend_decision = self.stt_audio_frontend.process_segment(segment)
        logger.info(
            "TomoroSession latency speech_end segment_ms=%.1f attention_mode=%s state=%s "
            "stt_frontend_action=%s stt_frontend_reason=%s filters=%s "
            "rms_db=%.1f peak_db=%.1f active_frame_ratio=%.3f",
            (segment.ended_at - segment.started_at).total_seconds() * 1000,
            self.attention_mode,
            self.state,
            frontend_decision.action,
            frontend_decision.reason,
            ",".join(frontend_decision.enabled_filters) or "none",
            frontend_decision.metrics.rms_db,
            frontend_decision.metrics.peak_db,
            frontend_decision.metrics.active_frame_ratio,
        )
        self._last_turn_taking_audio_metrics = TurnTakingAudioMetrics(
            segment_ms=frontend_decision.metrics.duration_ms,
            rms_db=frontend_decision.metrics.rms_db,
            peak_db=frontend_decision.metrics.peak_db,
            active_frame_ratio=frontend_decision.metrics.active_frame_ratio,
        )
        if not frontend_decision.accepted:
            self.vad_processor.reset()
            self._reset_transcriber_stream()
            await self._transition("idle")
            return
        assert frontend_decision.segment is not None

        stt_started_at = time.perf_counter()
        transcript = await self.transcriber.transcribe(frontend_decision.segment)
        stt_elapsed_ms = (time.perf_counter() - stt_started_at) * 1000
        logger.info(
            "TomoroSession transcript text=%r speaker=%s audio_level_db=%s "
            "attention_mode=%s state=%s stt_elapsed_ms=%.1f speech_end_to_transcript_ms=%.1f",
            transcript.text,
            transcript.speaker,
            transcript.audio_level_db,
            self.attention_mode,
            self.state,
            stt_elapsed_ms,
            self._elapsed_since_speech_end_ms(),
        )
        await self.process_transcript(transcript, reset_audio_input=True)

    async def process_transcript(
        self,
        transcript: Transcript,
        *,
        reset_audio_input: bool = False,
    ) -> None:
        """Handle a finalized transcript from local STT or a remote edge node."""
        filter_decision = self._filter_transcript(transcript, is_partial=False)
        if filter_decision.action == "drop":
            if reset_audio_input:
                self.vad_processor.reset()
                self._reset_transcriber_stream()
            await self._transition("idle")
            return
        previous_attention = self.attention_mode
        turn_taking_action = await self._maybe_apply_turn_taking_decision(
            transcript,
            previous_attention=previous_attention,
            reset_audio_input=reset_audio_input,
        )
        if turn_taking_action == "consumed":
            return
        barge_in_decision = (
            None
            if turn_taking_action == "skip_barge_in"
            else self._classify_barge_in(transcript)
        )
        if barge_in_decision is not None:
            await self._record_stop_intent_observation(
                transcript,
                rule_kind=barge_in_decision.kind,
                adopted_action=barge_in_decision.action,
            )
            await self._send_event(
                {
                    "type": "barge_in",
                    "kind": barge_in_decision.kind,
                    "action": barge_in_decision.action,
                }
            )
            logger.info(
                "TomoroSession barge-in kind=%s action=%s reason=%s",
                barge_in_decision.kind,
                barge_in_decision.action,
                barge_in_decision.reason,
            )
            if barge_in_decision.action == "restart_turn":
                await self._cancel_reply_generation(status="interrupted")
                await self._send_reserved_audio_stop()
            if barge_in_decision.action == "continue_speaking":
                if self.ambient_log_writer is not None:
                    await self.ambient_log_writer.write(
                        transcript,
                        tomoko_participated=False,
                        attention_mode=previous_attention,
                        attended=False,
                        participation_mode="observer",
                    )
                await self._send_transcript_final_event(
                    transcript,
                    attention_mode=previous_attention,
                    participation_mode="observer",
                    attended=False,
                )
                if reset_audio_input:
                    self.vad_processor.reset()
                    self._reset_transcriber_stream()
                await self._transition("idle")
                return

        decision = _withdraw_decision(transcript)
        if decision is None and self.participation_judge is not None:
            decision = await self.participation_judge.judge(
                ParticipationContext.from_transcript(
                    transcript,
                    attention_mode=previous_attention,
                )
            )
        await self._maybe_record_stop_intent_observation_for_decision(
            transcript,
            decision,
        )

        should_participate = bool(decision and decision.should_participate)
        participation_mode = decision.mode if decision is not None else "observer"
        attended = should_participate
        if self.ambient_log_writer is not None:
            await self.ambient_log_writer.write(
                transcript,
                tomoko_participated=should_participate,
                attention_mode=previous_attention,
                attended=attended,
                participation_mode=participation_mode,
            )

        if decision is not None and decision.mode == "withdraw":
            await self._transition_attention("withdrawn")
        await self._maybe_record_initiative_feedback(transcript)

        if decision is not None and decision.should_participate:
            start_reason = _start_reason_from_participation_mode(decision.mode)
            self._set_start_reason(start_reason)
            logger.info(
                "TomoroSession participation mode=%s reason=%s",
                decision.mode,
                decision.reason,
            )
            await self._ensure_conversation_session(
                device_id=transcript.device_id,
                start_reason=start_reason,
            )
            await self._transition_attention("engaged")
            if self.conversation_log_writer is not None:
                conversation_log_id = await self._write_user_turn(
                    transcript,
                    participation_mode=decision.mode,
                )
                if conversation_log_id is not None:
                    self._schedule_conversation_embedding(
                        conversation_log_id=conversation_log_id,
                        text=transcript.text,
                    )
            await self._send_transcript_final_event(
                transcript,
                attention_mode=previous_attention,
                participation_mode=participation_mode,
                attended=True,
                conversation_session_id=self.active_conversation_session_id,
            )
            await self._send_event({"type": "participation", "mode": decision.mode})

            if self.router is not None and self.thinking_mode is not None:
                await self._start_reply_task(transcript)
        else:
            await self._send_transcript_final_event(
                transcript,
                attention_mode=previous_attention,
                participation_mode=participation_mode,
                attended=attended,
            )

        if reset_audio_input:
            self.vad_processor.reset()
            self._reset_transcriber_stream()
        await self._transition("idle")

    async def _maybe_emit_partial_transcript(self, chunk: np.ndarray) -> None:
        if not supports_streaming(self.transcriber):
            return
        if not self.stt_audio_frontend.should_process_partial_chunk(chunk):
            return
        assert self.transcriber is not None
        partial = await self.transcriber.process_stream_chunk(  # type: ignore[attr-defined]
            chunk,
            device_id=self.vad_processor.device_id,
            sample_rate=self.vad_processor.sample_rate,
        )
        if partial is None:
            return
        logger.info(
            "TomoroSession partial transcript text=%r speaker=%s audio_level_db=%s "
            "attention_mode=%s state=%s",
            partial.text,
            partial.speaker,
            partial.audio_level_db,
            self.attention_mode,
            self.state,
        )
        filter_decision = self._filter_transcript(partial, is_partial=True)
        if filter_decision.action != "accept":
            return
        await self._send_event(
            {
                "type": "transcript_partial",
                "text": partial.text,
            }
        )

    def _filter_transcript(self, transcript: Transcript, *, is_partial: bool):
        if self.transcript_filter is None:
            from server.shared.models import TranscriptFilterDecision

            return TranscriptFilterDecision(action="accept", reason="not_configured")
        decision = self.transcript_filter.evaluate(transcript, is_partial=is_partial)
        logger.info(
            "TomoroSession transcript filter text=%r action=%s reason=%s "
            "audio_level_db=%s attention_mode=%s is_partial=%s",
            transcript.text,
            decision.action,
            decision.reason,
            transcript.audio_level_db,
            self.attention_mode,
            is_partial,
        )
        return decision

    def _reset_transcriber_stream(self) -> None:
        if supports_streaming(self.transcriber):
            assert self.transcriber is not None
            self.transcriber.reset_stream()  # type: ignore[attr-defined]

    async def _maybe_apply_turn_taking_decision(
        self,
        transcript: Transcript,
        *,
        previous_attention: AttentionMode,
        reset_audio_input: bool,
    ) -> Literal["consumed", "skip_barge_in"] | None:
        if self._should_suppress_duplicate_turn_taking_stop(transcript):
            await self._write_turn_taking_observer(
                transcript,
                previous_attention=previous_attention,
                reason="duplicate_stop_suppressed",
            )
            if reset_audio_input:
                self.vad_processor.reset()
                self._reset_transcriber_stream()
            await self._transition("idle")
            return "consumed"
        skip_reason = self._turn_taking_skip_reason(transcript)
        if skip_reason is not None:
            self._log_turn_taking_skipped(transcript, reason=skip_reason)
            return None
        async with self._turn_taking_control_lock:
            skip_reason = self._turn_taking_skip_reason(transcript)
            if skip_reason is not None:
                self._log_turn_taking_skipped(transcript, reason=skip_reason)
                return None
            return await self._apply_turn_taking_decision_locked(
                transcript,
                previous_attention=previous_attention,
                reset_audio_input=reset_audio_input,
            )

    async def _apply_turn_taking_decision_locked(
        self,
        transcript: Transcript,
        *,
        previous_attention: AttentionMode,
        reset_audio_input: bool,
    ) -> Literal["consumed", "skip_barge_in"] | None:

        turn_input = TurnTakingInput(
            pending_reply_state=self._pending_reply_state(),
            new_transcript=transcript.text,
            audio_metrics=self._last_turn_taking_audio_metrics
            or TurnTakingAudioMetrics.unknown(audio_level_db=transcript.audio_level_db),
            attention_mode=self.attention_mode,
            playback_state=self.audio_turns.playback_state,  # type: ignore[arg-type]
            recent_tomoko_text=self.audio_turns.recent_tomoko_text,
        )
        decision = await self.turn_taking_judge.judge(turn_input)
        logger.info(
            "TomoroSession turn_taking_decision decision=%s reason=%s source=%s "
            "elapsed_ms=%.1f pending_reply_state=%s playback_state=%s text=%r",
            decision.decision,
            decision.reason,
            decision.source,
            decision.elapsed_ms,
            turn_input.pending_reply_state,
            turn_input.playback_state,
            transcript.text,
        )
        await self._send_event(
            {
                "type": "turn_taking_decision",
                "decision": decision.decision,
                "reason": decision.reason,
                "source": decision.source,
            }
        )

        if decision.decision in {"ignore_as_noise", "continue_current_reply"}:
            await self._write_turn_taking_observer(
                transcript,
                previous_attention=previous_attention,
                reason=decision.reason,
            )
            if reset_audio_input:
                self.vad_processor.reset()
                self._reset_transcriber_stream()
            await self._transition("idle")
            return "consumed"

        if decision.decision == "defer_output":
            self._defer_reply_output(max_ms=220)
            await self._write_turn_taking_observer(
                transcript,
                previous_attention=previous_attention,
                reason=decision.reason,
            )
            if reset_audio_input:
                self.vad_processor.reset()
                self._reset_transcriber_stream()
            await self._transition("idle")
            return "consumed"

        if decision.decision == "stop_speaking":
            self._turn_taking_stop_suppress_until = time.perf_counter() + 0.5
            await self._record_stop_intent_observation(
                transcript,
                rule_kind="turn_taking_stop",
                adopted_action="stop_speaking",
            )
            await self._write_turn_taking_observer(
                transcript,
                previous_attention=previous_attention,
                reason=decision.reason,
            )
            await self._apply_stop_intent_ack()
            if reset_audio_input:
                self.vad_processor.reset()
                self._reset_transcriber_stream()
            await self._transition("idle")
            return "consumed"

        if decision.decision == "restart_with_new_input":
            await self._send_event(
                {
                    "type": "barge_in",
                    "kind": "hard_interrupt",
                    "action": "restart_turn",
                }
            )
            await self._cancel_reply_generation(status="interrupted")
            await self._send_reserved_audio_stop()
            await self._record_stop_intent_observation(
                transcript,
                rule_kind="turn_taking_restart",
                adopted_action="restart_with_new_input",
            )
            return "skip_barge_in"

        return None

    def _should_judge_turn_taking(self) -> bool:
        return self._turn_taking_skip_reason_for_state() is None

    def _turn_taking_skip_reason(self, transcript: Transcript) -> str | None:
        state_reason = self._turn_taking_skip_reason_for_state()
        if state_reason is not None:
            return state_reason
        if self.audio_turns.playback_state == "idle":
            return None
        if not self._is_turn_taking_interrupt_candidate(transcript.text):
            return "playback_non_interrupt_candidate"
        return None

    def _turn_taking_skip_reason_for_state(self) -> str | None:
        if self._is_reply_generation_active():
            return None
        if self.audio_turns.playback_state == "idle":
            return "no_active_reply_or_playback"
        return None

    def _is_turn_taking_interrupt_candidate(self, text: str) -> bool:
        judge = self.turn_taking_judge
        fallback = getattr(judge, "fallback", None)
        for candidate in (judge, fallback):
            is_interrupt_candidate = getattr(candidate, "is_interrupt_candidate", None)
            if callable(is_interrupt_candidate):
                return bool(is_interrupt_candidate(text))
        if should_record_stop_intent_candidate(text):
            return True
        return any(
            word in text.casefold()
            for word in (
                "ストップ",
                "止めて",
                "やめて",
                "停止",
                "待って",
                "まって",
                "違う",
                "ちがう",
            )
        )

    def _log_turn_taking_skipped(self, transcript: Transcript, *, reason: str) -> None:
        logger.info(
            "TomoroSession turn_taking_skipped reason=%s reply_active=%s "
            "playback_state=%s text=%r",
            reason,
            self._is_reply_generation_active(),
            self.audio_turns.playback_state,
            transcript.text,
        )

    def _should_suppress_duplicate_turn_taking_stop(self, transcript: Transcript) -> bool:
        suppress_until = self._turn_taking_stop_suppress_until
        if suppress_until is None or time.perf_counter() > suppress_until:
            return False
        text = transcript.text.casefold()
        return any(word in text for word in ("ストップ", "止めて", "やめて", "停止"))

    def _pending_reply_state(self):
        if not self._is_reply_generation_active() and self.audio_turns.playback_state == "idle":
            return "none"
        if (
            self._latency_probe.first_audio_chunk_at is not None
            or self.audio_turns.playback_state != "idle"
        ):
            return "audio_started"
        if (
            self._latency_probe.first_reply_text_at is not None
            or self._latency_probe.reply_output_started
        ):
            return "text_started"
        return "generating_not_started"

    async def _write_turn_taking_observer(
        self,
        transcript: Transcript,
        *,
        previous_attention: AttentionMode,
        reason: str,
    ) -> None:
        if self.ambient_log_writer is None:
            return
        await self.ambient_log_writer.write(
            transcript,
            tomoko_participated=False,
            attention_mode=previous_attention,
            attended=False,
            participation_mode="observer",
        )
        await self._send_transcript_final_event(
            transcript,
            attention_mode=previous_attention,
            participation_mode="observer",
            attended=False,
        )
        logger.info("TomoroSession turn-taking observer reason=%s", reason)

    def _defer_reply_output(self, *, max_ms: int) -> None:
        self._latency_probe.defer_reply_output(max_ms=max_ms)

    async def _maybe_wait_reply_output_defer(self) -> None:
        delay = self._latency_probe.consume_reply_output_defer_delay()
        if delay is not None:
            await asyncio.sleep(delay)

    def _merge_carried_long_term_memory(
        self,
        fresh_memory: list[MemoryHit],
    ) -> list[MemoryHit]:
        result = self._retrieved_context_carryover.merge_carried_long_term_memory(
            fresh_memory
        )
        if not result.carried_count:
            return result.memories

        logger.info(
            "TomoroSession carryover_used count=%s fresh_count=%s merged_count=%s",
            result.carried_count,
            result.fresh_count,
            result.merged_count,
        )
        return result.memories

    def _carried_long_term_memory(self) -> list[MemoryHit]:
        return self._retrieved_context_carryover.carried_long_term_memory()

    def _remember_retrieved_context(self, memories: list[MemoryHit]) -> None:
        result = self._retrieved_context_carryover.remember(memories)
        if result is None:
            return

        for eviction in result.evicted:
            logger.info(
                "TomoroSession carryover_evicted reason=%s key=%s similarity=%.3f chars=%s",
                eviction.reason,
                eviction.key,
                eviction.similarity,
                eviction.chars,
            )
        logger.info(
            "TomoroSession carryover_added added=%s total=%s evicted=%s",
            result.added,
            result.total,
            len(result.evicted),
        )

    def _evict_retrieved_context_carryover(self) -> int:
        evictions = self._retrieved_context_carryover.evict()
        for eviction in evictions:
            logger.info(
                "TomoroSession carryover_evicted reason=%s key=%s similarity=%.3f chars=%s",
                eviction.reason,
                eviction.key,
                eviction.similarity,
                eviction.chars,
            )
        return len(evictions)

    def _evict_one_carryover(self, *, reason: str) -> None:
        eviction = self._retrieved_context_carryover.evict_one(reason=reason)
        logger.info(
            "TomoroSession carryover_evicted reason=%s key=%s similarity=%.3f chars=%s",
            eviction.reason,
            eviction.key,
            eviction.similarity,
            eviction.chars,
        )

    def _clear_retrieved_context_carryover(self, *, reason: str) -> None:
        count = self._retrieved_context_carryover.clear()
        if count:
            logger.info(
                "TomoroSession carryover_cleared reason=%s count=%s",
                reason,
                count,
            )

    async def _reply_to(self, transcript: Transcript) -> None:
        if self.router is None or self.thinking_mode is None:
            return

        backend = await self.router.select("conversation", "privacy")
        self._latency_probe.mark_reply_start()
        logger.info(
            "TomoroSession latency reply_start backend=%s speech_end_to_reply_start_ms=%.1f",
            backend.name,
            self._elapsed_since_speech_end_ms(),
        )
        thinking_mode = self.thinking_mode
        explicit_memory_cue = has_deep_memory_cue(transcript.text)
        depth = "deep" if should_use_deep_memory(transcript.text) else "fast"
        context_snapshot = await self._build_context_snapshot(
            transcript,
            depth=depth,
            explicit_memory_cue=explicit_memory_cue,
        )
        recent_turns = self._recent_turns_with_precomputed_topic(
            context_snapshot.recent_turns
        )
        fresh_long_term_memory = [
            _session_summary_hit_to_memory(hit)
            for hit in context_snapshot.session_summaries
        ]
        fresh_long_term_memory.extend(context_snapshot.memory_hits)
        long_term_memory = self._merge_carried_long_term_memory(fresh_long_term_memory)
        if fresh_long_term_memory:
            self._remember_retrieved_context(fresh_long_term_memory)
        if self.deep_thinking_mode is not None and depth == "deep" and long_term_memory:
            thinking_mode = self.deep_thinking_mode
            logger.info(
                "TomoroSession deep memory selected hits=%s text=%r",
                len(long_term_memory),
                transcript.text,
            )

        assert thinking_mode is not None
        thinking_input = ThinkingInput(
            text=transcript.text,
            speaker=transcript.speaker,
            context=recent_turns,
            emotion="neutral",
            device_id=transcript.device_id,
            long_term_memory=long_term_memory,
            context_snapshot=context_snapshot,
        )
        reply = ReplyPipeline(initial_emotion=thinking_input.emotion)
        self.audio_turns.begin_turn()
        tts_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        tts_worker = asyncio.create_task(self._run_tts_queue(tts_queue))
        self._tts_queue = tts_queue
        self._tts_worker_task = tts_worker
        try:
            async for event in thinking_mode.think(backend, thinking_input):
                for command in reply.handle_event(event):
                    if command.action == "emotion":
                        assert command.image is not None
                        await self._maybe_wait_reply_output_defer()
                        self._latency_probe.mark_reply_output_started()
                        await self._send_event(
                            {
                                "type": "emotion",
                                "value": command.value,
                                "image": command.image,
                            }
                        )
                    elif command.action == "text_delta":
                        await self._maybe_wait_reply_output_defer()
                        if self._latency_probe.mark_first_reply_text_if_unmarked():
                            logger.info(
                                "TomoroSession latency first_reply_text "
                                "backend=%s speech_end_to_first_reply_text_ms=%.1f "
                                "reply_start_to_first_reply_text_ms=%.1f",
                                backend.name,
                                self._elapsed_since_speech_end_ms(),
                                self._elapsed_since_reply_start_ms(),
                            )
                        self._latency_probe.mark_reply_output_started()
                        logger.info("TomoroSession reply_text delta=%r", command.value)
                        await self._send_event({"type": "reply_text", "delta": command.value})
                    elif command.action == "tts_text":
                        await self._maybe_wait_reply_output_defer()
                        await tts_queue.put((command.value, command.style))
                    elif command.action == "done":
                        await tts_queue.put(None)
                        await tts_worker
                        reply_text = reply.reply_text.strip()
                        if self.conversation_log_writer is not None and reply_text:
                            await self._write_tomoko_turn(
                                text=reply_text,
                                emotion=reply.current_emotion,
                                device_id=transcript.device_id,
                                status="completed",
                            )
                        await self._send_reserved_audio_end()
                        await self._send_event({"type": "reply_done"})
                        self._note_attention_activity()
        except asyncio.CancelledError:
            tts_worker.cancel()
            with suppress(asyncio.CancelledError):
                await tts_worker
            reply_text = reply.reply_text.strip()
            if reply_text:
                await self._write_tomoko_turn(
                    text=reply_text,
                    emotion=reply.current_emotion,
                    device_id=transcript.device_id,
                    status=self._reply_cancel_status or "cancelled",
                )
            raise
        finally:
            if self._tts_worker_task is tts_worker:
                self._tts_worker_task = None
            if self._tts_queue is tts_queue:
                self._tts_queue = None

    async def handle_playback_telemetry(self, telemetry: PlaybackTelemetry) -> None:
        await self.post_event(
            SessionEvent(
                type=telemetry.type,
                payload={
                    "turn_id": telemetry.turn_id,
                    "chunk_id": telemetry.chunk_id,
                    "scheduled_audio_time": telemetry.scheduled_audio_time,
                    "sent_audio_time": telemetry.sent_audio_time,
                    "audio_context_time": telemetry.audio_context_time,
                    "performance_now_ms": telemetry.performance_now_ms,
                },
            )
        )
        logger.info(
            "TomoroSession playback telemetry type=%s turn_id=%s "
            "chunk_id=%s scheduled_audio_time=%s sent_audio_time=%s "
            "audio_context_time=%s performance_now_ms=%s",
            telemetry.type,
            telemetry.turn_id,
            telemetry.chunk_id,
            telemetry.scheduled_audio_time,
            telemetry.sent_audio_time,
            telemetry.audio_context_time,
            telemetry.performance_now_ms,
        )

    async def apply_stop_intent_event(self, event: SessionEvent) -> TransitionResult:
        """Apply a background stop-intent advisory result and run control commands."""
        result = await self.post_event(event)
        await self._run_internal_commands(result.commands)
        return result

    async def apply_client_lifecycle_event(
        self,
        event: SessionEvent,
    ) -> TransitionResult:
        """Apply client lifecycle facts while keeping session close ownership here."""
        result = await self.post_event(event)
        await self._run_internal_commands(result.commands)
        return result

    async def start_precomputed_reply(
        self,
        *,
        text: str,
        device_id: str,
        reason: str,
        audio_data: bytes | None = None,
        feedback_scope: CandidateFeedbackScope | None = None,
        candidate_source: object | None = None,
        candidate_id: object | None = None,
    ) -> None:
        """Speak a text-ready initiative/arrival candidate through the normal output path."""
        stripped_text = text.strip()
        if not stripped_text:
            return

        await self._cancel_reply_generation(status="cancelled")
        self._active_initiative_feedback_scope = feedback_scope
        self._last_precomputed_reply_text = stripped_text
        self._last_precomputed_reply_reason = reason
        self._last_precomputed_reply_source = (
            str(candidate_source) if candidate_source is not None else None
        )
        self._last_precomputed_reply_candidate_id = (
            str(candidate_id) if candidate_id is not None else None
        )
        self._last_precomputed_reply_at = datetime.now(UTC)
        self._reset_latency_probe()
        self._latency_probe.mark_reply_start()
        # Initiative/arrival speech opens attention, but the conversation session
        # starts only when a human replies through the normal participation path.
        await self._transition_attention("engaged")
        self._latency_probe.mark_reply_output_started()
        await self._send_event({"type": "reply_text", "delta": stripped_text})
        self.audio_turns.begin_turn()
        try:
            if audio_data is None:
                await self._flush_tts_text(stripped_text, style="neutral")
            else:
                await self._send_reserved_audio_start()
                outgoing = await self.audio_turns.reserve_audio_chunk(
                    text=stripped_text,
                    chunk=AudioChunkOut(data=audio_data, sequence=0, is_last=True),
                )
                await self._send_audio_chunk(outgoing)
            await self._write_tomoko_turn(
                text=stripped_text,
                emotion="neutral",
                device_id=device_id,
                status="completed",
            )
            await self._send_reserved_audio_end()
            await self._send_event({"type": "reply_done"})
        finally:
            await self._send_reserved_audio_end()
            self._note_attention_activity()

    async def _maybe_record_initiative_feedback(
        self,
        transcript: Transcript,
    ) -> None:
        if (
            self.candidate_feedback_store is None
            or self._active_initiative_feedback_scope is None
        ):
            return
        signal = classify_feedback(transcript, self._active_initiative_feedback_scope)
        if signal is None:
            return
        await self.candidate_feedback_store.record(signal)
        logger.info(
            "TomoroSession initiative feedback kind=%s score=%.2f source=%s "
            "topic=%s emotional_need=%s",
            signal.kind,
            signal.score,
            signal.scope.source,
            signal.scope.topic,
            signal.scope.emotional_need,
        )
        self._active_initiative_feedback_scope = None

    async def _transition(self, state: str) -> None:
        if state not in {"idle", "listening", "processing"}:
            raise ValueError(f"unknown session state: {state}")
        self.state = state  # type: ignore[assignment]
        logger.info("TomoroSession state changed to %s", state)
        await self._send_event({"type": "state", "state": state})

    async def _transition_attention(self, mode: AttentionMode) -> None:
        if self.attention_mode == mode:
            self._note_attention_activity()
            return
        old_mode = self.attention_mode
        self.attention_mode = mode
        self._note_attention_activity()
        logger.info("TomoroSession attention changed from %s to %s", old_mode, mode)
        await self._send_event({"type": "attention", "mode": mode})
        if mode == "ambient" and old_mode == "cooldown":
            await self._close_conversation_session(end_reason="attention_timeout")
        elif mode == "withdrawn":
            await self._close_conversation_session(end_reason="withdrawn")

    def _note_attention_activity(self) -> None:
        self._attention_idle_ms = 0.0

    async def _advance_attention_idle(self, sample_count: int) -> None:
        if self.attention_mode not in {"engaged", "cooldown"}:
            return
        self._attention_idle_ms += sample_count * 1000 / self.vad_processor.sample_rate
        if (
            self.attention_mode == "engaged"
            and self._attention_idle_ms >= self._engaged_timeout_ms
        ):
            await self._transition_attention("cooldown")
            return
        if (
            self.attention_mode == "cooldown"
            and self._attention_idle_ms >= self._cooldown_timeout_ms
        ):
            await self._transition_attention("ambient")

    async def _send_event(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            maybe_awaitable = self.send_event(event)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

    async def _send_transcript_final_event(
        self,
        transcript: Transcript,
        *,
        attention_mode: AttentionMode,
        participation_mode: ParticipationMode,
        attended: bool,
        conversation_session_id: UUID | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "transcript_final",
            "text": transcript.text,
            "attention_mode": attention_mode,
            "participation_mode": participation_mode,
            "attended": attended,
            "audio_level_db": transcript.audio_level_db,
            "is_final": transcript.is_final,
        }
        if transcript.speaker is not None:
            event["speaker"] = transcript.speaker
        if conversation_session_id is not None:
            event["conversation_session_id"] = str(conversation_session_id)
        await self._send_event(event)

    async def send_transition_emissions(self, result: TransitionResult) -> None:
        for emission in result.emissions:
            await self._send_event(
                {
                    "type": emission.type,
                    **_json_safe_payload(emission.payload),
                }
            )

    async def _send_audio_chunk(self, chunk: AudioChunkOut) -> None:
        if self.send_audio is None:
            return
        self._latency_probe.mark_reply_output_started()
        async with self._send_lock:
            maybe_awaitable = self.send_audio(chunk.data)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

    async def _start_reply_task(self, transcript: Transcript) -> None:
        await self._cancel_reply_generation(status="cancelled")
        self._reply_cancel_status = None
        self._reply_task = asyncio.create_task(self._run_reply_task(transcript))

    async def _run_reply_task(self, transcript: Transcript) -> None:
        try:
            await self._reply_to(transcript)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error generating reply: %s", e)
        finally:
            if self._reply_task is asyncio.current_task():
                self._reply_task = None

    async def _cancel_reply_generation(
        self,
        *,
        status: ConversationLogStatus = "cancelled",
    ) -> None:
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in (self._reply_task, self._tts_worker_task)
            if task is not None and task is not current_task and not task.done()
        ]
        if tasks:
            self._reply_cancel_status = status
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _cancel_unstarted_reply_for_resumed_user_speech(self) -> None:
        if (
            not self._is_reply_generation_active()
            or self._latency_probe.reply_output_started
        ):
            return
        logger.info(
            "TomoroSession stale reply cancelled reason=resumed_user_speech_before_output"
        )
        await self._cancel_reply_generation(status="cancelled")

    async def _run_internal_commands(self, commands: list[SessionCommand]) -> None:
        for command in commands:
            if command.type == "apply_stop_intent_ack":
                await self._apply_stop_intent_ack()
            elif command.type == "insert_stop_intent_observation":
                await self._insert_stop_intent_observation(command)
            elif command.type == "close_conversation_session":
                await self._close_conversation_session(
                    end_reason=str(command.payload.get("end_reason") or "unknown")
                )

    async def _insert_stop_intent_observation(self, command: SessionCommand) -> None:
        if self.stop_intent_store is None:
            return
        observation = command.payload.get("observation")
        if observation is None:
            return
        try:
            await self.stop_intent_store.insert_observation(observation)
        except Exception as exc:
            logger.warning(
                "TomoroSession stop-intent observation insert failed "
                "rule_kind=%s adopted_action=%s error=%s",
                getattr(observation, "rule_kind", None),
                getattr(observation, "adopted_action", None),
                exc,
            )

    async def _apply_stop_intent_ack(self) -> None:
        await self._cancel_reply_generation(status="interrupted")
        await self._send_reserved_audio_stop()
        self.audio_turns.begin_turn()
        await self._send_reserved_audio_start()
        chunk = self.stop_ack_audio_provider.chunk()
        outgoing = await self.audio_turns.reserve_audio_chunk(
            text=self.stop_ack_audio_provider.text,
            chunk=chunk,
        )
        await self._send_audio_chunk(outgoing)
        await self._send_reserved_audio_end()
        await self._send_event({"type": "reply_done", "control": "stop_ack"})

    async def _wait_for_reply_task(self) -> None:
        task = self._reply_task
        if task is None:
            return
        await task

    async def _run_tts_queue(
        self,
        queue: asyncio.Queue[tuple[str, str] | None],
    ) -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            text, style = item
            await self._flush_tts_text(text, style=style)

    async def _flush_tts_text(self, text: str, *, style: str) -> None:
        if self.tts_backend is None or not text.strip():
            return

        speech_text = text.strip()
        if self.speech_normalizer is not None:
            speech_text = await self.speech_normalizer.normalize(speech_text)

        if self._latency_probe.mark_tts_start_if_unmarked():
            logger.info(
                "TomoroSession latency tts_start "
                "speech_end_to_tts_start_ms=%.1f first_reply_text_to_tts_start_ms=%.1f "
                "text=%r",
                self._elapsed_since_speech_end_ms(),
                self._elapsed_since_first_reply_text_ms(),
                speech_text,
            )

        tts_input = TTSInput(text=speech_text, style=style)
        async for chunk in self.tts_backend.synthesize(tts_input):
            if self._latency_probe.mark_first_audio_chunk_if_unmarked():
                logger.info(
                    "TomoroSession latency first_audio_chunk "
                    "speech_end_to_first_audio_ms=%.1f "
                    "reply_start_to_first_audio_ms=%.1f "
                    "tts_start_to_first_audio_ms=%.1f text=%r bytes=%s",
                    self._elapsed_since_speech_end_ms(),
                    self._elapsed_since_reply_start_ms(),
                    self._elapsed_since_tts_start_ms(),
                    tts_input.text,
                    len(chunk.data),
                )
            await self._send_reserved_audio_start()
            outgoing = await self.audio_turns.reserve_audio_chunk(
                text=tts_input.text,
                chunk=chunk,
            )
            await self._send_audio_chunk(outgoing)

    async def _record_stop_intent_observation(
        self,
        transcript: Transcript,
        *,
        rule_kind: str,
        adopted_action: str,
    ) -> None:
        if self.stop_intent_store is None:
            return
        observation = build_stop_observation(
            transcript_text=transcript.text,
            conversation_session_id=self.active_conversation_session_id,
            turn_id=self.audio_turns.active_turn_id,
            rule_kind=rule_kind,
            adopted_action=adopted_action,
            playback_state_json={
                "playback_state": self.audio_turns.playback_state,
                "client_playback_active": self.audio_turns.is_client_playback_active(),
                "echo_grace_active": self.audio_turns.is_playback_echo_grace_active(),
            },
            reply_state_json={
                "reply_active": self._is_reply_generation_active(),
                "first_reply_text_emitted": self._latency_probe.first_reply_text_at
                is not None,
                "first_audio_chunk_emitted": self._latency_probe.first_audio_chunk_at
                is not None,
            },
        )
        await self._run_internal_commands(
            [
                SessionCommand(
                    type="insert_stop_intent_observation",
                    payload={"observation": observation},
                )
            ]
        )

    async def _maybe_record_stop_intent_observation_for_decision(
        self,
        transcript: Transcript,
        decision,
    ) -> None:
        if decision is not None and decision.mode == "withdraw":
            await self._record_stop_intent_observation(
                transcript,
                rule_kind="withdraw_rule",
                adopted_action="withdraw",
            )
            return
        if should_record_stop_intent_candidate(transcript.text):
            await self._record_stop_intent_observation(
                transcript,
                rule_kind="stop_candidate",
                adopted_action=(
                    decision.mode
                    if decision is not None and decision.mode != "observer"
                    else "observer"
                ),
            )

    def _classify_barge_in(self, transcript: Transcript):
        in_active_playback = self.audio_turns.is_client_playback_active()
        in_playback_echo_grace = self.audio_turns.is_playback_echo_grace_active()
        if self.barge_in_detector is None or not (
            self.audio_turns.is_tomoko_speaking()
            or in_active_playback
            or in_playback_echo_grace
            or self._is_reply_generation_active()
        ):
            return None
        decision = self.barge_in_detector.classify(
            BargeInContext(
                transcript=transcript.text,
                recent_tomoko_text=self.audio_turns.recent_tomoko_text,
                speaking_elapsed_ms=self.audio_turns.speaking_elapsed_ms,
            )
        )
        if in_active_playback and decision.kind != "hard_interrupt":
            return BargeInDecision(
                kind="echo",
                action="continue_speaking",
                reason="playback_active_chunk",
            )
        if in_playback_echo_grace and decision.kind != "hard_interrupt":
            return BargeInDecision(
                kind="echo",
                action="continue_speaking",
                reason="playback_ended_grace",
            )
        return decision

    def _is_reply_generation_active(self) -> bool:
        return bool(
            (self._reply_task is not None and not self._reply_task.done())
            or (self._tts_worker_task is not None and not self._tts_worker_task.done())
        )

    async def _send_reserved_audio_start(self) -> None:
        event = await self.audio_turns.reserve_start_event()
        if event is None:
            return
        await self._send_event(event)

    async def _send_reserved_audio_end(self) -> None:
        event = await self.audio_turns.reserve_end_event()
        if event is None:
            return
        await self._send_event(event)

    async def _send_reserved_audio_stop(self) -> None:
        event = await self.audio_turns.reserve_stop_event()
        if event is None:
            return
        await self._send_event(event)

    def _reset_latency_probe(self) -> None:
        self._latency_probe.reset()

    async def _load_recent_context(self, transcript: Transcript) -> list[ConversationTurn]:
        snapshot = await self._build_context_snapshot(transcript, depth="fast")
        return snapshot.recent_turns

    async def _build_context_snapshot(
        self,
        transcript: Transcript,
        *,
        depth: ContextDepth,
        explicit_memory_cue: bool = False,
    ) -> TomokoContextSnapshot:
        policy = ContextBuildPolicy.for_depth(depth)
        if depth == "deep" and explicit_memory_cue:
            policy = replace(policy, max_build_ms=300)
        builder = self.context_snapshot_builder or ContextSnapshotBuilder(
            conversation_log_reader=self.conversation_log_writer,
            embedding_backend=self.embedding_backend,
            memory_store=self.memory_store,
            session_summary_store=self.session_summary_store,
            persona_store=self.persona_store,
        )
        return await builder.build(
            text=transcript.text,
            speaker=transcript.speaker,
            device_id=transcript.device_id,
            active_session_id=self.active_conversation_session_id,
            policy=policy,
        )

    def _recent_turns_with_precomputed_topic(
        self,
        recent_turns: list[ConversationTurn],
    ) -> list[ConversationTurn]:
        text = self._last_precomputed_reply_text
        if not text:
            return recent_turns
        if any(turn.speaker == "tomoko" and turn.text == text for turn in recent_turns):
            return recent_turns
        timestamp = self._last_precomputed_reply_at or datetime.now(UTC)
        logger.info(
            "TomoroSession context includes last_precomputed_reply reason=%s "
            "source=%s candidate_id=%s",
            self._last_precomputed_reply_reason,
            self._last_precomputed_reply_source,
            self._last_precomputed_reply_candidate_id,
        )
        return [
            *recent_turns,
            ConversationTurn(
                speaker="tomoko",
                text=text,
                timestamp=timestamp,
                emotion="neutral",
            ),
        ]

    async def _ensure_conversation_session(
        self,
        *,
        device_id: str,
        start_reason: str,
    ) -> UUID | None:
        if self.active_conversation_session_id is not None:
            return self.active_conversation_session_id
        if self.conversation_session_store is None:
            return None
        session_id = await self.conversation_session_store.create_session(
            device_id=device_id,
            start_reason=start_reason,
        )
        self.active_conversation_session_id = session_id
        logger.info(
            "TomoroSession conversation session started id=%s reason=%s device_id=%s",
            session_id,
            start_reason,
            device_id,
        )
        return session_id

    async def _close_conversation_session(self, *, end_reason: str) -> None:
        session_id = self.active_conversation_session_id
        if session_id is None:
            return
        if self.conversation_session_store is not None:
            await self.conversation_session_store.close_session(
                session_id,
                end_reason=end_reason,
            )
        self.active_conversation_session_id = None
        logger.info(
            "TomoroSession conversation session closed id=%s reason=%s",
            session_id,
            end_reason,
        )
        self._clear_retrieved_context_carryover(reason=end_reason)

    async def _write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
    ) -> UUID | None:
        assert self.conversation_log_writer is not None
        write_user_turn = self.conversation_log_writer.write_user_turn
        if _accepts_keyword(write_user_turn, "conversation_session_id"):
            return await write_user_turn(
                transcript,
                participation_mode=participation_mode,
                conversation_session_id=self.active_conversation_session_id,
            )
        return await write_user_turn(
            transcript,
            participation_mode=participation_mode,
        )

    async def _write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus,
    ) -> None:
        if self.conversation_log_writer is None:
            return
        write_tomoko_turn = self.conversation_log_writer.write_tomoko_turn
        if _accepts_keyword(write_tomoko_turn, "conversation_session_id"):
            conversation_log_id = await write_tomoko_turn(
                text=text,
                emotion=emotion,
                device_id=device_id,
                status=status,
                conversation_session_id=self.active_conversation_session_id,
            )
        else:
            conversation_log_id = await write_tomoko_turn(
                text=text,
                emotion=emotion,
                device_id=device_id,
                status=status,
            )
        if status == "completed" and conversation_log_id is not None:
            self._schedule_conversation_embedding(
                conversation_log_id=conversation_log_id,
                text=text,
            )

    def _schedule_conversation_embedding(
        self,
        *,
        conversation_log_id,
        text: str,
    ) -> None:
        if self.embedding_backend is None or self.memory_store is None or not text.strip():
            return
        asyncio.create_task(
            self._write_conversation_embedding(
                conversation_log_id=conversation_log_id,
                text=text,
            )
        )

    async def _write_conversation_embedding(
        self,
        *,
        conversation_log_id,
        text: str,
    ) -> None:
        if self.embedding_backend is None or self.memory_store is None:
            return
        try:
            embedding = await self.embedding_backend.embed_passage(text)
            await self.memory_store.write_embedding(
                conversation_log_id=conversation_log_id,
                embedding=embedding,
                model=self.embedding_backend.model,
            )
            logger.info(
                "TomoroSession conversation embedding stored log_id=%s model=%s",
                conversation_log_id,
                self.embedding_backend.model,
            )
        except Exception as e:
            logger.warning(
                "TomoroSession conversation embedding failed log_id=%s error=%s",
                conversation_log_id,
                e,
            )

    def _elapsed_since_speech_end_ms(self) -> float:
        return self._latency_probe.elapsed_since_speech_end_ms()

    def _elapsed_since_reply_start_ms(self) -> float:
        return self._latency_probe.elapsed_since_reply_start_ms()

    def _elapsed_since_first_reply_text_ms(self) -> float:
        return self._latency_probe.elapsed_since_first_reply_text_ms()

    def _elapsed_since_tts_start_ms(self) -> float:
        return self._latency_probe.elapsed_since_tts_start_ms()


def _withdraw_decision(transcript: Transcript):
    text = transcript.text
    withdraw_phrases = (
        "静かにして",
        "入らないで",
        "黙ってて",
        "だまってて",
        "話さないで",
    )
    if any(phrase in text for phrase in withdraw_phrases):
        from server.shared.models import ParticipationDecision

        return ParticipationDecision(
            should_participate=False,
            mode="withdraw",
            reason="explicit_withdraw_request",
        )
    return None


def _start_reason_from_participation_mode(mode: ParticipationMode) -> StartReason:
    if mode == "called":
        return "wake_word"
    if mode == "invited":
        return "followup"
    raise ValueError(f"participation mode does not start a reply: {mode}")


def _playback_telemetry_from_event(event: SessionEvent) -> PlaybackTelemetry:
    if event.type not in {"playback_started", "playback_ended"}:
        raise ValueError(f"not a playback event: {event.type}")
    return PlaybackTelemetry(
        type=event.type,  # type: ignore[arg-type]
        turn_id=_optional_str_payload(event.payload.get("turn_id")),
        chunk_id=_optional_int_payload(event.payload.get("chunk_id")),
        scheduled_audio_time=_optional_float_payload(
            event.payload.get("scheduled_audio_time")
        ),
        sent_audio_time=_optional_float_payload(event.payload.get("sent_audio_time")),
        audio_context_time=_optional_float_payload(
            event.payload.get("audio_context_time")
        ),
        performance_now_ms=_optional_float_payload(
            event.payload.get("performance_now_ms")
        ),
    )


def _playback_payload(event: SessionEvent) -> dict[str, Any]:
    return {
        "turn_id": event.payload.get("turn_id"),
        "chunk_id": event.payload.get("chunk_id"),
    }


def _candidate_policy_payload(event: SessionEvent) -> dict[str, Any] | None:
    policy = event.payload.get("policy_decision")
    if isinstance(policy, CandidateSpeakDecision):
        return policy.to_json()
    return None


def _json_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe_value(value) for key, value in payload.items()}


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def _optional_str_payload(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int_payload(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float_payload(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _elapsed_ms(started_at: float | None) -> float:
    return elapsed_ms(started_at)


def _session_summary_hit_to_memory(hit: SessionSummaryHit) -> MemoryHit:
    return MemoryHit(
        speaker="tomoko",
        text=f"会話セッション要約: {hit.summary_text}",
        timestamp=hit.ended_at or hit.started_at,
        similarity=hit.similarity,
        source_id=f"session_summary:{hit.session_id}",
    )


def _retrieved_context_key(hit: MemoryHit) -> str:
    return retrieved_context_key(hit)


def _accepts_keyword(callable_obj: Callable[..., Any], keyword: str) -> bool:
    signature = inspect.signature(callable_obj)
    return keyword in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
