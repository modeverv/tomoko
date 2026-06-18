from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from server.edge.pipeline.stt import SpeechTranscriber, supports_streaming
from server.edge.pipeline.stt_filter import TranscriptFilter
from server.edge.pipeline.stt_gate import SttAudioFrontend, SttSignalGate
from server.edge.pipeline.vad import VADProcessor
from server.gateway.audio_turn import AudioTurnController
from server.gateway.reply.audio import ReplyAudioPlanner
from server.shared.edge_protocol import EdgeSpeechEvent
from server.shared.inference.tts.base import TTSBackend, TTSInput

logger = logging.getLogger(__name__)


class EdgeRemoteAudioSession:
    def __init__(
        self,
        *,
        device_id: str,
        vad_processor: VADProcessor,
        transcriber: SpeechTranscriber,
        transcript_filter: TranscriptFilter,
        send_browser_event: Callable[[dict[str, Any]], Any],
        send_gateway_event: Callable[[dict[str, Any]], Any],
        stt_audio_frontend: SttAudioFrontend | None = None,
        stt_signal_gate: SttSignalGate | None = None,
    ) -> None:
        self.device_id = device_id
        self.vad_processor = vad_processor
        self.transcriber = transcriber
        self.transcript_filter = transcript_filter
        self.stt_audio_frontend = stt_audio_frontend or SttAudioFrontend(
            sample_rate=vad_processor.sample_rate,
            signal_gate=stt_signal_gate,
        )
        self.send_browser_event = send_browser_event
        self.send_gateway_event = send_gateway_event

    async def process_audio_chunk(self, chunk_bytes: bytes) -> None:
        chunk = np.frombuffer(chunk_bytes, dtype=np.float32)
        result = self.vad_processor.process_chunk(chunk)
        if result.state_changed_to is not None:
            await self._send_browser_event(
                {"type": "state", "state": result.state_changed_to}
            )
        if result.segment is None and self.vad_processor.state == "listening":
            await self._maybe_emit_partial_transcript(chunk)
        if result.segment is None:
            return

        frontend_decision = self.stt_audio_frontend.process_segment(result.segment)
        logger.info(
            "EdgeRemoteAudioSession stt frontend action=%s reason=%s device_id=%s "
            "filters=%s rms_db=%.1f peak_db=%.1f active_frame_ratio=%.3f",
            frontend_decision.action,
            frontend_decision.reason,
            self.device_id,
            ",".join(frontend_decision.enabled_filters) or "none",
            frontend_decision.metrics.rms_db,
            frontend_decision.metrics.peak_db,
            frontend_decision.metrics.active_frame_ratio,
        )
        if not frontend_decision.accepted:
            self.vad_processor.reset()
            self._reset_transcriber_stream()
            await self._send_browser_event({"type": "state", "state": "idle"})
            return
        assert frontend_decision.segment is not None

        transcript = await self.transcriber.transcribe(frontend_decision.segment)
        logger.info(
            "EdgeRemoteAudioSession transcript text=%r device_id=%s audio_level_db=%s",
            transcript.text,
            transcript.device_id,
            transcript.audio_level_db,
        )
        decision = self.transcript_filter.evaluate(transcript, is_partial=False)
        if decision.action != "drop":
            await self._send_gateway_event(
                EdgeSpeechEvent(
                    device_id=self.device_id,
                    transcript=transcript.text,
                    speaker=transcript.speaker,
                    audio_level_db=transcript.audio_level_db,
                    observed_at=transcript.recorded_at,
                ).to_json()
            )
        self.vad_processor.reset()
        self._reset_transcriber_stream()
        await self._send_browser_event({"type": "state", "state": "idle"})

    async def _maybe_emit_partial_transcript(self, chunk: np.ndarray) -> None:
        if not supports_streaming(self.transcriber):
            return
        if not self.stt_audio_frontend.should_process_partial_chunk(chunk):
            return
        partial = await self.transcriber.process_stream_chunk(  # type: ignore[attr-defined]
            chunk,
            device_id=self.device_id,
            sample_rate=self.vad_processor.sample_rate,
        )
        if partial is None:
            return
        decision = self.transcript_filter.evaluate(partial, is_partial=True)
        if decision.action == "accept":
            await self._send_browser_event(
                {"type": "transcript_partial", "text": partial.text}
            )

    def _reset_transcriber_stream(self) -> None:
        if supports_streaming(self.transcriber):
            self.transcriber.reset_stream()  # type: ignore[attr-defined]

    async def _send_browser_event(self, event: dict[str, Any]) -> None:
        maybe_awaitable = self.send_browser_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def _send_gateway_event(self, event: dict[str, Any]) -> None:
        maybe_awaitable = self.send_gateway_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable


class EdgeReplyPlayer:
    def __init__(
        self,
        *,
        tts_backend: TTSBackend,
        send_browser_event: Callable[[dict[str, Any]], Any],
        send_browser_audio: Callable[[bytes], Any],
    ) -> None:
        self.tts_backend = tts_backend
        self.send_browser_event = send_browser_event
        self.send_browser_audio = send_browser_audio
        self.audio_turns = AudioTurnController()
        self.planner = ReplyAudioPlanner()
        self.current_style = "neutral"

    async def handle_gateway_payload(self, payload: str | dict[str, Any]) -> None:
        event = json.loads(payload) if isinstance(payload, str) else payload
        event_type = event.get("type")
        if event_type == "emotion":
            self.current_style = str(event.get("value") or "neutral")
            await self._send_browser_event(event)
            return
        if event_type == "reply_text":
            delta = str(event.get("delta") or "")
            await self._send_browser_event(event)
            for sentence in self.planner.append_delta(delta):
                await self._speak(sentence)
            return
        if event_type == "reply_done":
            remainder = self.planner.flush_remainder()
            if remainder is not None:
                await self._speak(remainder)
            await self._end_audio_turn()
            await self._send_browser_event(event)
            return
        await self._send_browser_event(event)

    async def _speak(self, text: str) -> None:
        if not text.strip():
            return
        tts_input = TTSInput(text=text.strip(), style=self.current_style)
        async for chunk in self.tts_backend.synthesize(tts_input):
            await self._ensure_audio_turn_started()
            outgoing = await self.audio_turns.reserve_audio_chunk(text=text, chunk=chunk)
            await self._send_browser_audio(outgoing.data)

    async def _ensure_audio_turn_started(self) -> None:
        event = await self.audio_turns.reserve_start_event()
        if event is not None:
            await self._send_browser_event(event)

    async def _end_audio_turn(self) -> None:
        event = await self.audio_turns.reserve_end_event()
        if event is not None:
            await self._send_browser_event(event)

    async def _send_browser_event(self, event: dict[str, Any]) -> None:
        maybe_awaitable = self.send_browser_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def _send_browser_audio(self, audio_data: bytes) -> None:
        maybe_awaitable = self.send_browser_audio(audio_data)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
