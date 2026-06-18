from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class LatencyProbeState:
    speech_end_at: float | None = None
    reply_start_at: float | None = None
    first_reply_text_at: float | None = None
    tts_start_at: float | None = None
    first_audio_chunk_at: float | None = None
    reply_output_started: bool = False
    reply_output_defer_until: float | None = None

    def reset(self) -> None:
        self.speech_end_at = None
        self.reply_start_at = None
        self.first_reply_text_at = None
        self.tts_start_at = None
        self.first_audio_chunk_at = None
        self.reply_output_started = False

    def mark_speech_end(self) -> None:
        self.speech_end_at = time.perf_counter()

    def mark_reply_start(self) -> None:
        self.reply_start_at = time.perf_counter()

    def mark_reply_output_started(self) -> None:
        self.reply_output_started = True

    def mark_first_reply_text_if_unmarked(self) -> bool:
        if self.first_reply_text_at is not None:
            return False
        self.first_reply_text_at = time.perf_counter()
        return True

    def mark_tts_start_if_unmarked(self) -> bool:
        if self.tts_start_at is not None:
            return False
        self.tts_start_at = time.perf_counter()
        return True

    def mark_first_audio_chunk_if_unmarked(self) -> bool:
        if self.first_audio_chunk_at is not None:
            return False
        self.first_audio_chunk_at = time.perf_counter()
        return True

    def elapsed_since_speech_end_ms(self) -> float:
        return elapsed_ms(self.speech_end_at)

    def elapsed_since_reply_start_ms(self) -> float:
        return elapsed_ms(self.reply_start_at)

    def elapsed_since_first_reply_text_ms(self) -> float:
        return elapsed_ms(self.first_reply_text_at)

    def elapsed_since_tts_start_ms(self) -> float:
        return elapsed_ms(self.tts_start_at)

    def defer_reply_output(self, *, max_ms: int) -> None:
        self.reply_output_defer_until = max(
            self.reply_output_defer_until or 0.0,
            time.perf_counter() + max_ms / 1000,
        )

    def consume_reply_output_defer_delay(self) -> float | None:
        defer_until = self.reply_output_defer_until
        if defer_until is None:
            return None
        remaining = defer_until - time.perf_counter()
        self.reply_output_defer_until = None
        if remaining <= 0:
            return None
        return min(remaining, 0.25)


def elapsed_ms(started_at: float | None) -> float:
    if started_at is None:
        return 0.0
    return (time.perf_counter() - started_at) * 1000
