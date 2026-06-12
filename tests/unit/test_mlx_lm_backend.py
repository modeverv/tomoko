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


@pytest.mark.unit
async def test_mlx_lm_backend_chat_stream_structured() -> None:
    prompts: list[str] = []

    def load_model(model_name: str, adapter_path: str | None = None):
        assert model_name == "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"
        assert adapter_path is None
        return object(), FakeTokenizer()

    def stream_generate(_model, _tokenizer, prompt: str, max_tokens: int):
        prompts.append(prompt)
        assert max_tokens == 180
        yield FakeResponse('{"semantic_saturation": 0.95, "remaining_info_risk": 0.2}')

    backend = MLXLMBackend(
        name="local_lfm",
        model="lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit",
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
    assert "あなたは発話判定アシスタントです。\n\n重要: あなたの出力は以下の JSON Schema に完全に準拠した JSON オブジェクトのみである必要があります。" in prompts[0]


# ============================================================
# Phase TT-v2.10c: prefill API（KVキャッシュ再利用）
# ============================================================


class FakeTokenizerWithEncode(FakeTokenizer):
    def encode(self, text: str) -> list[int]:
        return [ord(ch) for ch in text]


def _make_prefill_backend(stream_calls: list | None = None, trim_ok: bool = True):
    processed: list[list[int]] = []
    trims: list[int] = []

    def load_model(model_name: str, adapter_path: str | None = None):
        return object(), FakeTokenizerWithEncode()

    def stream_generate(_model, _tokenizer, prompt, max_tokens, prompt_cache=None):
        if stream_calls is not None:
            stream_calls.append({"prompt": prompt, "prompt_cache": prompt_cache})
        yield FakeResponse("OK")

    def cache_factory(_model):
        return {"id": len(processed)}  # 識別可能なダミーキャッシュ

    def prompt_processor(_model, _cache, tokens: list[int]) -> None:
        processed.append(list(tokens))

    def cache_trimmer(_cache, num_tokens: int) -> bool:
        trims.append(num_tokens)
        return trim_ok

    backend = MLXLMBackend(
        name="local_lfm",
        model="m",
        model_loader=load_model,
        stream_generator=stream_generate,
        prompt_cache_factory=cache_factory,
        prompt_processor=prompt_processor,
        cache_trimmer=cache_trimmer,
    )
    return backend, processed, trims


@pytest.mark.unit
async def test_prefill_processes_only_diff_tokens_on_extension() -> None:
    backend, processed, _trims = _make_prefill_backend()

    r1 = await backend.prefill("turn:1", "sys", [{"role": "user", "content": "こんにち"}])
    r2 = await backend.prefill("turn:1", "sys", [{"role": "user", "content": "こんにちは元気？"}])

    assert r1["new_tokens"] == r1["total_tokens"]
    # 2回目は差分トークンのみ処理される
    assert r2["new_tokens"] == r2["total_tokens"] - r1["total_tokens"]
    assert len(processed) == 2
    assert len(processed[1]) == r2["new_tokens"]


@pytest.mark.unit
async def test_chat_stream_uses_prefilled_cache_and_pops_it() -> None:
    stream_calls: list[dict] = []
    backend, _processed, _trims = _make_prefill_backend(stream_calls)

    sys_p = "sys"
    msgs = [{"role": "user", "content": "こんにちは"}]
    await backend.prefill("turn:1", sys_p, msgs)

    # prefill と完全一致するプロンプト → 最後の1トークンを巻き戻して入力にする
    async for _ in backend.chat_stream(sys_p, msgs, cache_key="turn:1"):
        pass

    assert stream_calls[0]["prompt_cache"] is not None
    assert isinstance(stream_calls[0]["prompt"], list)
    assert len(stream_calls[0]["prompt"]) >= 1
    # 使い切り: キャッシュは pop されている
    assert backend._prompt_caches == {}


@pytest.mark.unit
async def test_chat_stream_passes_suffix_tokens_when_prompt_extended() -> None:
    stream_calls: list[dict] = []
    backend, _processed, _trims = _make_prefill_backend(stream_calls)

    await backend.prefill("turn:1", "sys", [{"role": "user", "content": "こんにち"}])
    async for _ in backend.chat_stream(
        "sys", [{"role": "user", "content": "こんにちは元気？"}], cache_key="turn:1"
    ):
        pass

    suffix = stream_calls[0]["prompt"]
    assert isinstance(suffix, list)
    # 差分（"は元気？" 相当）のみ渡される
    assert 0 < len(suffix) < 20


@pytest.mark.unit
async def test_prefill_rebuilds_cache_when_trim_unsupported() -> None:
    backend, processed, trims = _make_prefill_backend(trim_ok=False)

    await backend.prefill("turn:1", "sys", [{"role": "user", "content": "こんにちは"}])
    # 分岐するプロンプト（前回の続きではない）→ trim 不可なので作り直し
    r2 = await backend.prefill("turn:1", "sys", [{"role": "user", "content": "さようなら"}])

    assert trims  # trim が試みられた
    assert r2["new_tokens"] == r2["total_tokens"]  # 全トークン再処理


@pytest.mark.unit
async def test_drop_prefill_discards_cache() -> None:
    backend, _processed, _trims = _make_prefill_backend()

    await backend.prefill("turn:1", "sys", [{"role": "user", "content": "こんにちは"}])
    assert "turn:1" in backend._prompt_caches

    await backend.drop_prefill("turn:1")
    assert backend._prompt_caches == {}
    # 二重 drop も安全
    await backend.drop_prefill("turn:1")


@pytest.mark.unit
async def test_chat_stream_without_cache_key_unaffected_by_existing_cache() -> None:
    stream_calls: list[dict] = []
    backend, _processed, _trims = _make_prefill_backend(stream_calls)

    await backend.prefill("turn:1", "sys", [{"role": "user", "content": "こんにちは"}])
    async for _ in backend.chat_stream("sys", [{"role": "user", "content": "こんにちは"}]):
        pass

    assert stream_calls[0]["prompt_cache"] is None
    assert isinstance(stream_calls[0]["prompt"], str)
    assert "turn:1" in backend._prompt_caches  # キャッシュは温存
