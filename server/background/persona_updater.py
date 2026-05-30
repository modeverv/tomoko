from __future__ import annotations

import json
from typing import Any, Protocol

from server.shared.inference.router import InferenceRouter
from server.shared.inference.trace import chat_stream_structured_with_trace_role
from server.shared.models import (
    PersonaDiffEntry,
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    PersonaVersionDiff,
)
from server.shared.persona import PersonaSnapshotStore


class PersonaSnapshotExtractor(Protocol):
    model: str

    async def extract(
        self,
        *,
        summary_text: str,
        raw_turns: list[str],
        previous_lexicon: PersonaLexiconSnapshot | None,
        previous_state: PersonaStateSnapshot | None,
    ) -> tuple[
        PersonaLexiconSnapshot,
        PersonaVersionDiff,
        PersonaStateSnapshot,
        PersonaVersionDiff,
    ]: ...


class PersonaSnapshotUpdater:
    def __init__(
        self,
        *,
        store: PersonaSnapshotStore,
        extractor: PersonaSnapshotExtractor,
    ) -> None:
        self.store = store
        self.extractor = extractor

    async def process_completed_sessions(self, *, limit: int = 10) -> int:
        session_ids = await self.store.find_completed_sessions_without_persona_versions(
            limit=limit
        )
        processed = 0
        for session_id in session_ids:
            material = await self.store.read_session_material(session_id=session_id)
            if material is None:
                continue
            summary_text, raw_turns = material
            previous_lexicon = await self.store.read_latest_lexicon()
            previous_state = await self.store.read_latest_state()
            (
                lexicon_snapshot,
                lexicon_diff,
                state_snapshot,
                state_diff,
            ) = await self.extractor.extract(
                summary_text=summary_text,
                raw_turns=raw_turns,
                previous_lexicon=previous_lexicon,
                previous_state=previous_state,
            )
            await self.store.write_lexicon_version(
                source_session_id=session_id,
                reason="session_summary_completed",
                snapshot=lexicon_snapshot,
                diff=lexicon_diff,
                model=self.extractor.model,
            )
            await self.store.write_state_version(
                source_session_id=session_id,
                reason="session_summary_completed",
                snapshot=state_snapshot,
                diff=state_diff,
                model=self.extractor.model,
            )
            processed += 1
        return processed


PERSONA_EXTRACTOR_SYSTEM_PROMPT = """\
あなたは Tomoko の人格・用語集 snapshot に対する変更提案だけを返す background worker です。
会話セッション要約、必要な原文ターン、compact previous snapshot だけを材料にしてください。
推測で事実を足さないでください。
1 回の更新で返す変更は、added / updated / deprecated それぞれ最大 6 件に絞ってください。
判断に迷う項目は返さず、確度が高い変更だけを返してください。

JSON だけを返してください。形は次の通りです。
{
  "lexicon_diff_json": {
    "schema_version": 1,
    "added": [],
    "updated": [],
    "deprecated": []
  },
  "state_diff_json": {
    "schema_version": 1,
    "added": [],
    "updated": [],
    "deprecated": []
  }
}
"""
PERSONA_UPDATE_MAX_TOKENS = 4096

MAX_COMPACT_USER_TERMS = 12
MAX_COMPACT_TOMOKO_PHRASES = 6
MAX_COMPACT_RELATIONSHIP_MARKERS = 6
MAX_COMPACT_CORRECTIONS = 6
MAX_COMPACT_SIGNATURE_PHRASES = 10
MAX_COMPACT_OPEN_THREADS = 6
MIN_COMPACT_SALIENCE = 0.05
MAX_DIFF_ITEMS_PER_KIND = 6

MAX_SNAPSHOT_USER_TERMS = 24
MAX_SNAPSHOT_TOMOKO_PHRASES = 12
MAX_SNAPSHOT_RELATIONSHIP_MARKERS = 12
MAX_SNAPSHOT_CORRECTIONS = 12
MAX_SNAPSHOT_BOUNDARIES = 12
MAX_SNAPSHOT_SIGNATURE_PHRASES = 12
MAX_SNAPSHOT_OPEN_THREADS = 12
MAX_EVIDENCE_ITEMS = 4


