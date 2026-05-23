from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any, Literal

import numpy as np

from server.edge.participation.base import ParticipationJudge
from server.edge.pipeline.stt import SpeechTranscriber, supports_streaming
from server.edge.pipeline.stt_filter import TranscriptFilter
from server.edge.pipeline.vad import VADProcessor
from server.gateway.audio_turn import AudioTurnController
from server.gateway.reply_audio import ReplyAudioPipeline
from server.gateway.thinking.base import ThinkingMode
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.shared.db import AmbientLogWriter, ConversationLogWriter
from server.shared.inference.router import InferenceRouter
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import (
    AttentionMode,
    AudioChunkOut,
    BargeInContext,
    BargeInDecision,
    ParticipationContext,
    PlaybackTelemetry,
    SpeechSegment,
    ThinkingInput,
    Transcript,
    TTSInput,
)

SessionState = Literal["idle", "listening", "processing"]

logger = logging.getLogger(__name__)
EMOTION_TO_IMAGE = {
    "neutral": "/assets/images/tomoko-neutral.svg",
    "happy": "/assets/images/tomoko-happy.svg",
    "surprised": "/assets/images/tomoko-surprised.svg",
    "sad": "/assets/images/tomoko-sad.svg",
    "thinking": "/assets/images/tomoko-thinking.svg",
    "gentle": "/assets/images/tomoko-gentle.svg",
    "excited": "/assets/images/tomoko-excited.svg",
}


