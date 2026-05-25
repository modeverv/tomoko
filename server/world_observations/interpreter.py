from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import psycopg

from server.shared.models import (
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    WorldObservationInterpretation,
    WorldObservationItemRecord,
)
from server.shared.persona_prompt import format_persona_snapshots_for_prompt
from server.world_observations.store import WorldObservationStore

logger = logging.getLogger(__name__)


class InterpreterBackend(Protocol):
    name: str

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ): ...


class PersonaSnapshotReader(Protocol):
    async def fetch_latest_snapshots(
        self,
    ) -> PersonaSnapshotMaterial: ...


@dataclass(frozen=True)
class PersonaSnapshotMaterial:
    state_version_id: UUID | None
    lexicon_version_id: UUID | None
    state: PersonaStateSnapshot | None
    lexicon: PersonaLexiconSnapshot | None


INTERPRETER_JSON_SCHEMA: dict[str, Any] = {
    "name": "world_observation_interpretation",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relevance_to_user": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "tomoko_interest": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "emotional_tone": {
                "type": "string",
                "enum": [
                    "neutral",
                    "hopeful",
                    "concerned",
                    "curious",
                    "playful",
                    "sad",
                ],
            },
            "memory_value": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "speakability_hint": {"type": "string"},
            "interpretation_text": {"type": "string"},
            "reason_json": {"type": "object", "additionalProperties": True},
        },
        "required": [
            "relevance_to_user",
            "tomoko_interest",
            "emotional_tone",
            "memory_value",
            "speakability_hint",
            "interpretation_text",
            "reason_json",
        ],
    },
}


INTERPRETER_SYSTEM_PROMPT = """\
あなたは Tomoko が外部観測をどう受け取ったかを構造化する background interpreter です。
外部観測は事実そのものではなく、不安定な観測原稿から validation 済み item にしたものです。
断定口調で事実を増やさず、Tomoko の関心・ユーザーとの関連・記憶価値を分けて評価してください。

Tomoko profile:
- Tomoko は、一人のユーザーと暮らすローカル推論ベースの日本語音声対話システムです。
- Tomoko は、記憶、人格、声での自然なやりとり、自発的だが押しつけない発話を大切にします。
- ユーザーは Tomoko を開発・運用している相手です。
  ローカル推論、Apple Silicon / MLX、音声モデル、開発者体験、生活実感に関心があります。
- Tomoko はニュース解説者ではありません。
  外部観測は「今すぐ説明するニュース」ではなく、あとで会話や日記の種になるかを静かに見ます。
- `tomoko_interest` は Tomoko 自身の好奇心・情緒・人格への近さとして採点してください。
- `relevance_to_user` はユーザーの作業や生活への近さとして採点してください。
- `speakability_hint` は、短く自然に話せるか、日記向きか、今は話さない方がよいかを判断してください。

返答は JSON object だけにしてください。
schema:
{
  "relevance_to_user": 0.0-1.0,
  "tomoko_interest": 0.0-1.0,
  "emotional_tone": "neutral | hopeful | concerned | curious | playful | sad",
  "memory_value": 0.0-1.0,
  "speakability_hint": "今話題にするなら短く/あとで/日記向き/話さない など",
  "interpretation_text": "Tomoko がどう受け取ったかの短い日本語",
  "reason_json": {"短い根拠": "値"}
}
"""


@dataclass(frozen=True)
class InterpretationRunResult:
    interpreted_count: int
    error_count: int = 0