class LLMPersonaSnapshotExtractor:
    def __init__(self, *, router: InferenceRouter) -> None:
        self.router = router
        self.model = "unknown"

    async def extract(
        self,
        *,
        summary_text: str,
        raw_turns: list[str],
        previous_lexicon: PersonaLexiconSnapshot | None,
        previous_state: PersonaStateSnapshot | None,
    ) -> tuple[
        PersonaLexiconSnapshot,
        PersonaVersionDiff,
        PersonaStateSnapshot,
        PersonaVersionDiff,
    ]:
        backend = await self.router.select("persona_update", "privacy")
        if not hasattr(backend, "chat_stream_structured"):
            raise RuntimeError(
                f"persona_update backend does not support structured output: {backend.name}"
            )
        self.model = backend.name
        chunks: list[str] = []
        async for chunk in chat_stream_structured_with_trace_role(
            backend,
            PERSONA_EXTRACTOR_SYSTEM_PROMPT,
            [
                {
                    "role": "user",
                    "content": _format_persona_extraction_input(
                        summary_text=summary_text,
                        raw_turns=raw_turns,
                        previous_lexicon=previous_lexicon,
                        previous_state=previous_state,
                    ),
                }
            ],
            json_schema=_persona_update_schema(),
            max_tokens=PERSONA_UPDATE_MAX_TOKENS,
            trace_role="persona_update",
        ):
            chunks.append(chunk)
        payload = _load_json_object("".join(chunks))
        lexicon_diff = PersonaVersionDiff.from_json(
            payload.get("lexicon_diff_json", {})
        )
        state_diff = PersonaVersionDiff.from_json(payload.get("state_diff_json", {}))
        return (
            _merge_lexicon_diff(previous_lexicon, lexicon_diff),
            lexicon_diff,
            _merge_state_diff(previous_state, state_diff),
            state_diff,
        )


def _format_persona_extraction_input(
    *,
    summary_text: str,
    raw_turns: list[str],
    previous_lexicon: PersonaLexiconSnapshot | None,
    previous_state: PersonaStateSnapshot | None,
) -> str:
    return json.dumps(
        {
            "session_summary": summary_text,
            "raw_turns": raw_turns,
            "previous_compact": _compact_previous_snapshot(
                previous_lexicon=previous_lexicon,
                previous_state=previous_state,
            ),
            "update_contract": (
                "Return only lexicon_diff_json and state_diff_json. "
                "Do not return full snapshots. Snapshot merge and pruning are done "
                "by deterministic application code."
            ),
        },
        ensure_ascii=False,
    )


def _load_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    return json.loads(stripped)


def _persona_update_schema() -> dict[str, Any]:
    diff_entry_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "reason": {"type": "string"},
            "value": {},
            "from": {},
            "to": {},
        },
        "additionalProperties": False,
    }
    diff_schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "added": {
                "type": "array",
                "items": diff_entry_schema,
                "maxItems": MAX_DIFF_ITEMS_PER_KIND,
            },
            "updated": {
                "type": "array",
                "items": diff_entry_schema,
                "maxItems": MAX_DIFF_ITEMS_PER_KIND,
            },
            "deprecated": {
                "type": "array",
                "items": diff_entry_schema,
                "maxItems": MAX_DIFF_ITEMS_PER_KIND,
            },
        },
        "required": ["schema_version", "added", "updated", "deprecated"],
        "additionalProperties": False,
    }
    return {
        "name": "persona_snapshot_update",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "lexicon_diff_json": diff_schema,
                "state_diff_json": diff_schema,
            },
            "required": ["lexicon_diff_json", "state_diff_json"],
            "additionalProperties": False,
        },
    }


def _json_object_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
    }


def _compact_previous_snapshot(
    *,
    previous_lexicon: PersonaLexiconSnapshot | None,
    previous_state: PersonaStateSnapshot | None,
) -> dict[str, Any]:
    return {
        "lexicon": (
            _compact_lexicon(previous_lexicon)
            if previous_lexicon is not None
            else None
        ),
        "state": _compact_state(previous_state) if previous_state is not None else None,
        "limits": {
            "user_terms": MAX_SNAPSHOT_USER_TERMS,
            "tomoko_phrases": MAX_SNAPSHOT_TOMOKO_PHRASES,
            "relationship_markers": MAX_SNAPSHOT_RELATIONSHIP_MARKERS,
            "corrections": MAX_SNAPSHOT_CORRECTIONS,
            "open_threads": MAX_SNAPSHOT_OPEN_THREADS,
        },
    }


