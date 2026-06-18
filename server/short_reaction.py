from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from server.shared.models import PromptRequest, PromptScope


class ShortReactionKind(StrEnum):
    BACKCHANNEL = "backchannel"
    SHORT_CONFIRMATION = "short_confirmation"
    LIGHT_ACK = "light_ack"
    WAIT_SIGNAL = "wait_signal"


@dataclass(frozen=True, slots=True)
class ShortReactionProposal:
    emotion: str
    text: str
    kind: ShortReactionKind


def parse_short_reaction(raw: str, kind: ShortReactionKind) -> ShortReactionProposal:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) != 2 or not lines[0].startswith("EMOTION:"):
        raise ValueError("short reaction must be EMOTION:<label> plus one short sentence")
    text = lines[1]
    if len(text) > 40 or "\n" in text:
        raise ValueError("short reaction text must be one short sentence")
    return ShortReactionProposal(emotion=lines[0].split(":", 1)[1].strip(), text=text, kind=kind)


@dataclass(slots=True)
class ShortReactionLifecycle:
    active_request_id: str | None = None
    discarded: bool = False

    def start(self, request: PromptRequest) -> None:
        if request.scope != PromptScope.SHORT:
            raise ValueError("short reaction lifecycle only accepts short scope")
        self.active_request_id = str(request.id)
        self.discarded = False

    def discard_if_stale(self, *, final_text: str, partial_text: str) -> bool:
        if final_text and partial_text and not final_text.startswith(partial_text):
            self.discarded = True
        return self.discarded
