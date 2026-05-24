from __future__ import annotations

import json
from typing import Protocol

from server.shared.inference.router import InferenceRouter
from server.shared.models import (
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
あなたは Tomoko の人格・用語集 snapshot を更新する background worker です。
会話セッション要約と必要な原文ターンだけを材料にし、推測で事実を足さないでください。

JSON だけを返してください。形は次の通りです。
{
  "lexicon_json": {
    "schema_version": 1,
    "user_terms": [],
    "tomoko_phrases": [],
    "relationship_markers": [],
    "corrections": []
  },
  "lexicon_diff_json": {
    "schema_version": 1,
    "added": [],
    "updated": [],
    "deprecated": []
  },
  "state_json": {
    "schema_version": 1,
    "traits": {},
    "relationship": {},
    "speaking_style": {},
    "open_threads": []
  },
  "state_diff_json": {
    "schema_version": 1,
    "added": [],
    "updated": [],
    "deprecated": []
  }
}
"""


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
        backend = await self.router.select("session_summary", "privacy")
        self.model = backend.name
        chunks: list[str] = []
        async for chunk in backend.chat_stream(
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
        ):
            chunks.append(chunk)
        payload = _load_json_object("".join(chunks))
        return (
            PersonaLexiconSnapshot.from_json(payload.get("lexicon_json", {})),
            PersonaVersionDiff.from_json(payload.get("lexicon_diff_json", {})),
            PersonaStateSnapshot.from_json(payload.get("state_json", {})),
            PersonaVersionDiff.from_json(payload.get("state_diff_json", {})),
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
            "previous_lexicon": (
                previous_lexicon.to_json() if previous_lexicon is not None else None
            ),
            "previous_state": (
                previous_state.to_json() if previous_state is not None else None
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
