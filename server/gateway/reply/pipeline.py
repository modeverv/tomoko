from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from server.gateway.reply.audio import ReplyAudioPlanner
from server.gateway.reply.display import ReplyDisplayPlanner
from server.gateway.reply.text import ReplyTextSanitizer
from server.shared.models import ThinkingEvent

ReplyAction = Literal["emotion", "text_delta", "tts_text", "done"]


@dataclass(frozen=True)
class ReplyCommand:
    action: ReplyAction
    value: str
    style: str = "neutral"
    image: str | None = None


class ReplyPipeline:
    """Converts thinking events into reply display and TTS commands."""

    def __init__(
        self,
        *,
        initial_emotion: str = "neutral",
    ) -> None:
        self.display = ReplyDisplayPlanner(initial_emotion=initial_emotion)
        self.audio = ReplyAudioPlanner()
        self.text = ReplyTextSanitizer()
        self.reply_text = ""

    @property
    def current_emotion(self) -> str:
        return self.display.current_emotion

    def handle_event(self, event: ThinkingEvent) -> list[ReplyCommand]:
        if event.type == "emotion":
            display = self.display.update_emotion(event.value)
            return [
                ReplyCommand(
                    action="emotion",
                    value=display.emotion,
                    style=display.emotion,
                    image=display.image,
                )
            ]

        if event.type == "text_delta":
            raw_delta = event.value
            delta = self.text.sanitize_delta(raw_delta)
            commands: list[ReplyCommand] = []
            if delta:
                self.reply_text += delta
                commands.append(
                    ReplyCommand(
                        action="text_delta",
                        value=delta,
                        style=self.current_emotion,
                    )
                )
            commands.extend(
                ReplyCommand(
                    action="tts_text",
                    value=sentence,
                    style=self.current_emotion,
                )
                for sentence in self.audio.append_delta(raw_delta)
            )
            return commands

        if event.type == "done":
            commands: list[ReplyCommand] = []
            remainder = self.audio.flush_remainder()
            if remainder is not None:
                commands.append(
                    ReplyCommand(
                        action="tts_text",
                        value=remainder,
                        style=self.current_emotion,
                    )
                )
            commands.append(
                ReplyCommand(action="done", value="", style=self.current_emotion)
            )
            return commands

        return []
