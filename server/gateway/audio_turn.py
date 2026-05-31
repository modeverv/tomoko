from __future__ import annotations

import asyncio
import struct
import time
import uuid

from server.shared.models import AudioChunkOut, OutputLane, PlaybackTelemetry

TURN_AUDIO_OUTPUT_LANES: tuple[OutputLane, ...] = (
    "reply_turn",
    "initiative_turn",
    "stop_ack",
    "interrupting_turn",
)


class AudioTurnController:
    """Owns audio turn and playback telemetry state without doing WebSocket I/O."""

    def __init__(self, *, playback_echo_grace_ms: int = 1200) -> None:
        self._lock = asyncio.Lock()
        self._audio_sequence = 0
        self._recent_tomoko_text = ""
        self._tomoko_speaking_started_at: float | None = None
        self._tomoko_speaking_until = 0.0
        self._active_audio_turn_id: str | None = None
        self._audio_turn_started = False
        self._audio_turn_ended = False
        self._last_playback_started: PlaybackTelemetry | None = None
        self._last_playback_ended: PlaybackTelemetry | None = None
        self._active_playback_chunks: set[tuple[str | None, int | None]] = set()
        self._playback_echo_until = 0.0
        self._playback_echo_grace_ms = playback_echo_grace_ms

    @property
    def playback_echo_grace_ms(self) -> int:
        return self._playback_echo_grace_ms

    @property
    def recent_tomoko_text(self) -> str:
        return self._recent_tomoko_text

    @property
    def active_turn_id(self) -> str | None:
        return self._active_audio_turn_id

    @property
    def speaking_turn_id(self) -> str | None:
        if self.is_tomoko_speaking():
            return self._active_audio_turn_id
        return None

    @property
    def speaking_elapsed_ms(self) -> float:
        if self._tomoko_speaking_started_at is None:
            return 0.0
        return max(0.0, (time.monotonic() - self._tomoko_speaking_started_at) * 1000)

    @property
    def playback_state(self) -> str:
        if self.is_client_playback_active():
            return "client_playing"
        if self.is_tomoko_speaking():
            return "speaking"
        if self.is_playback_echo_grace_active():
            return "echo_grace"
        return "idle"

    def begin_turn(self, *, lane: OutputLane = "reply_turn") -> None:
        if lane not in TURN_AUDIO_OUTPUT_LANES:
            raise ValueError(f"output lane does not use AudioTurnController: {lane}")
        self._active_audio_turn_id = uuid.uuid4().hex
        self._audio_turn_started = False
        self._audio_turn_ended = False

    async def reserve_start_event(self) -> dict[str, str] | None:
        async with self._lock:
            if self._active_audio_turn_id is None:
                self.begin_turn()
            if self._audio_turn_started:
                return None
            assert self._active_audio_turn_id is not None
            self._audio_turn_started = True
            return {
                "type": "audio_start",
                "turn_id": self._active_audio_turn_id,
            }

    async def reserve_end_event(self) -> dict[str, str] | None:
        async with self._lock:
            if self._active_audio_turn_id is None:
                return None
            if not self._audio_turn_started or self._audio_turn_ended:
                return None
            self._audio_turn_ended = True
            return {
                "type": "audio_end",
                "turn_id": self._active_audio_turn_id,
            }

    async def reserve_stop_event(self) -> dict[str, str] | None:
        async with self._lock:
            if self._active_audio_turn_id is None:
                return None
            turn_id = self._active_audio_turn_id
            self._tomoko_speaking_until = 0.0
            self._active_audio_turn_id = None
            self._audio_turn_started = False
            self._audio_turn_ended = False
            return {
                "type": "audio_control",
                "action": "stop",
                "turn_id": turn_id,
            }

    async def reserve_audio_chunk(
        self, *, text: str, chunk: AudioChunkOut
    ) -> AudioChunkOut:
        async with self._lock:
            self._mark_tomoko_speaking(text=text, audio_data=chunk.data)
            outgoing = AudioChunkOut(
                data=chunk.data,
                sequence=self._audio_sequence,
                is_last=chunk.is_last,
            )
            self._audio_sequence += 1
            return outgoing

    async def handle_playback_telemetry(self, telemetry: PlaybackTelemetry) -> None:
        async with self._lock:
            if telemetry.turn_id is None:
                return
            chunk_key = (telemetry.turn_id, telemetry.chunk_id)
            if telemetry.type == "playback_started":
                self._last_playback_started = telemetry
                self._active_playback_chunks.add(chunk_key)
            elif telemetry.type == "playback_ended":
                self._last_playback_ended = telemetry
                self._active_playback_chunks.discard(chunk_key)
                self._playback_echo_until = max(
                    self._playback_echo_until,
                    time.monotonic() + self._playback_echo_grace_ms / 1000,
                )

    def is_tomoko_speaking(self) -> bool:
        return time.monotonic() <= self._tomoko_speaking_until

    def is_playback_echo_grace_active(self) -> bool:
        return time.monotonic() <= self._playback_echo_until

    def is_client_playback_active(self) -> bool:
        return bool(self._active_playback_chunks)

    def _mark_tomoko_speaking(self, *, text: str, audio_data: bytes) -> None:
        now = time.monotonic()
        duration = _wav_duration_seconds(audio_data)
        if duration is None:
            duration = max(0.6, len(text) * 0.12)
        self._recent_tomoko_text = _append_recent_text(self._recent_tomoko_text, text)
        self._tomoko_speaking_started_at = now
        self._tomoko_speaking_until = max(self._tomoko_speaking_until, now) + duration + 0.5


def _append_recent_text(previous: str, text: str, max_chars: int = 240) -> str:
    combined = f"{previous}{text}"
    if len(combined) <= max_chars:
        return combined
    return combined[-max_chars:]


def _wav_duration_seconds(audio_data: bytes) -> float | None:
    if len(audio_data) < 44 or audio_data[:4] != b"RIFF" or audio_data[8:12] != b"WAVE":
        return None
    offset = 12
    sample_rate: int | None = None
    channels: int | None = None
    bits_per_sample: int | None = None
    data_size: int | None = None
    while offset + 8 <= len(audio_data):
        chunk_id = audio_data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", audio_data, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_id == b"fmt " and chunk_size >= 16 and chunk_end <= len(audio_data):
            channels = struct.unpack_from("<H", audio_data, chunk_start + 2)[0]
            sample_rate = struct.unpack_from("<I", audio_data, chunk_start + 4)[0]
            bits_per_sample = struct.unpack_from("<H", audio_data, chunk_start + 14)[0]
        elif chunk_id == b"data":
            data_size = chunk_size
        offset = chunk_end + (chunk_size % 2)
    if not sample_rate or not channels or not bits_per_sample or data_size is None:
        return None
    bytes_per_second = sample_rate * channels * (bits_per_sample / 8)
    if bytes_per_second <= 0:
        return None
    return data_size / bytes_per_second
