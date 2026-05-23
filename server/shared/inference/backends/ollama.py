import httpx
from collections.abc import AsyncGenerator
from ollama import AsyncClient
from typing import Any

from server.shared.inference.backends.base import InferenceBackend


class OllamaBackend(InferenceBackend):
    def __init__(self, name: str, url: str, model: str, privacy_allowed: bool = True):
        self.name = name
        self.url = url
        self.model = model
        self.privacy_allowed = privacy_allowed
        self.client = AsyncClient(host=url)

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        formatted_messages = [{"role": "system", "content": system_prompt}] + messages
        
        response = await self.client.chat(
            model=self.model,
            messages=formatted_messages, # type: ignore
            stream=True,
        )
        async for part in response: # type: ignore
            if hasattr(part, "message") and part.message and hasattr(part.message, "content"):
                yield part.message.content
            elif isinstance(part, dict):
                if "message" in part and "content" in part["message"]:
                    yield part["message"]["content"]
