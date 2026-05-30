from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from server.gateway.thinking.base import ThinkingMode
from server.gateway.thinking.memory_prompt import format_long_term_memory_prompt
from server.gateway.thinking.short_memory_prompt import format_short_memory_prompt
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.trace import chat_stream_with_trace_role
from server.shared.models import CalendarEvent, ThinkingEvent, ThinkingInput
from server.shared.persona_prompt import format_persona_prompt_slice_for_prompt

EMOTION_PREFIX = "EMOTION:"
logger = logging.getLogger(__name__)
DEFAULT_PROMPT_LOG_PATH = Path("logs/conversation-prompts.jsonl")
EMOTIONS = {
    "neutral",
    "happy",
    "surprised",
    "sad",
    "thinking",
    "gentle",
    "excited",
}
WEEKDAYS_JA = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
CALENDAR_TIMEZONE = ZoneInfo("Asia/Tokyo")


class ThinkFastMode(ThinkingMode):
    def __init__(
        self,
        persona_path: str | Path = "prompts/base_persona.md",
        *,
        persona_overlay_path: str | Path | None = None,
        prompt_log_path: str | Path | None = DEFAULT_PROMPT_LOG_PATH,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.persona_path = Path(persona_path)
        self.persona_overlay_path = (
            Path(persona_overlay_path)
            if persona_overlay_path is not None
            else self.persona_path.with_name("persona_overlay.md")
        )
        self.prompt_log_path = (
            Path(prompt_log_path) if prompt_log_path is not None else None
        )
        self.now_provider = now_provider or (lambda: datetime.now().astimezone())
        self.system_prompt = self._load_system_prompt()

    def _load_persona(self) -> str:
        if self.persona_path.exists():
            return self.persona_path.read_text(encoding="utf-8")
        return "あなたはトモコです。短く答えてください。"

    def _load_persona_overlay(self) -> str:
        if self.persona_overlay_path.exists():
            return self.persona_overlay_path.read_text(encoding="utf-8").strip()
        return ""

    def _load_system_prompt(self) -> str:
        persona = self._load_persona().rstrip()
        overlay = self._load_persona_overlay()
        if not overlay:
            return persona
        return f"{persona}\n\n{overlay}"

    def _build_system_prompt(self, thinking_input: ThinkingInput) -> str:
        current_time_prompt = _format_current_time_prompt(self.now_provider())
        prompt_parts = [
            part
            for part in (
                current_time_prompt,
                _format_context_snapshot_prompt(thinking_input),
                _format_calendar_context_prompt(thinking_input),
                format_short_memory_prompt(thinking_input.short_memory_notes),
                format_long_term_memory_prompt(thinking_input.long_term_memory),
            )
            if part
        ]
        return f"{self.system_prompt}\n\n" + "\n\n".join(prompt_parts)

    def _append_prompt_log(
        self,
        *,
        backend_name: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        thinking_input: ThinkingInput,
    ) -> None:
        if self.prompt_log_path is None:
            return

        payload = {
            "logged_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "backend": backend_name,
            "system_prompt": system_prompt,
            "messages": messages,
            "device_id": thinking_input.device_id,
            "speaker": thinking_input.speaker,
        }
        try:
            self.prompt_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.prompt_log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception(
                "ThinkFastMode prompt file log failed path=%s",
                self.prompt_log_path,
            )

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
        self._append_prompt_log(
            backend_name=backend.name,
            system_prompt=system_prompt,
            messages=messages,
            thinking_input=thinking_input,
        )
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
        logger.info(
            "ThinkFastMode conversation_system_prompt backend=%s\n%s",
            backend.name,
            system_prompt,
        )
        logger.info(
            "ThinkFastMode conversation_messages backend=%s payload=%s",
            backend.name,
            json.dumps(messages, ensure_ascii=False),
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


def _format_current_time_prompt(now: datetime) -> str:
    local_now = now.astimezone() if now.tzinfo is not None else now
    timezone = local_now.tzname() or "local"
    return "\n".join(
        [
            "## CURRENT LOCAL TIME",
            (
                "現在日時: "
                f"{local_now.strftime('%Y-%m-%d %H:%M:%S')} {timezone}"
            ),
            f"曜日: {WEEKDAYS_JA[local_now.weekday()]}",
            "この日時と曜日を、今日・明日・昨日などの相対表現を解釈する基準にする。",
        ]
    )


def _format_context_snapshot_prompt(thinking_input: ThinkingInput) -> str:
    snapshot = thinking_input.context_snapshot
    if snapshot is None:
        return ""

    return format_persona_prompt_slice_for_prompt(
        persona_slice=snapshot.persona_slice,
        lexicon_terms=list(snapshot.lexicon_terms),
    )


def _format_calendar_context_prompt(thinking_input: ThinkingInput) -> str:
    snapshot = thinking_input.context_snapshot
    if snapshot is None or not snapshot.calendar_events:
        return ""

    lines = [
        "## CALENDAR CONTEXT",
        (
            "Google Calendar から取り込んだ予定です。"
            "予定の有無や時刻を答える時だけ参照し、"
            "これ自体をユーザー発話として扱わない。"
        ),
    ]
    for event in snapshot.calendar_events:
        lines.append(f"- {_format_calendar_event(event)}")
    return "\n".join(lines)


def _format_calendar_event(event: CalendarEvent) -> str:
    start = _calendar_time_text(event.start_time, all_day=event.all_day)
    if event.all_day:
        time_text = f"{start} 終日"
    elif event.end_time is not None and _same_local_date(
        event.start_time,
        event.end_time,
    ):
        end = event.end_time.astimezone(CALENDAR_TIMEZONE).strftime("%H:%M")
        time_text = f"{start}-{end}"
    else:
        time_text = start
    detail = event.summary
    if event.location:
        detail = f"{detail} @ {event.location}"
    return f"{time_text}: {detail}"


def _same_local_date(left: datetime, right: datetime) -> bool:
    left_local = left.astimezone(CALENDAR_TIMEZONE) if left.tzinfo is not None else left
    right_local = right.astimezone(CALENDAR_TIMEZONE) if right.tzinfo is not None else right
    return left_local.date() == right_local.date()


def _calendar_time_text(value: datetime, *, all_day: bool) -> str:
    local_value = (
        value.astimezone(CALENDAR_TIMEZONE) if value.tzinfo is not None else value
    )
    if all_day:
        return local_value.strftime("%Y-%m-%d")
    return local_value.strftime("%Y-%m-%d %H:%M")
