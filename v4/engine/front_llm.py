"""手前の LLM。返事の文を速く作る。Web の枠組みには依存しない。

Gemma の thinking は、chat_template_kwargs の enable_thinking でしか止まらない（v1 の経験）。
"""

import json
from collections.abc import AsyncIterator

import httpx

from engine.dto import EngineConfig

_ENDS = "。！？!?\n"
_COMMAS = "、，,"


class PhraseSplitter:
    """届いた文字を、文の切れ目で句にする。短い断片を TTS に渡すと余計な音が出る（v1 の経験）。

    first_cut_min_chars を与えると、返事の最初の句だけ、最初の読点でも切る（声を早く出すため）。
    読点までの文字数がこの数に満たないときは切らない。
    """

    def __init__(self, first_cut_min_chars: int | None = None) -> None:
        self._buf = ""
        self._first_cut = first_cut_min_chars

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        start = 0
        for i, ch in enumerate(self._buf):
            early = (self._first_cut is not None and ch in _COMMAS
                     and i + 1 - start >= self._first_cut)
            if ch in _ENDS or early:
                phrase = self._buf[start : i + 1].strip()
                if phrase.strip(_ENDS):
                    out.append(phrase)
                    self._first_cut = None  # 最初の句が出たら、あとは文の切れ目だけ
                start = i + 1
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest.strip(_ENDS) else []


async def stream_reply(client: httpx.AsyncClient, cfg: EngineConfig,
                       messages: list[dict]) -> AsyncIterator[str]:
    body = {
        "model": cfg.llm_model, "stream": True, "max_tokens": cfg.llm_max_tokens,
        "messages": [{"role": "system", "content": cfg.system_prompt}, *messages],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with client.stream("POST", cfg.llm_url, json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
            if delta:
                yield delta
