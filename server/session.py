from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any, Literal

import numpy as np

from server.edge.participation.base import ParticipationJudge
from server.edge.pipeline.stt import SpeechTranscriber
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking.base import ThinkingMode
from server.shared.db import AmbientLogWriter, ConversationLogWriter
from server.shared.inference.router import InferenceRouter
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import (
    AttentionMode,
    AudioChunkOut,
    ParticipationContext,
    SpeechSegment,
    ThinkingInput,
    Transcript,
    TTSInput,
)

SessionState = Literal["idle", "listening", "processing"]

logger = logging.getLogger(__name__)
TTS_FLUSH_PUNCTUATION = "。！？"
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
        engaged_timeout_ms: int = 8000,
        cooldown_timeout_ms: int = 8000,
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
        self.state: SessionState = "idle"
        self.attention_mode: AttentionMode = "ambient"
        self.latest_segment: SpeechSegment | None = None
        self._audio_sequence = 0
        self._attention_idle_ms = 0.0
        self._engaged_timeout_ms = engaged_timeout_ms
        self._cooldown_timeout_ms = cooldown_timeout_ms

    async def process_audio_chunk(self, chunk_bytes: bytes) -> SpeechSegment | None:
        chunk = np.frombuffer(chunk_bytes, dtype=np.float32)
        result = self.vad_processor.process_chunk(chunk)
        if result.segment is None:
            await self._advance_attention_idle(len(chunk))
        if result.state_changed_to is not None:
            await self._transition(result.state_changed_to)
        if result.segment is not None:
            self._note_attention_activity()
            self.latest_segment = result.segment
            await self._handle_finished_speech(result.segment)
        return result.segment

    async def _handle_finished_speech(self, segment: SpeechSegment) -> None:
        if self.transcriber is None:
            return

        transcript = await self.transcriber.transcribe(segment)
        previous_attention = self.attention_mode
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
        await self._transition("idle")

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
        tts_buffer = ""
        reply_text = ""
        current_emotion = thinking_input.emotion
        async for event in self.thinking_mode.think(backend, thinking_input):
            if event.type == "emotion":
                current_emotion = event.value
                await self._send_event(
                    {
                        "type": "emotion",
                        "value": event.value,
                        "image": _image_for_emotion(event.value),
                    }
                )
            elif event.type == "text_delta":
                await self._send_event({"type": "reply_text", "delta": event.value})
                reply_text += event.value
                tts_buffer += event.value
                tts_buffer = await self._flush_tts_sentences(
                    tts_buffer,
                    style=current_emotion,
                )
            elif event.type == "done":
                await self._flush_tts_text(tts_buffer, style=current_emotion)
                if self.conversation_log_writer is not None and reply_text.strip():
                    await self.conversation_log_writer.write_tomoko_turn(
                        text=reply_text.strip(),
                        emotion=current_emotion,
                        device_id=transcript.device_id,
                    )
                await self._send_event({"type": "reply_done"})
                self._note_attention_activity()

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

    async def _flush_tts_sentences(self, text: str, *, style: str) -> str:
        remainder = text
        while True:
            flush_index = _first_sentence_end_index(remainder)
            if flush_index is None:
                return remainder
            sentence = remainder[: flush_index + 1].strip()
            remainder = remainder[flush_index + 1 :]
            await self._flush_tts_text(sentence, style=style)

    async def _flush_tts_text(self, text: str, *, style: str) -> None:
        if self.tts_backend is None or not text.strip():
            return

        tts_input = TTSInput(text=text.strip(), style=style)
        async for chunk in self.tts_backend.synthesize(tts_input):
            outgoing = AudioChunkOut(
                data=chunk.data,
                sequence=self._audio_sequence,
                is_last=chunk.is_last,
            )
            self._audio_sequence += 1
            await self._send_audio_chunk(outgoing)


def _first_sentence_end_index(text: str) -> int | None:
    indexes = [text.find(punctuation) for punctuation in TTS_FLUSH_PUNCTUATION]
    found = [index for index in indexes if index >= 0]
    if not found:
        return None
    return min(found)


def _image_for_emotion(emotion: str) -> str:
    return EMOTION_TO_IMAGE.get(emotion, EMOTION_TO_IMAGE["neutral"])


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