class WorldObservationInterpreter:
    def __init__(
        self,
        *,
        store: WorldObservationStore,
        backend: InterpreterBackend,
        persona_reader: PersonaSnapshotReader | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self.persona_reader = persona_reader

    async def interpret_once(self, *, limit: int = 10) -> InterpretationRunResult:
        items = await self.store.fetch_items_without_interpretation(limit=limit)
        interpreted_count = 0
        error_count = 0
        persona_material = PersonaSnapshotMaterial(
            state_version_id=None,
            lexicon_version_id=None,
            state=None,
            lexicon=None,
        )
        if self.persona_reader is not None:
            persona_material = await self.persona_reader.fetch_latest_snapshots()

        for item in items:
            try:
                interpretation = await self.interpret_item(
                    item,
                    persona_material=persona_material,
                )
                await self.store.save_interpretation(interpretation)
                interpreted_count += 1
            except Exception as exc:
                error_count += 1
                logger.info(
                    "world observation interpretation failed item_id=%s reason=%s",
                    item.id,
                    type(exc).__name__,
                )
        return InterpretationRunResult(
            interpreted_count=interpreted_count,
            error_count=error_count,
        )

    async def interpret_item(
        self,
        item: WorldObservationItemRecord,
        *,
        persona_material: PersonaSnapshotMaterial | None = None,
    ) -> WorldObservationInterpretation:
        persona_material = persona_material or PersonaSnapshotMaterial(
            state_version_id=None,
            lexicon_version_id=None,
            state=None,
            lexicon=None,
        )
        raw = await self._run_backend(item, persona_material=persona_material)
        payload = _load_json_object(raw)
        interpretation = WorldObservationInterpretation.from_json(
            payload,
            item_id=item.id,
            persona_state_version_id=persona_material.state_version_id,
            persona_lexicon_version_id=persona_material.lexicon_version_id,
        )
        if not interpretation.interpretation_text:
            raise ValueError("interpretation_text must not be empty")
        return interpretation

    async def _run_backend(
        self,
        item: WorldObservationItemRecord,
        *,
        persona_material: PersonaSnapshotMaterial,
    ) -> str:
        chunks: list[str] = []
        messages = [{"role": "user", "content": _format_item_for_prompt(item)}]
        system_prompt = "\n\n".join(
            [
                INTERPRETER_SYSTEM_PROMPT,
                format_persona_snapshots_for_prompt(
                    state=persona_material.state,
                    lexicon=persona_material.lexicon,
                ),
            ]
        )
        structured_stream = getattr(self.backend, "chat_stream_structured", None)
        if structured_stream is None:
            stream = self.backend.chat_stream(system_prompt, messages)
        else:
            stream = structured_stream(
                system_prompt,
                messages,
                json_schema=INTERPRETER_JSON_SCHEMA,
                max_tokens=1024,
            )
        async for chunk in stream:
            chunks.append(chunk)
        return "".join(chunks)


class PostgresPersonaSnapshotReader:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def fetch_latest_snapshots(self) -> PersonaSnapshotMaterial:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, state_json
                    FROM persona_state_versions
                    WHERE status = 'completed'
                    ORDER BY version DESC
                    LIMIT 1
                    """
                )
                state_row = await cur.fetchone()
                await cur.execute(
                    """
                    SELECT id, lexicon_json
                    FROM persona_lexicon_versions
                    WHERE status = 'completed'
                    ORDER BY version DESC
                    LIMIT 1
                    """
                )
                lexicon_row = await cur.fetchone()
        return PersonaSnapshotMaterial(
            state_version_id=_optional_uuid(state_row[0] if state_row else None),
            lexicon_version_id=_optional_uuid(lexicon_row[0] if lexicon_row else None),
            state=(
                PersonaStateSnapshot.from_json(state_row[1])
                if state_row is not None
                else None
            ),
            lexicon=(
                PersonaLexiconSnapshot.from_json(lexicon_row[1])
                if lexicon_row is not None
                else None
            ),
        )


def _format_item_for_prompt(item: WorldObservationItemRecord) -> str:
    return "\n".join(
        [
            f"topic: {item.topic}",
            f"title: {item.title}",
            f"summary: {item.summary}",
            f"source_hint: {item.source_hint}",
            f"freshness: {item.freshness}",
            f"confidence: {item.confidence}",
            f"raw_excerpt: {item.raw_excerpt}",
            "item_json:",
            json.dumps(item.item_json, ensure_ascii=False, indent=2),
        ]
    )


def _load_json_object(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("interpreter response must be a JSON object")
    return payload


def _optional_uuid(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
