from __future__ import annotations

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

    def build_main_reply(self, snapshot: ContextSnapshot, current_utterance: str) -> PromptRequest:
        calendar = {} if is_clock_question(current_utterance) else snapshot.calendar_items
        sections = [
            "SYSTEM:",
            self._format_system(snapshot, calendar),
            "INSTRUCTION:",
            "次のtomoko発話だけ返す。",
            "SESSION_TRANSCRIPT:",
            self._format_session_transcript(snapshot, current_utterance),
        ]
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

    def _format_system(self, snapshot: ContextSnapshot, calendar: dict[str, str]) -> str:
        lines: list[str] = []
        lines.append(self.system_header)
        lines.extend(
            f"summary={summary.keyword}: {summary.conclusion}"
            for summary in snapshot.summaries
        )
        lines.extend(f"calendar[{when}]={what}" for when, what in sorted(calendar.items()))
        if snapshot.user_status is not None:
            lines.append(f"user_status={snapshot.user_status.activity_label}")
        candidates = self._format_candidates(snapshot)
        if candidates:
            lines.append("VOLATILE_RECALL:")
            lines.append(candidates)
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
