from __future__ import annotations


class ReplyEmotionState:
    """Owns the current reply emotion used by TTS style and display events."""

    def __init__(self, *, initial_emotion: str = "neutral") -> None:
        self.current = initial_emotion

    def update(self, emotion: str) -> str:
        self.current = emotion
        return self.current
