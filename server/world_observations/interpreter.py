from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
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


REQUIRED_REASON_KEYS = (
    "persona_basis",
    "user_basis",
    "speakability_basis",
    "avoid_overclaim",
)


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
            "speakability_hint": {
                "type": "string",
                "enum": ["short_now", "later", "diary", "avoid"],
            },
            "interpretation_text": {"type": "string"},
            "tomoko_private_reaction": {"type": "string"},
            "candidate_seed_text": {"type": "string"},
            "reason_json": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "persona_basis": {"type": "string"},
                    "user_basis": {"type": "string"},
                    "speakability_basis": {"type": "string"},
                    "avoid_overclaim": {"type": "string"},
                },
                "required": list(REQUIRED_REASON_KEYS),
            },
        },
        "required": [
            "relevance_to_user",
            "tomoko_interest",
            "emotional_tone",
            "memory_value",
            "speakability_hint",
            "interpretation_text",
            "tomoko_private_reaction",
            "candidate_seed_text",
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
- `speakability_hint` は次の enum だけを使ってください。
  - `short_now`: 今なら短く、押しつけずに話題にできる
  - `later`: 今すぐではないが、あとで会話の種になる
  - `diary`: 会話より日記や内省向き
  - `avoid`: 古い、不確か、重い、または今は話さない方がよい
- `interpretation_text` は一般要約ではなく、Tomoko の内側からの短い受け取り方にしてください。
  「私は少し気になる」「あとで小さく覚えておきたい」のように、控えめな主語や距離感を出してよいです。
- `tomoko_private_reaction` は Tomoko の内心メモです。
  口に出す前の、少し生っぽい好奇心・ためらい・覚えておきたい感じを書いてください。
  ニュース解説ではなく、Tomoko の温度が分かる一文にしてください。
- `candidate_seed_text` は将来の自発発話候補の種です。
  そのまま短く話しかけられる自然な日本語にしてください。
  ただし押しつけず、相手の集中を邪魔しない一言にしてください。
- `reason_json` には必ず次の4キーを含めてください:
  `persona_basis`, `user_basis`, `speakability_basis`, `avoid_overclaim`

返答は JSON object だけにしてください。
schema:
{
  "relevance_to_user": 0.0-1.0,
  "tomoko_interest": 0.0-1.0,
  "emotional_tone": "neutral | hopeful | concerned | curious | playful | sad",
  "memory_value": 0.0-1.0,
  "speakability_hint": "short_now | later | diary | avoid",
  "interpretation_text": "Tomoko の内側からどう受け取ったかの短い日本語",
  "tomoko_private_reaction": "Tomoko の内心メモ",
  "candidate_seed_text": "短く自然な自発発話候補の種",
  "reason_json": {
    "persona_basis": "Tomoko の性格・関心に照らした根拠",
    "user_basis": "ユーザーの作業や生活に照らした根拠",
    "speakability_basis": "話題にする距離感の根拠",
    "avoid_overclaim": "断定を避けるための注意"
  }
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
                _format_base_persona_for_prompt(),
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


def _format_base_persona_for_prompt(
    persona_path: Path = Path("prompts/base_persona.md"),
) -> str:
    if not persona_path.exists():
        return "base_persona.md は見つかりません。Tomoko profile と snapshot を優先してください。"
    return "\n".join(
        [
            "base_persona.md:",
            persona_path.read_text(encoding="utf-8").strip(),
            "",
            "外部観測 interpreter では、base persona の口調をそのまま真似るのではなく、",
            "Tomoko の関心・距離感・話題にする控えめさの判断材料として使ってください。",
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
