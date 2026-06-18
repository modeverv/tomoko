from __future__ import annotations

import json
from typing import Any

from server.shared.models import (
    LexiconTerm,
    PersonaLexiconSnapshot,
    PersonaPromptSlice,
    PersonaStateSnapshot,
)

EMPTY_PERSONA_STATE_JSON: dict[str, Any] = {
    "schema_version": 1,
    "traits": {},
    "relationship": {
        "familiarity": 0.0,
        "boundaries": [],
    },
    "speaking_style": {
        "signature_phrases": [],
    },
    "open_threads": [],
}

EMPTY_PERSONA_LEXICON_JSON: dict[str, Any] = {
    "schema_version": 1,
    "user_terms": [],
    "tomoko_phrases": [],
    "relationship_markers": [],
    "corrections": [],
}

PERSONA_SNAPSHOT_RULES = """\
人格 snapshot の扱い:
- base persona は Tomoko の固定された core persona です。
- snapshot は会話から得た派生状態であり、base persona を上書きしません。
- snapshot が空の場合は、まだ学習済みの人格・用語がないという意味です。
- snapshot にない事実を推測で足さないでください。
- snapshot は必要な時だけ自然に反映し、会話や解釈を硬くしすぎないでください。
"""


def format_persona_snapshots_for_prompt(
    *,
    state: PersonaStateSnapshot | None,
    lexicon: PersonaLexiconSnapshot | None,
) -> str:
    return "\n".join(
        [
            PERSONA_SNAPSHOT_RULES,
            "serialized_persona_snapshots:",
            json.dumps(
                {
                    "persona_state": (
                        state.to_json() if state is not None else EMPTY_PERSONA_STATE_JSON
                    ),
                    "persona_lexicon": (
                        lexicon.to_json()
                        if lexicon is not None
                        else EMPTY_PERSONA_LEXICON_JSON
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
        ]
    )


def format_persona_prompt_slice_for_prompt(
    *,
    persona_slice: PersonaPromptSlice | None,
    lexicon_terms: list[LexiconTerm],
) -> str:
    return "\n".join(
        [
            PERSONA_SNAPSHOT_RULES,
            "serialized_persona_prompt_slice:",
            json.dumps(
                {
                    "persona_state_slice": _persona_slice_to_json(persona_slice),
                    "lexicon_terms": [
                        _lexicon_term_to_json(term) for term in lexicon_terms
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
        ]
    )


def _persona_slice_to_json(
    persona_slice: PersonaPromptSlice | None,
) -> dict[str, Any]:
    if persona_slice is None:
        return {
            "traits": {},
            "relationship_familiarity": 0.0,
            "preferred_address": None,
            "sentence_length": None,
            "honorific_level": None,
            "signature_phrases": [],
        }
    return {
        "traits": dict(persona_slice.traits),
        "relationship_familiarity": persona_slice.relationship_familiarity,
        "preferred_address": persona_slice.preferred_address,
        "sentence_length": persona_slice.sentence_length,
        "honorific_level": persona_slice.honorific_level,
        "signature_phrases": list(persona_slice.signature_phrases),
    }


def _lexicon_term_to_json(term: LexiconTerm) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "term": term.term,
        "meaning": term.meaning,
        "salience": term.salience,
    }
    if term.tone is not None:
        payload["tone"] = term.tone
    return payload
