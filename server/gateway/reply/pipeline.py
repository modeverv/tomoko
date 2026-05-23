from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from server.gateway.reply.audio import ReplyAudioPlanner
from server.gateway.reply.emotion import ReplyEmotionState
from server.gateway.reply.image import EmotionImageMapper
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
        image_mapper: EmotionImageMapper | None = None,
    ) -> None:
        self.emotion = ReplyEmotionState(initial_emotion=initial_emotion)
        self.audio = ReplyAudioPlanner()
        self.image_mapper = image_mapper or EmotionImageMapper()
        self.reply_text = ""

    @property
    def current_emotion(self) -> str:
        return self.emotion.current

    def handle_event(self, event: ThinkingEvent) -> list[ReplyCommand]:
        if event.type == "emotion":
            emotion = self.emotion.update(event.value)
            return [
                ReplyCommand(
                    action="emotion",
                    value=emotion,
                    style=emotion,
                    image=self.image_mapper.image_for(emotion),
                )
            ]

        if event.type == "text_delta":
            self.reply_text += event.value
            commands = [
                ReplyCommand(
                    action="text_delta",
                    value=event.value,
                    style=self.current_emotion,
                )
            ]
            commands.extend(
                ReplyCommand(
                    action="tts_text",
                    value=sentence,
                    style=self.current_emotion,
                )
                for sentence in self.audio.append_delta(event.value)
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
