from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from server.shared.models import ThinkingEvent

TTS_FLUSH_PUNCTUATION = "。！？"

ReplyAudioAction = Literal["emotion", "text_delta", "tts_text", "done"]


@dataclass(frozen=True)
class ReplyAudioCommand:
    action: ReplyAudioAction
    value: str
    style: str = "neutral"


class ReplyAudioPipeline:
    """Tracks streamed thinking output and decides reply/TTS commands."""

    def __init__(self, *, initial_emotion: str = "neutral") -> None:
        self.current_emotion = initial_emotion
        self.reply_text = ""
        self._tts_buffer = ""

    def handle_event(self, event: ThinkingEvent) -> list[ReplyAudioCommand]:
        if event.type == "emotion":
            self.current_emotion = event.value
            return [
                ReplyAudioCommand(
                    action="emotion",
                    value=event.value,
                    style=self.current_emotion,
                )
            ]

        if event.type == "text_delta":
            self.reply_text += event.value
            self._tts_buffer += event.value
            commands = [
                ReplyAudioCommand(
                    action="text_delta",
                    value=event.value,
                    style=self.current_emotion,
                )
            ]
            sentences, self._tts_buffer = _split_flushable_sentences(self._tts_buffer)
            commands.extend(
                ReplyAudioCommand(
                    action="tts_text",
                    value=sentence,
                    style=self.current_emotion,
                )
                for sentence in sentences
            )
            return commands

        if event.type == "done":
            commands: list[ReplyAudioCommand] = []
            if self._tts_buffer.strip():
                commands.append(
                    ReplyAudioCommand(
                        action="tts_text",
                        value=self._tts_buffer.strip(),
                        style=self.current_emotion,
                    )
                )
            self._tts_buffer = ""
            commands.append(
                ReplyAudioCommand(action="done", value="", style=self.current_emotion)
            )
            return commands

        return []


def _split_flushable_sentences(text: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    remainder = text
    while True:
        flush_index = _first_sentence_end_index(remainder)
        if flush_index is None:
            return sentences, remainder
        sentence = remainder[: flush_index + 1].strip()
        remainder = remainder[flush_index + 1 :]
        if sentence:
            sentences.append(sentence)


def _first_sentence_end_index(text: str) -> int | None:
    indexes = [text.find(punctuation) for punctuation in TTS_FLUSH_PUNCTUATION]
    found = [index for index in indexes if index >= 0]
    if not found:
        return None
    return min(found)
