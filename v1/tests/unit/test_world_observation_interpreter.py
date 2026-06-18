from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from server.shared.models import WorldObservationItemRecord
from server.world_observations.interpreter import (
    PersonaSnapshotMaterial,
    WorldObservationInterpreter,
)
from server.world_observations.store import InMemoryWorldObservationStore


class FakeBackend:
    name = "fake-interpreter"

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        assert "background interpreter" in system_prompt
        assert "Tomoko profile" in system_prompt
        assert "ローカル推論" in system_prompt
        assert "一人のユーザー" in system_prompt
        assert "ニュース解説者ではありません" in system_prompt
        assert "base_persona.md" in system_prompt
        assert "性格の芯" in system_prompt
        assert "persona_basis" in system_prompt
        assert "short_now" in system_prompt
        assert "tomoko_private_reaction" in system_prompt
        assert "candidate_seed_text" in system_prompt
        assert "serialized_persona_snapshots" in system_prompt
        assert '"persona_state"' in system_prompt
        assert '"persona_lexicon"' in system_prompt
        assert "小型モデル" in messages[0]["content"]
        yield (
            '{"relevance_to_user":0.7,"tomoko_interest":0.8,'
            '"emotional_tone":"curious","memory_value":0.6,'
            '"speakability_hint":"short_now",'
            '"interpretation_text":"私は、端末の中で小さく賢く動ける話として少し身を乗り出したくなる。",'
            '"tomoko_private_reaction":"こういう小さく手元で動く話、私はかなり好きだな。",'
            '"candidate_seed_text":"ローカル推論の小型モデルの話、少しだけ気になるかも。",'
            '"reason_json":{'
            '"persona_basis":"ローカル推論と声での自然なやりとりに近い",'
            '"user_basis":"ユーザーのTomoko開発に直接関係する",'
            '"speakability_basis":"短くなら今の話題の種にできる",'
            '"avoid_overclaim":"sample由来なので断定しない"'
            "}}"
        )


class FakePersonaReader:
    async def fetch_latest_snapshots(self) -> PersonaSnapshotMaterial:
        return PersonaSnapshotMaterial(
            state_version_id=None,
            lexicon_version_id=None,
            state=None,
            lexicon=None,
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
    interpreter = WorldObservationInterpreter(
        store=store,
        backend=FakeBackend(),
        persona_reader=FakePersonaReader(),
    )

    result = await interpreter.interpret_once(limit=10)

    assert result.interpreted_count == 1
    assert store.interpretations[0].tomoko_interest == 0.8
    assert store.interpretations[0].topic == "ai"
    assert store.interpretations[0].speakability_hint == "short_now"
    assert "小さく手元" in store.interpretations[0].tomoko_private_reaction
    assert "少しだけ気になる" in store.interpretations[0].candidate_seed_text
    assert store.interpretations[0].reason_json["persona_basis"]
