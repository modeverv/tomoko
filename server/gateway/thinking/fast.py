import os
from pathlib import Path
from collections.abc import AsyncGenerator
from server.shared.inference.backends.base import InferenceBackend
from server.gateway.thinking.base import ThinkingMode


class ThinkFastMode(ThinkingMode):
    def __init__(self, persona_path: str | Path = "prompts/base_persona.md"):
        self.persona_path = Path(persona_path)
        self.system_prompt = self._load_persona()

    def _load_persona(self) -> str:
        if self.persona_path.exists():
            return self.persona_path.read_text(encoding="utf-8")
        return "あなたはトモコです。短く答えてください。"

    async def think(
        self, backend: InferenceBackend, transcript: str
    ) -> AsyncGenerator[str, None]:
        messages = [{"role": "user", "content": transcript}]
        async for chunk in backend.chat_stream(self.system_prompt, messages):
            yield chunk
