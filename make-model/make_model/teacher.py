from __future__ import annotations

import asyncio
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from make_model.schema import PrefixExample, TeacherLabel

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_semantic = importlib.import_module("server.tomoko.semantic")
deterministic_saturation = _semantic.deterministic_saturation
parse_saturation_output = _semantic.parse_saturation_output
saturation_prompt = _semantic.saturation_prompt
SATURATION_SYSTEM_PROMPT = _semantic.SATURATION_SYSTEM_PROMPT

DEFAULT_TEACHER_URL = "http://127.0.0.1:8082"
DEFAULT_TEACHER_MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"


class TeacherBackend(Protocol):
    async def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class TeacherConfig:
    source_model: str = DEFAULT_TEACHER_MODEL
    fallback_on_error: bool = True
    sleep_sec: float = 0.0


@dataclass(frozen=True, slots=True)
class OpenAICompatibleTeacher:
    url: str = DEFAULT_TEACHER_URL
    model: str = DEFAULT_TEACHER_MODEL
    max_tokens: int = 16
    temperature: float = 0.0
    timeout_sec: float = 60.0

    @classmethod
    def from_env(cls) -> OpenAICompatibleTeacher:
        return cls(
            url=os.environ.get("TOMOKO_MAKE_MODEL_TEACHER_URL", DEFAULT_TEACHER_URL),
            model=os.environ.get("TOMOKO_MAKE_MODEL_TEACHER_MODEL", DEFAULT_TEACHER_MODEL),
            max_tokens=int(os.environ.get("TOMOKO_MAKE_MODEL_TEACHER_MAX_TOKENS", "16")),
            timeout_sec=float(os.environ.get("TOMOKO_MAKE_MODEL_TEACHER_TIMEOUT_SEC", "60.0")),
        )

    async def complete(self, prompt: str) -> str:
        timeout = httpx.Timeout(self.timeout_sec, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.url.rstrip('/')}/v1/chat/completions",
                json=self.payload(prompt),
            )
            response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content", "")).strip()

    def payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": SATURATION_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }


async def label_prefix_examples(
    examples: list[PrefixExample],
    *,
    teacher: TeacherBackend,
    config: TeacherConfig,
) -> list[TeacherLabel]:
    labels: list[TeacherLabel] = []
    for example in examples:
        raw_output = ""
        label_source = "teacher_llm"
        try:
            raw_output = await teacher.complete(saturation_prompt(example.prefix_text))
            result = parse_saturation_output(
                raw_output,
                basis_text=example.prefix_text,
                source="teacher_llm",
            )
        except Exception:
            if not config.fallback_on_error:
                raise
            result = deterministic_saturation(
                example.prefix_text,
                source="deterministic_fallback",
            )
            label_source = "deterministic_fallback"
        labels.append(
            TeacherLabel(
                utterance_id=example.utterance_id,
                prefix_index=example.prefix_index,
                prefix_text=example.prefix_text,
                full_text=example.full_text,
                saturation=result.saturation,
                teacher_model=config.source_model,
                source=example.source,
                conversation_id=example.conversation_id,
                turn_index=example.turn_index,
                is_final=example.is_final,
                label_source=label_source,
                raw_output=raw_output,
            )
        )
        if config.sleep_sec > 0:
            await asyncio.sleep(config.sleep_sec)
    return labels
