from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from server.gateway.thinking.base import ThinkingMode
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.trace import chat_stream_with_trace_role
from server.shared.models import ThinkingEvent, ThinkingInput
from server.shared.persona_prompt import format_persona_prompt_slice_for_prompt

EMOTION_PREFIX = "EMOTION:"
logger = logging.getLogger(__name__)
EMOTIONS = {
    "neutral",
    "happy",
    "surprised",
    "sad",
    "thinking",
    "gentle",
    "excited",
}


class ThinkFastMode(ThinkingMode):
    def __init__(self, persona_path: str | Path = "prompts/base_persona.md"):
        self.persona_path = Path(persona_path)
        self.system_prompt = self._load_persona()

    def _load_persona(self) -> str:
        if self.persona_path.exists():
            return self.persona_path.read_text(encoding="utf-8")
        return "あなたはトモコです。短く答えてください。"

    def _build_system_prompt(self, thinking_input: ThinkingInput) -> str:
        context_prompt = _format_context_snapshot_prompt(thinking_input)
        if not context_prompt:
            return self.system_prompt
        return f"{self.system_prompt}\n\n{context_prompt}"

    async def think(
        self, backend: InferenceBackend, thinking_input: ThinkingInput
    ) -> AsyncGenerator[ThinkingEvent, None]:
        messages = [
            {
                "role": "assistant" if turn.speaker == "tomoko" else "user",
                "content": turn.text,
            }
            for turn in thinking_input.context
        ]
        messages.append({"role": "user", "content": thinking_input.text})
        system_prompt = self._build_system_prompt(thinking_input)
        logger.info(
            "ThinkFastMode llm_prompt backend=%s payload=%s",
            backend.name,
            json.dumps(
                {
                    "system_prompt": system_prompt,
                    "messages": messages,
                    "device_id": thinking_input.device_id,
                    "speaker": thinking_input.speaker,
                },
                ensure_ascii=False,
            ),
        )
        header_buffer = ""
        header_parsed = False
        async for chunk in chat_stream_with_trace_role(
            backend,
            system_prompt,
            messages,
            trace_role="conversation",
        ):
            if not chunk:
                continue

            if header_parsed:
                yield ThinkingEvent(type="text_delta", value=chunk)
                continue

            header_buffer += chunk
            if "\n" not in header_buffer:
                inline_emotion = _parse_inline_emotion_header(header_buffer)
                if inline_emotion is not None:
                    emotion, remainder = inline_emotion
                    yield ThinkingEvent(type="emotion", value=emotion)
                    if remainder:
                        yield ThinkingEvent(type="text_delta", value=remainder)
                    header_parsed = True
                    header_buffer = ""
                    continue
                if EMOTION_PREFIX.startswith(header_buffer) or header_buffer.startswith(
                    EMOTION_PREFIX
                ):
                    continue
                header_parsed = True
                yield ThinkingEvent(type="text_delta", value=header_buffer)
                header_buffer = ""
                continue

            first_line, remainder = header_buffer.split("\n", 1)
            emotion = _parse_emotion_line(first_line)
            if emotion is not None:
                yield ThinkingEvent(type="emotion", value=emotion)
                if remainder:
                    yield ThinkingEvent(type="text_delta", value=remainder)
            else:
                yield ThinkingEvent(type="text_delta", value=header_buffer)
            header_parsed = True

        if not header_parsed and header_buffer:
            emotion = _parse_emotion_line(header_buffer)
            if emotion is not None:
                yield ThinkingEvent(type="emotion", value=emotion)
            else:
                yield ThinkingEvent(type="text_delta", value=header_buffer)
        yield ThinkingEvent(type="done", value="")


def _parse_emotion_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith(EMOTION_PREFIX):
        return None
    emotion = stripped.removeprefix(EMOTION_PREFIX).strip()
    if emotion not in EMOTIONS:
        return None
    return emotion


def _parse_inline_emotion_header(text: str) -> tuple[str, str] | None:
    if not text.startswith(EMOTION_PREFIX):
        return None

    rest = text.removeprefix(EMOTION_PREFIX).lstrip()
    if not rest:
        return None

    parts = rest.split(maxsplit=1)
    if len(parts) != 2:
        return None

    emotion, remainder = parts
    if emotion not in EMOTIONS:
        return None
    return emotion, remainder


def _format_context_snapshot_prompt(thinking_input: ThinkingInput) -> str:
    snapshot = thinking_input.context_snapshot
    if snapshot is None:
        return ""

    return format_persona_prompt_slice_for_prompt(
        persona_slice=snapshot.persona_slice,
        lexicon_terms=list(snapshot.lexicon_terms),
    )
