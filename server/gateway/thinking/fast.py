from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from server.gateway.thinking.base import ThinkingMode
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import ThinkingEvent, ThinkingInput

EMOTION_PREFIX = "EMOTION:"
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
        header_buffer = ""
        header_parsed = False
        async for chunk in backend.chat_stream(
            self._build_system_prompt(thinking_input),
            messages,
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

    sections: list[str] = []
    if snapshot.lexicon_terms:
        terms = "\n".join(
            f"- {term.term}: {term.meaning}"
            + (f" (tone={term.tone})" if term.tone else "")
            for term in snapshot.lexicon_terms
        )
        sections.append(
            "会話で使える用語メモです。必要な時だけ自然に使ってください。\n"
            f"{terms}"
        )
    if snapshot.persona_slice is not None:
        slice_ = snapshot.persona_slice
        details: list[str] = []
        if slice_.preferred_address:
            details.append(f"- 呼び方: {slice_.preferred_address}")
        if slice_.sentence_length:
            details.append(f"- 文の長さ: {slice_.sentence_length}")
        if slice_.honorific_level:
            details.append(f"- 敬語レベル: {slice_.honorific_level}")
        if slice_.signature_phrases:
            details.append(f"- 印象的な言い回し: {', '.join(slice_.signature_phrases)}")
        if details:
            sections.append(
                "現在の人格スナップショットからの話し方メモです。"
                "基本人格を上書きせず、自然な範囲で反映してください。\n"
                + "\n".join(details)
            )
    return "\n\n".join(sections)
