from __future__ import annotations

from server.gateway.thinking.fast import ThinkFastMode
from server.shared.models import ThinkingInput


class ThinkDeepMode(ThinkFastMode):
    def _build_system_prompt(self, thinking_input: ThinkingInput) -> str:
        return super()._build_system_prompt(thinking_input)
