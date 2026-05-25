from __future__ import annotations

from uuid import uuid4

import pytest

from server.background.persona_updater import PersonaSnapshotUpdater
from server.shared.models import (
    LexiconTerm,
    PersonaDiffEntry,
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    PersonaVersionDiff,
)
from server.shared.persona import NullPersonaSnapshotStore
from server.shared.persona_prompt import (
    format_persona_prompt_slice_for_prompt,
    format_persona_snapshots_for_prompt,
)


@pytest.mark.unit
def test_persona_lexicon_snapshot_round_trips_json() -> None:
    snapshot = PersonaLexiconSnapshot.from_json(
        {
            "schema_version": 1,
            "user_terms": [
                {
                    "term": "カレーの話",
                    "meaning": "前に作ったカレーの経過や味の話題",
                    "tone": "親しみ",
                    "salience": 0.82,
                    "first_seen_session_id": str(uuid4()),
                    "last_seen_session_id": str(uuid4()),
                    "evidence": ["昨日カレーを作ったよ"],
                }
            ],
            "tomoko_phrases": [
                {
                    "phrase": "それ、ちょっと覚えておきたい",
                    "usage": "相手のこだわりや感情が出た時",
                    "salience": 0.74,
                    "evidence_session_id": str(uuid4()),
                }
            ],
            "relationship_markers": [
                {
                    "marker": "さっきの続き",
                    "meaning": "同一会話セッション内の継続話題",
                    "salience": 0.7,
                }
            ],
            "corrections": [
                {
                    "wrong": "以前の仮理解",
                    "correct": "訂正後の理解",
                    "source_session_id": str(uuid4()),
                }
            ],
        }
    )

    payload = snapshot.to_json()
    reloaded = PersonaLexiconSnapshot.from_json(payload)

    assert reloaded == snapshot
    assert payload["user_terms"][0]["term"] == "カレーの話"


@pytest.mark.unit
def test_persona_state_snapshot_loader_accepts_legacy_missing_sections() -> None:
    snapshot = PersonaStateSnapshot.from_json(
        {
            "schema_version": 1,
            "traits": {"warmth": 0.72, "playfulness": 0.48},
            "relationship": {"familiarity": 0.61},
            "speaking_style": {"sentence_length": "short"},
        }
    )

    assert snapshot.schema_version == 1
    assert snapshot.relationship.preferred_address is None
    assert snapshot.speaking_style.signature_phrases == []
    assert snapshot.open_threads == []
    assert snapshot.to_json()["traits"]["warmth"] == 0.72


@pytest.mark.unit
def test_persona_snapshot_loader_rejects_future_schema() -> None:
    with pytest.raises(ValueError, match="Unsupported persona lexicon schema_version"):
        PersonaLexiconSnapshot.from_json({"schema_version": 999})


@pytest.mark.unit
def test_persona_version_diff_tracks_added_updated_deprecated() -> None:
    diff = PersonaVersionDiff(
        added=[
            PersonaDiffEntry(
                path="$.user_terms",
                reason="会話内で繰り返し参照された",
                value={"term": "カレーの話"},
            )
        ],
        updated=[
            PersonaDiffEntry(
                path="$.relationship.familiarity",
                reason="継続会話が自然に成立した",
                from_value=0.58,
                to_value=0.61,
            )
        ],
        deprecated=[
            PersonaDiffEntry(path="$.corrections[0]", reason="新しい訂正で置き換え")
        ],
    )

    reloaded = PersonaVersionDiff.from_json(diff.to_json())

    assert reloaded == diff
    assert reloaded.added[0].value == {"term": "カレーの話"}
    assert reloaded.updated[0].from_value == 0.58
    assert reloaded.deprecated[0].path == "$.corrections[0]"


@pytest.mark.unit
def test_persona_prompt_uses_subset_not_full_snapshot() -> None:
    lexicon = PersonaLexiconSnapshot.from_json(
        {
            "schema_version": 1,
            "user_terms": [
                {
                    "term": "カレーの話",
                    "meaning": "材料と買い物の話題",
                    "salience": 0.8,
                },
                {
                    "term": "散歩",
                    "meaning": "朝の散歩の習慣",
                    "salience": 0.9,
                },
            ],
        }
    )
    state = PersonaStateSnapshot.from_json(
        {
            "schema_version": 1,
            "traits": {"warmth": 0.73},
            "relationship": {
                "familiarity": 0.63,
                "preferred_address": "トモコ",
                "boundaries": ["静かにしてを尊重する"],
            },
            "speaking_style": {
                "sentence_length": "short",
                "honorific_level": "casual_polite",
                "signature_phrases": ["うん"],
            },
            "open_threads": [{"topic": "カレー", "status": "watch"}],
        }
    )

    terms = lexicon.select_terms_for_prompt(query="カレーの話の続き", limit=1)
    persona_slice = state.to_prompt_slice()

    assert terms[0].term == "カレーの話"
    assert len(terms) == 1
    assert persona_slice.signature_phrases == ["うん"]
    assert not hasattr(persona_slice, "open_threads")