def _compact_lexicon(snapshot: PersonaLexiconSnapshot) -> dict[str, Any]:
    user_terms = [
        item for item in _sort_by_salience(snapshot.user_terms)
        if item.salience >= MIN_COMPACT_SALIENCE
    ]
    tomoko_phrases = [
        item for item in _sort_by_salience(snapshot.tomoko_phrases)
        if item.salience >= MIN_COMPACT_SALIENCE
    ]
    relationship_markers = [
        item for item in _sort_by_salience(snapshot.relationship_markers)
        if item.salience >= MIN_COMPACT_SALIENCE
    ]
    return {
        "schema_version": 1,
        "user_terms": [
            _pick_keys(item.to_json(), ("term", "meaning", "tone", "salience"))
            for item in user_terms[:MAX_COMPACT_USER_TERMS]
        ],
        "tomoko_phrases": [
            _pick_keys(item.to_json(), ("phrase", "usage", "salience"))
            for item in tomoko_phrases[:MAX_COMPACT_TOMOKO_PHRASES]
        ],
        "relationship_markers": [
            _pick_keys(item.to_json(), ("marker", "meaning", "salience"))
            for item in relationship_markers[:MAX_COMPACT_RELATIONSHIP_MARKERS]
        ],
        "corrections": [
            _pick_keys(item.to_json(), ("wrong", "correct"))
            for item in snapshot.corrections[:MAX_COMPACT_CORRECTIONS]
        ],
    }


def _compact_state(snapshot: PersonaStateSnapshot) -> dict[str, Any]:
    payload = snapshot.to_json()
    relationship = dict(payload["relationship"])
    relationship["boundaries"] = relationship.get("boundaries", [])[
        :MAX_COMPACT_SIGNATURE_PHRASES
    ]
    speaking_style = dict(payload["speaking_style"])
    speaking_style["signature_phrases"] = speaking_style.get(
        "signature_phrases", []
    )[:MAX_COMPACT_SIGNATURE_PHRASES]
    return {
        "schema_version": 1,
        "traits": payload["traits"],
        "relationship": relationship,
        "speaking_style": speaking_style,
        "open_threads": payload["open_threads"][:MAX_COMPACT_OPEN_THREADS],
    }


def _merge_lexicon_diff(
    previous: PersonaLexiconSnapshot | None, diff: PersonaVersionDiff
) -> PersonaLexiconSnapshot:
    payload = (
        previous.to_json()
        if previous is not None
        else PersonaLexiconSnapshot().to_json()
    )
    for entry in [*diff.added, *diff.updated]:
        value = _entry_update_value(entry)
        if not isinstance(value, dict):
            continue
        _upsert_lexicon_value(payload, entry.path, value)
    for entry in diff.deprecated:
        value = entry.value if isinstance(entry.value, dict) else {}
        _remove_lexicon_value(payload, entry.path, value)
    return PersonaLexiconSnapshot.from_json(_prune_lexicon_payload(payload))


def _merge_state_diff(
    previous: PersonaStateSnapshot | None, diff: PersonaVersionDiff
) -> PersonaStateSnapshot:
    payload = (
        previous.to_json() if previous is not None else PersonaStateSnapshot().to_json()
    )
    for entry in [*diff.added, *diff.updated]:
        _apply_state_update(payload, entry)
    for entry in diff.deprecated:
        value = entry.value if isinstance(entry.value, dict) else {}
        _remove_state_value(payload, entry.path, value)
    return PersonaStateSnapshot.from_json(_prune_state_payload(payload))


def _entry_update_value(entry: PersonaDiffEntry) -> Any:
    return entry.to_value if entry.to_value is not None else entry.value


def _upsert_lexicon_value(
    payload: dict[str, Any], path: str, value: dict[str, Any]
) -> None:
    if "tomoko_phrases" in path or "phrase" in value:
        _upsert_by_key(payload["tomoko_phrases"], value, "phrase")
    elif "relationship_markers" in path or "marker" in value:
        _upsert_by_key(payload["relationship_markers"], value, "marker")
    elif "corrections" in path or "wrong" in value:
        _upsert_by_key(payload["corrections"], value, "wrong")
    else:
        _upsert_by_key(payload["user_terms"], value, "term")