class TomoroSession:
    def __init__(
        self,
        *,
        vad_processor: VADProcessor,
        send_event: Callable[[dict[str, str]], Any],
        send_audio: Callable[[bytes], Any] | None = None,
        transcriber: SpeechTranscriber | None = None,
        participation_judge: ParticipationJudge | None = None,
        ambient_log_writer: AmbientLogWriter | None = None,
        conversation_log_writer: ConversationLogWriter | None = None,
        router: InferenceRouter | None = None,
        thinking_mode: ThinkingMode | None = None,
        tts_backend: TTSBackend | None = None,
        barge_in_detector: BargeInDetector | None = None,
        transcript_filter: TranscriptFilter | None = None,
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
        self.router = router
        self.thinking_mode = thinking_mode
        self.tts_backend = tts_backend
        self.barge_in_detector = barge_in_detector
        self.transcript_filter = transcript_filter
        self.state: SessionState = "idle"
        self.attention_mode: AttentionMode = "ambient"
        self.latest_segment: SpeechSegment | None = None
        self._attention_idle_ms = 0.0
        self._engaged_timeout_ms = engaged_timeout_ms
        self._cooldown_timeout_ms = cooldown_timeout_ms
        self.audio_turns = AudioTurnController(
            playback_echo_grace_ms=playback_echo_grace_ms
        )

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
        if result.segment is None and self.state == "idle":
            await self._advance_attention_idle(len(chunk))
        if result.segment is not None:
            self.latest_segment = result.segment
            await self._handle_finished_speech(result.segment)
        return result.segment

    async def _handle_finished_speech(self, segment: SpeechSegment) -> None:
        if self.transcriber is None:
            return

        transcript = await self.transcriber.transcribe(segment)
        logger.info(
            "TomoroSession transcript text=%r speaker=%s audio_level_db=%s "
            "attention_mode=%s state=%s",
            transcript.text,
            transcript.speaker,
            transcript.audio_level_db,
            self.attention_mode,
            self.state,
        )
        filter_decision = self._filter_transcript(transcript, is_partial=False)
        if filter_decision.action == "drop":
            self.vad_processor.reset()
            self._reset_transcriber_stream()
            await self._transition("idle")
            return
        previous_attention = self.attention_mode
        barge_in_decision = self._classify_barge_in(transcript)
        if barge_in_decision is not None:
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
                await self._stop_active_audio_turn()
            if barge_in_decision.action == "continue_speaking":
                if self.ambient_log_writer is not None:
                    await self.ambient_log_writer.write(
                        transcript,
                        tomoko_participated=False,
                        attention_mode=previous_attention,
                        attended=False,
                        participation_mode="observer",
                    )
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

        if decision is not None and decision.should_participate:
            logger.info(
                "TomoroSession participation mode=%s reason=%s",
                decision.mode,
                decision.reason,
            )
            await self._transition_attention("engaged")
            await self._send_event({"type": "participation", "mode": decision.mode})
            if self.conversation_log_writer is not None:
                await self.conversation_log_writer.write_user_turn(
                    transcript,
                    participation_mode=decision.mode,
                )
            
            if self.router is not None and self.thinking_mode is not None:
                try:
                    await self._reply_to(transcript)
                except Exception as e:
                    logger.error("Error generating reply: %s", e)

        self.vad_processor.reset()
        self._reset_transcriber_stream()
        await self._transition("idle")

    async def _maybe_emit_partial_transcript(self, chunk: np.ndarray) -> None:
        if not supports_streaming(self.transcriber):
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

    async def _reply_to(self, transcript: Transcript) -> None:
        if self.router is None or self.thinking_mode is None:
            return

        backend = await self.router.select("conversation", "privacy")
        thinking_input = ThinkingInput(
            text=transcript.text,
            speaker=transcript.speaker,
            context=[],
            emotion="neutral",
            device_id=transcript.device_id,
        )
        reply_audio = ReplyAudioPipeline(initial_emotion=thinking_input.emotion)
        self._begin_audio_turn()
        async for event in self.thinking_mode.think(backend, thinking_input):
            for command in reply_audio.handle_event(event):
                if command.action == "emotion":
                    await self._send_event(
                        {
                            "type": "emotion",
                            "value": command.value,
                            "image": _image_for_emotion(command.value),
                        }
                    )
                elif command.action == "text_delta":
                    await self._send_event({"type": "reply_text", "delta": command.value})
                elif command.action == "tts_text":
                    await self._flush_tts_text(command.value, style=command.style)
                elif command.action == "done":
                    reply_text = reply_audio.reply_text.strip()
                    if self.conversation_log_writer is not None and reply_text:
                        await self.conversation_log_writer.write_tomoko_turn(
                            text=reply_text,
                            emotion=reply_audio.current_emotion,
                            device_id=transcript.device_id,
                        )
                    await self._send_event({"type": "reply_done"})
                    await self._end_audio_turn()
                    self._note_attention_activity()

    async def handle_playback_telemetry(self, telemetry: PlaybackTelemetry) -> None:
        await self.audio_turns.handle_playback_telemetry(telemetry)
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

    async def _send_event(self, event: dict[str, str]) -> None:
        maybe_awaitable = self.send_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def _send_audio_chunk(self, chunk: AudioChunkOut) -> None:
        if self.send_audio is None:
            return
        maybe_awaitable = self.send_audio(chunk.data)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def _flush_tts_text(self, text: str, *, style: str) -> None:
        if self.tts_backend is None or not text.strip():
            return

        tts_input = TTSInput(text=text.strip(), style=style)
        async for chunk in self.tts_backend.synthesize(tts_input):
            await self._ensure_audio_turn_started()
            outgoing = await self._reserve_audio_chunk(
                text=tts_input.text,
                chunk=chunk,
            )
            await self._send_audio_chunk(outgoing)

    def _classify_barge_in(self, transcript: Transcript):
        in_active_playback = self._is_client_playback_active()
        in_playback_echo_grace = self._is_playback_echo_grace_active()
        if self.barge_in_detector is None or not (
            self._is_tomoko_speaking() or in_active_playback or in_playback_echo_grace
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

    def _is_tomoko_speaking(self) -> bool:
        return self.audio_turns.is_tomoko_speaking()

    def _is_playback_echo_grace_active(self) -> bool:
        return self.audio_turns.is_playback_echo_grace_active()

    def _is_client_playback_active(self) -> bool:
        return self.audio_turns.is_client_playback_active()

    async def _reserve_audio_chunk(self, *, text: str, chunk: AudioChunkOut) -> AudioChunkOut:
        return await self.audio_turns.reserve_audio_chunk(text=text, chunk=chunk)

    def _mark_tomoko_speaking(self, *, text: str, audio_data: bytes) -> None:
        self.audio_turns._mark_tomoko_speaking(text=text, audio_data=audio_data)

    def _begin_audio_turn(self) -> None:
        self.audio_turns.begin_turn()

    async def _ensure_audio_turn_started(self) -> None:
        event = await self._reserve_audio_start_event()
        if event is None:
            return
        await self._send_event(event)

    async def _end_audio_turn(self) -> None:
        event = await self._reserve_audio_end_event()
        if event is None:
            return
        await self._send_event(event)

    async def _stop_active_audio_turn(self) -> None:
        event = await self._reserve_audio_stop_event()
        if event is None:
            return
        await self._send_event(event)

    async def _reserve_audio_start_event(self) -> dict[str, str] | None:
        return await self.audio_turns.reserve_start_event()

    async def _reserve_audio_end_event(self) -> dict[str, str] | None:
        return await self.audio_turns.reserve_end_event()

    async def _reserve_audio_stop_event(self) -> dict[str, str] | None:
        return await self.audio_turns.reserve_stop_event()


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


def _image_for_emotion(emotion: str) -> str:
    return EMOTION_TO_IMAGE.get(emotion, EMOTION_TO_IMAGE["neutral"])
