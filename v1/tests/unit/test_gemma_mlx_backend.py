from __future__ import annotations

import pytest

from server.shared.inference.backends.gemma_mlx import GemmaMLXBackend


class FakeTokenizer:
    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> str:
        assert tokenize is False
        assert add_generation_prompt is True
        return "\n".join(message["content"] for message in messages)


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.mark.unit
async def test_gemma_mlx_backend_streams_with_chat_template() -> None:
    prompts: list[str] = []

    def load_model(model_name: str):
        assert model_name == "fake-gemma"
        return object(), FakeTokenizer()

    def stream_generate(_model, _tokenizer, prompt: str, max_tokens: int):
        prompts.append(prompt)
        assert max_tokens == 180
        yield FakeResponse("EMOTION:happy\n")
        yield FakeResponse("聞こえるよ。")

    backend = GemmaMLXBackend(
        name="local_gemma",
        model="fake-gemma",
        model_loader=load_model,
        stream_generator=stream_generate,
    )

    chunks = [
        chunk
        async for chunk in backend.chat_stream(
            "あなたはトモコです。",
            [{"role": "user", "content": "トモコ、聞こえる？"}],
        )
    ]

    assert chunks == ["EMOTION:happy\n", "聞こえるよ。"]
    assert prompts == ["あなたはトモコです。\nトモコ、聞こえる？"]


@pytest.mark.unit
async def test_gemma_mlx_backend_chat_stream_structured() -> None:
    prompts: list[str] = []

    def load_model(model_name: str):
        assert model_name == "fake-gemma"
        return object(), FakeTokenizer()

    def stream_generate(_model, _tokenizer, prompt: str, max_tokens: int):
        prompts.append(prompt)
        assert max_tokens == 180
        yield FakeResponse('{"semantic_saturation": 0.95, "remaining_info_risk": 0.2}')

    backend = GemmaMLXBackend(
        name="local_gemma",
        model="fake-gemma",
        model_loader=load_model,
        stream_generator=stream_generate,
    )

    chunks = [
        chunk
        async for chunk in backend.chat_stream_structured(
            "あなたは発話判定アシスタントです。",
            [{"role": "user", "content": "てすとおわり"}],
            json_schema={
                "type": "object",
                "properties": {
                    "semantic_saturation": {"type": "number"},
                    "remaining_info_risk": {"type": "number"},
                },
                "required": ["semantic_saturation", "remaining_info_risk"],
            },
        )
    ]

    assert chunks == ['{"semantic_saturation": 0.95, "remaining_info_risk": 0.2}']
    # Verify that the schema instruction is appended to the system prompt (which fake tokenizer concatenates)
    assert "あなたは発話判定アシスタントです。\n\n重要: あなたの出力は以下の JSON Schema に完全に準拠した JSON オブジェクトのみである必要があります。" in prompts[0]

