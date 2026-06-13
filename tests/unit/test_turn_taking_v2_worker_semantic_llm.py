from __future__ import annotations

import pytest

from server.background.turn_taking_v2_worker import (
    _compact_semantic_user_prompt,
    _parse_compact_semantic_response,
    _run_compact_semantic_finish_judge,
)


class FakeCompactBackend:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.chat_stream_calls: list[dict[str, object]] = []
        self.chat_stream_structured_called = False

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        trace_role: str | None = None,
    ):
        self.chat_stream_calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "max_tokens": max_tokens,
                "trace_role": trace_role,
            }
        )
        for chunk in self.chunks:
            yield chunk

    async def chat_stream_structured(self, *_args, **_kwargs):
        self.chat_stream_structured_called = True
        raise AssertionError("shadow worker semantic lane must not use structured stream")


@pytest.mark.unit
async def test_compact_semantic_judge_uses_plain_chat_stream() -> None:
    backend = FakeCompactBackend(
        ['{"semantic_saturation": 0.8, "remaining_info_risk": 0.2}']
    )

    result = await _run_compact_semantic_finish_judge(
        backend,
        "それで大丈夫です。",
    )

    assert backend.chat_stream_structured_called is False
    assert len(backend.chat_stream_calls) == 1
    call = backend.chat_stream_calls[0]
    assert call["max_tokens"] == 48
    assert call["trace_role"] == "turn_taking_v2"
    user_prompt = str(call["messages"][0]["content"])
    assert "JSONキー" not in user_prompt
    assert "strict" not in user_prompt
    assert result["semantic_saturation"] == 0.8
    assert result["remaining_info_risk"] == 0.2
    assert result["safe_response_level"] == 4
    assert result["semantic_split_risk"] == 0.0


@pytest.mark.unit
def test_compact_semantic_parser_requires_strict_two_key_shape() -> None:
    assert _parse_compact_semantic_response(
        '```json\n{"semantic_saturation": 0.3, "remaining_info_risk": 0.7}\n```'
    ) == (0.3, 0.7)

    with pytest.raises(ValueError, match="strict 2-key"):
        _parse_compact_semantic_response(
            '{"semantic_saturation": 0.3, "remaining_info_risk": 0.7, "note": "x"}'
        )

    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        _parse_compact_semantic_response(
            '{"semantic_saturation": 1.3, "remaining_info_risk": 0.7}'
        )


@pytest.mark.unit
def test_compact_semantic_user_prompt_escapes_raw_text_as_json_string() -> None:
    prompt = _compact_semantic_user_prompt('彼が "まだ" って言ってた')

    assert '発話: "彼が \\"まだ\\" って言ってた"' in prompt
    assert "値入りJSONだけ" in prompt
