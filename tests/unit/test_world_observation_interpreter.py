from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from server.shared.models import WorldObservationItemRecord
from server.world_observations.interpreter import WorldObservationInterpreter
from server.world_observations.store import InMemoryWorldObservationStore


class FakeBackend:
    name = "fake-interpreter"

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        assert "background interpreter" in system_prompt
        assert "小型モデル" in messages[0]["content"]
        yield (
            '{"relevance_to_user":0.7,"tomoko_interest":0.8,'
            '"emotional_tone":"curious","memory_value":0.6,'
            '"speakability_hint":"短くなら話題にできる",'
            '"interpretation_text":"ローカル推論の話は少し気になる。",'
            '"reason_json":{"reason":"Tomokoの設計に近い"}}'
        )


@pytest.mark.unit
async def test_interpreter_saves_item_interpretation() -> None:
    store = InMemoryWorldObservationStore()
    item = WorldObservationItemRecord(
        id=uuid4(),
        document_id=uuid4(),
        topic="ai",
        title="小型モデル",
        summary="端末内推論",
        source_hint="sample",
        freshness="fresh",
        confidence=0.8,
        item_json={},
        raw_excerpt="端末内推論",
        created_at=datetime.now(UTC),
    )
    store.items.append(item)
    interpreter = WorldObservationInterpreter(store=store, backend=FakeBackend())

    result = await interpreter.interpret_once(limit=10)

    assert result.interpreted_count == 1
    assert store.interpretations[0].tomoko_interest == 0.8
    assert store.interpretations[0].topic == "ai"