@pytest.mark.unit
def test_persona_snapshot_prompt_serializes_empty_fallback() -> None:
    prompt = format_persona_snapshots_for_prompt(state=None, lexicon=None)

    assert "base persona を上書きしません" in prompt
    assert "serialized_persona_snapshots" in prompt
    assert '"persona_state"' in prompt
    assert '"traits": {}' in prompt
    assert '"persona_lexicon"' in prompt
    assert '"user_terms": []' in prompt


@pytest.mark.unit
def test_persona_prompt_slice_serializes_empty_and_terms() -> None:
    prompt = format_persona_prompt_slice_for_prompt(
        persona_slice=None,
        lexicon_terms=[
            LexiconTerm(
                term="ローカル推論",
                meaning="Tomoko の重要な関心領域",
                salience=0.8,
            )
        ],
    )

    assert "serialized_persona_prompt_slice" in prompt
    assert '"persona_state_slice"' in prompt
    assert '"signature_phrases": []' in prompt
    assert '"term": "ローカル推論"' in prompt


@pytest.mark.unit
async def test_persona_snapshot_updater_writes_versions_from_session_summary() -> None:
    session_id = uuid4()
    store = InMemoryPersonaSnapshotStore(session_id=session_id)
    extractor = FakePersonaExtractor()
    updater = PersonaSnapshotUpdater(store=store, extractor=extractor)

    processed = await updater.process_completed_sessions(limit=1)

    assert processed == 1
    assert extractor.inputs == [
        (
            "カレーの材料と買い物について話した。",
            [],
            None,
            None,
        )
    ]
    assert store.lexicon_versions[0][2].user_terms[0].term == "カレーの話"
    assert store.state_versions[0][2].relationship.familiarity == 0.63


@pytest.mark.unit
async def test_null_persona_snapshot_store_keeps_background_boundary_noop() -> None:
    store = NullPersonaSnapshotStore()

    assert await store.find_completed_sessions_without_persona_versions(limit=10) == []
    assert await store.read_session_material(session_id=uuid4()) is None
    assert await store.read_latest_lexicon() is None
    assert await store.read_latest_state() is None


class FakePersonaExtractor:
    model = "fake_persona_extractor"

    def __init__(self) -> None:
        self.inputs: list[tuple[str, list[str], object, object]] = []

    async def extract(
        self,
        *,
        summary_text: str,
        raw_turns: list[str],
        previous_lexicon: PersonaLexiconSnapshot | None,
        previous_state: PersonaStateSnapshot | None,
    ):
        self.inputs.append(
            (summary_text, raw_turns, previous_lexicon, previous_state)
        )
        return (
            PersonaLexiconSnapshot.from_json(
                {
                    "schema_version": 1,
                    "user_terms": [
                        {
                            "term": "カレーの話",
                            "meaning": "材料と買い物の話題",
                            "salience": 0.8,
                            "evidence": [summary_text],
                        }
                    ],
                }
            ),
            PersonaVersionDiff.from_json(
                {
                    "schema_version": 1,
                    "added": [
                        {
                            "path": "$.user_terms",
                            "value": {"term": "カレーの話"},
                            "reason": "session summary に残った",
                        }
                    ],
                }
            ),
            PersonaStateSnapshot.from_json(
                {
                    "schema_version": 1,
                    "traits": {"warmth": 0.73},
                    "relationship": {"familiarity": 0.63},
                    "speaking_style": {"sentence_length": "short"},
                }
            ),
            PersonaVersionDiff.from_json(
                {
                    "schema_version": 1,
                    "updated": [
                        {
                            "path": "$.relationship.familiarity",
                            "from": 0.61,
                            "to": 0.63,
                            "reason": "会話が自然に継続した",
                        }
                    ],
                }
            ),
        )


class InMemoryPersonaSnapshotStore:
    def __init__(self, *, session_id) -> None:
        self.session_id = session_id
        self.lexicon_versions = []
        self.state_versions = []

    async def find_completed_sessions_without_persona_versions(self, *, limit: int):
        assert limit == 1
        return [self.session_id]

    async def read_session_material(self, *, session_id):
        assert session_id == self.session_id
        return ("カレーの材料と買い物について話した。", [])

    async def read_latest_lexicon(self):
        return None

    async def read_latest_state(self):
        return None

    async def write_lexicon_version(
        self,
        *,
        source_session_id,
        reason,
        snapshot,
        diff,
        model,
        status="completed",
    ):
        self.lexicon_versions.append((source_session_id, reason, snapshot, diff, model, status))
        return uuid4()

    async def write_state_version(
        self,
        *,
        source_session_id,
        reason,
        snapshot,
        diff,
        model,
        status="completed",
    ):
        self.state_versions.append((source_session_id, reason, snapshot, diff, model, status))
        return uuid4()