def _remove_lexicon_value(
    payload: dict[str, Any], path: str, value: dict[str, Any]
) -> None:
    if "tomoko_phrases" in path:
        _remove_by_key(payload["tomoko_phrases"], "phrase", value.get("phrase"))
    elif "relationship_markers" in path:
        _remove_by_key(
            payload["relationship_markers"], "marker", value.get("marker")
        )
    elif "corrections" in path:
        _remove_by_key(payload["corrections"], "wrong", value.get("wrong"))
    else:
        _remove_by_key(payload["user_terms"], "term", value.get("term"))


def _apply_state_update(payload: dict[str, Any], entry: PersonaDiffEntry) -> None:
    value = _entry_update_value(entry)
    path = entry.path
    if path.startswith("$.traits."):
        trait = path.removeprefix("$.traits.")
        if isinstance(value, int | float):
            payload["traits"][trait] = _clamp01(float(value))
        return
    if path.startswith("$.relationship."):
        field = path.removeprefix("$.relationship.")
        if field == "familiarity" and isinstance(value, int | float):
            payload["relationship"]["familiarity"] = _clamp01(float(value))
        elif value is not None:
            payload["relationship"][field] = value
        return
    if path.startswith("$.speaking_style."):
        field = path.removeprefix("$.speaking_style.")
        if value is not None:
            payload["speaking_style"][field] = value
        return
    if "open_threads" in path and isinstance(value, dict):
        _upsert_by_key(payload["open_threads"], value, "topic")
        return
    if path == "$.traits" and isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, int | float):
                payload["traits"][str(key)] = _clamp01(float(item))
        return
    if path == "$.relationship" and isinstance(value, dict):
        payload["relationship"].update(value)
        return
    if path == "$.speaking_style" and isinstance(value, dict):
        payload["speaking_style"].update(value)


def _remove_state_value(
    payload: dict[str, Any], path: str, value: dict[str, Any]
) -> None:
    if path.startswith("$.traits."):
        payload["traits"].pop(path.removeprefix("$.traits."), None)
    elif "open_threads" in path:
        _remove_by_key(payload["open_threads"], "topic", value.get("topic"))


def _prune_lexicon_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload["user_terms"] = [
        _with_limited_evidence(item)
        for item in _sort_json_by_salience(payload["user_terms"])[
            :MAX_SNAPSHOT_USER_TERMS
        ]
    ]
    payload["tomoko_phrases"] = _sort_json_by_salience(payload["tomoko_phrases"])[
        :MAX_SNAPSHOT_TOMOKO_PHRASES
    ]
    payload["relationship_markers"] = _sort_json_by_salience(
        payload["relationship_markers"]
    )[:MAX_SNAPSHOT_RELATIONSHIP_MARKERS]
    payload["corrections"] = payload["corrections"][:MAX_SNAPSHOT_CORRECTIONS]
    return payload


def _prune_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload["traits"] = {
        key: _clamp01(float(value))
        for key, value in payload.get("traits", {}).items()
        if isinstance(value, int | float)
    }
    relationship = payload.setdefault("relationship", {})
    if "familiarity" in relationship and isinstance(
        relationship["familiarity"], int | float
    ):
        relationship["familiarity"] = _clamp01(float(relationship["familiarity"]))
    relationship["boundaries"] = relationship.get("boundaries", [])[
        :MAX_SNAPSHOT_BOUNDARIES
    ]
    speaking_style = payload.setdefault("speaking_style", {})
    speaking_style["signature_phrases"] = speaking_style.get(
        "signature_phrases", []
    )[:MAX_SNAPSHOT_SIGNATURE_PHRASES]
    payload["open_threads"] = payload.get("open_threads", [])[:MAX_SNAPSHOT_OPEN_THREADS]
    return payload


def _sort_by_salience(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda item: item.salience, reverse=True)


def _sort_json_by_salience(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: float(item.get("salience", 0.0)), reverse=True)


def _pick_keys(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def _upsert_by_key(items: list[dict[str, Any]], value: dict[str, Any], key: str) -> None:
    identity = value.get(key)
    if not identity:
        return
    for index, item in enumerate(items):
        if item.get(key) == identity:
            merged = dict(item)
            merged.update(value)
            items[index] = merged
            return
    items.append(value)


def _remove_by_key(items: list[dict[str, Any]], key: str, identity: Any) -> None:
    if not identity:
        return
    items[:] = [item for item in items if item.get(key) != identity]


def _with_limited_evidence(item: dict[str, Any]) -> dict[str, Any]:
    if "evidence" in item:
        item = dict(item)
        item["evidence"] = item.get("evidence", [])[:MAX_EVIDENCE_ITEMS]
    return item


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
