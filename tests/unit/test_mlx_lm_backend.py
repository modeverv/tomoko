from __future__ import annotations

import pytest

from server.shared.inference.backends.mlx_lm import MLXLMBackend


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
async def test_mlx_lm_backend_streams_with_chat_template() -> None:
    prompts: list[str] = []

    def load_model(model_name: str, adapter_path: str | None = None):
        assert model_name == "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"
        assert adapter_path is None
        return object(), FakeTokenizer()

    def stream_generate(_model, _tokenizer, prompt: str, max_tokens: int):
        prompts.append(prompt)
        assert max_tokens == 180
        yield FakeResponse("EMOTION:gentle\n")
        yield FakeResponse("うん、聞こえてる。")

    backend = MLXLMBackend(
        name="local_lfm",
        model="lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit",
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

    assert chunks == ["EMOTION:gentle\n", "うん、聞こえてる。"]
    assert prompts == ["あなたはトモコです。\nトモコ、聞こえる？"]


@pytest.mark.unit
async def test_mlx_lm_backend_warm_up_uses_streaming_path() -> None:
    calls: list[str] = []

    def load_model(model_name: str, adapter_path: str | None = None):
        calls.append(model_name)
        assert adapter_path is None
        return object(), FakeTokenizer()

    def stream_generate(_model, _tokenizer, _prompt: str, _max_tokens: int):
        yield FakeResponse("はい。")

    backend = MLXLMBackend(
        name="local_lfm",
        model="lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit",
        model_loader=load_model,
        stream_generator=stream_generate,
    )

    await backend.warm_up()

    assert calls == ["lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"]


@pytest.mark.unit
async def test_mlx_lm_backend_loads_with_adapter() -> None:
    calls: list[tuple[str, str | None]] = []

    def load_model(model_name: str, adapter_path: str | None = None):
        calls.append((model_name, adapter_path))
        return object(), FakeTokenizer()

    def stream_generate(_model, _tokenizer, _prompt: str, _max_tokens: int):
        yield FakeResponse("はい。")

    backend = MLXLMBackend(
        name="local_lfm",
        model="lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit",
        adapter_path="lora/adapters",
        model_loader=load_model,
        stream_generator=stream_generate,
    )

    async for _ in backend.chat_stream(
        "あなたはトモコです。",
        [{"role": "user", "content": "聞こえる？"}],
    ):
        pass

    assert calls == [("lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit", "lora/adapters")]
