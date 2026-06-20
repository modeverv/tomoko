from __future__ import annotations

import hashlib
from dataclasses import dataclass

from server.shared.models import (
    CancelPolicy,
    ContextSnapshot,
    ConversationHistoryItem,
    PromptRequest,
    PromptScope,
)


def is_clock_question(text: str) -> bool:
    lowered = text.lower()
    return "何時" in text or "いま何時" in text or "what time" in lowered


@dataclass(frozen=True, slots=True)
class PromptBuilderV2:
    system_header: str = "Tomoko v2: natural local voice conversation."

    def build_main_reply(
        self,
        snapshot: ContextSnapshot,
        current_utterance: str,
        *,
        concise: bool = False,
    ) -> PromptRequest:
        calendar = {} if is_clock_question(current_utterance) else snapshot.calendar_items
        instruction = "次のtomoko発話だけ返す。"
        runtime_context = self._format_runtime_context(snapshot, calendar)
        candidates = self._format_candidates(snapshot)
        sections = [
            "SYSTEM:",
            self._format_system(),
            "INSTRUCTION:",
            instruction,
            "SESSION_TRANSCRIPT:",
            self._format_session_transcript(snapshot, current_utterance),
        ]
        if runtime_context:
            sections.extend(["RUNTIME_CONTEXT:", runtime_context])
        if candidates:
            sections.extend(["VOLATILE_RECALL:", candidates])
        return PromptRequest(
            prompt_text="\n".join(sections),
            scope=PromptScope.MAIN,
            decision_id=None,
            utterance_id=None,
            candidate_id=None,
            priority=50,
            cancel_policy=CancelPolicy.CANCEL_ON_USER_SPEAKING,
            context_snapshot_id=snapshot.id,
            trace_id=snapshot.trace_id,
        )

    def _format_system(self) -> str:
        return self.system_header

    def _format_runtime_context(
        self,
        snapshot: ContextSnapshot,
        calendar: dict[str, str],
    ) -> str:
        lines: list[str] = []
        lines.extend(
            f"summary[{summary.keyword}]={summary.conclusion}"
            for summary in snapshot.summaries
        )
        lines.extend(f"calendar[{when}]={what}" for when, what in sorted(calendar.items()))
        if snapshot.user_status is not None:
            lines.append(f"user_status={snapshot.user_status.activity_label}")
        return "\n".join(lines)

    def _format_session_transcript(
        self,
        snapshot: ContextSnapshot,
        current_utterance: str,
    ) -> str:
        history = list(snapshot.recent_history)
        if not history:
            history = [
                ConversationHistoryItem(speaker="user", text=text)
                for text in snapshot.recent_utterances
            ]
        history.append(ConversationHistoryItem(speaker="user", text=current_utterance))
        return "\n".join(_format_transcript_item(item) for item in history)

    def _format_candidates(self, snapshot: ContextSnapshot) -> str:
        return "\n".join(
            f"candidate[{candidate.source}:{candidate.source_key}]={candidate.text}"
            for candidate in snapshot.candidates
            if candidate.lifecycle == "active"
        )


def _format_transcript_item(item: ConversationHistoryItem) -> str:
    speaker = "tomoko" if item.speaker == "tomoko" else "user"
    return f"{speaker}: {item.text}"


def prompt_cache_shape(prompt_text: str) -> dict[str, object]:
    system_body = _section_between(prompt_text, "SYSTEM:", "INSTRUCTION:")
    instruction_body = _section_between(prompt_text, "INSTRUCTION:", "SESSION_TRANSCRIPT:")
    tail = (
        prompt_text.split("SESSION_TRANSCRIPT:", 1)[1]
        if "SESSION_TRANSCRIPT:" in prompt_text
        else ""
    )
    transcript_body, runtime_context, volatile_recall = _split_trailing_context(tail.strip())
    return {
        "system_chars": len(system_body),
        "system_hash": _short_hash(system_body),
        "instruction_chars": len(instruction_body),
        "instruction_hash": _short_hash(instruction_body),
        "transcript_turns": sum(
            1
            for line in transcript_body.splitlines()
            if line.startswith(("user: ", "tomoko: "))
        ),
        "transcript_chars": len(transcript_body),
        "runtime_context_chars": len(runtime_context),
        "runtime_context_hash": _short_hash(runtime_context),
        "volatile_recall_chars": len(volatile_recall),
        "volatile_recall_hash": _short_hash(volatile_recall),
    }


def split_prompt_trailing_context(prompt_text: str) -> tuple[str, str, str]:
    tail = prompt_text.split("SESSION_TRANSCRIPT:", 1)[1].strip()
    return _split_trailing_context(tail)


def _split_trailing_context(text: str) -> tuple[str, str, str]:
    transcript_and_runtime, volatile = _split_marker(text, "VOLATILE_RECALL:")
    transcript, runtime = _split_marker(transcript_and_runtime, "RUNTIME_CONTEXT:")
    return transcript.strip(), runtime.strip(), volatile.strip()


def _split_marker(text: str, marker: str) -> tuple[str, str]:
    marker_with_newline = f"\n{marker}"
    if marker_with_newline not in text:
        return text, ""
    before, after = text.split(marker_with_newline, 1)
    return before, after.strip()


def _section_between(text: str, start_marker: str, end_marker: str) -> str:
    if start_marker not in text:
        return ""
    tail = text.split(start_marker, 1)[1]
    if end_marker in tail:
        return tail.split(end_marker, 1)[0].strip()
    return tail.strip()


def _short_hash(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
