from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FollowUpQueue:
    items: list[str] = field(default_factory=list)
    discarded: bool = False

    def start_generation(self, previous_tomoko_speech: list[str], context: str) -> None:
        base = " ".join(previous_tomoko_speech)[-80:]
        self.items = [f"{base} / {context}".strip()]
        self.discarded = False

    def discard_on_user_reaction(self) -> None:
        self.items.clear()
        self.discarded = True

    def pop_ready(self) -> str | None:
        if self.discarded or not self.items:
            return None
        return self.items.pop(0)
