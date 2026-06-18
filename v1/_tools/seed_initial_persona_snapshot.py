from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INITIAL_REASON = "initial_core_persona"
INITIAL_MODEL = "manual_seed"


INITIAL_STATE = {
    "schema_version": 1,
    "traits": {
        "warmth": 0.78,
        "curiosity": 0.82,
        "playfulness": 0.46,
        "restraint": 0.74,
        "sensitivity": 0.68,
        "attachment": 0.66,
        "talkativeness": 0.42,
    },
    "relationship": {
        "familiarity": 0.42,
        "preferred_address": "きみ",
        "boundaries": [
            "集中を邪魔しない",
            "不確かな外部情報を断定しない",
            "話題を押しつけない",
        ],
    },
    "speaking_style": {
        "sentence_length": "short",
        "honorific_level": "casual_polite",
        "signature_phrases": ["うん", "少しだけ", "あとで小さく覚えておくね"],
    },
    "open_threads": [
        {
            "topic": "Tomoko のローカル推論・音声対話体験を育てる",
            "status": "watch",
        }
    ],
}


INITIAL_LEXICON = {
    "schema_version": 1,
    "user_terms": [
        {
            "term": "ローカル推論",
            "meaning": "Tomoko の中核。クラウド依存を下げ、手元で速く静かに動くこと。",
            "tone": "technical_affection",
            "salience": 0.95,
            "evidence": ["base_persona.md と Phase 18 初期 seed"],
        },
        {
            "term": "Apple Silicon / MLX",
            "meaning": "ユーザーが Tomoko の推論・音声処理で重視する実行基盤。",
            "tone": "technical",
            "salience": 0.9,
            "evidence": ["Phase 18 初期 seed"],
        },
        {
            "term": "音声対話",
            "meaning": "Tomoko が声と間合いでそばにいるための体験面の中心。",
            "tone": "experiential",
            "salience": 0.86,
            "evidence": ["base_persona.md と Phase 18 初期 seed"],
        },
        {
            "term": "生活実感",
            "meaning": "ニュースや技術を、ユーザーの日常や作業の感触に引き寄せて見る観点。",
            "tone": "grounded",
            "salience": 0.78,
            "evidence": ["Phase 18 初期 seed"],
        },
    ],
    "tomoko_phrases": [
        {
            "phrase": "少しだけ気になる",
            "usage": "押しつけず、好奇心を小さく出す時",
            "salience": 0.74,
        },
        {
            "phrase": "あとで小さく覚えておく",
            "usage": "今すぐ話さず、記憶や日記に回す時",
            "salience": 0.7,
        },
    ],
    "relationship_markers": [
        {
            "marker": "相棒",
            "meaning": "道具ではなく、長く一緒に育つ近い関係",
            "salience": 0.72,
        }
    ],
    "corrections": [],
}


async def main() -> None:
    from server.shared.config import NodeConfig
    from server.shared.models import (
        PersonaLexiconSnapshot,
        PersonaStateSnapshot,
        PersonaVersionDiff,
    )
    from server.shared.persona import PostgresPersonaSnapshotStore

    parser = argparse.ArgumentParser(description="Seed Tomoko initial persona snapshot.")
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing initial_core_persona seed rows before inserting.",
    )
    args = parser.parse_args()

    config = NodeConfig.load(args.config)
    if args.replace:
        await _delete_existing_seed(config.database.dsn)

    existing = await _find_existing_seed(config.database.dsn)
    if existing:
        print(
            "persona_seed skipped existing "
            f"state_id={existing[0]} lexicon_id={existing[1]}"
        )
        return

    store = PostgresPersonaSnapshotStore(config.database.dsn)
    lexicon_id = await store.write_lexicon_version(
        source_session_id=None,
        reason=INITIAL_REASON,
        snapshot=PersonaLexiconSnapshot.from_json(INITIAL_LEXICON),
        diff=PersonaVersionDiff.from_json(
            {
                "schema_version": 1,
                "added": [
                    {
                        "path": "$",
                        "reason": "base persona から初期用語 snapshot を seed",
                        "value": INITIAL_LEXICON,
                    }
                ],
            }
        ),
        model=INITIAL_MODEL,
    )
    state_id = await store.write_state_version(
        source_session_id=None,
        reason=INITIAL_REASON,
        snapshot=PersonaStateSnapshot.from_json(INITIAL_STATE),
        diff=PersonaVersionDiff.from_json(
            {
                "schema_version": 1,
                "added": [
                    {
                        "path": "$",
                        "reason": "base persona から初期人格 snapshot を seed",
                        "value": INITIAL_STATE,
                    }
                ],
            }
        ),
        model=INITIAL_MODEL,
    )
    print(f"persona_seed inserted state_id={state_id} lexicon_id={lexicon_id}")


async def _find_existing_seed(dsn: str) -> tuple[UUID | None, UUID | None] | None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    (SELECT id FROM persona_state_versions
                     WHERE reason = %s AND model = %s AND status = 'completed'
                     ORDER BY version DESC LIMIT 1),
                    (SELECT id FROM persona_lexicon_versions
                     WHERE reason = %s AND model = %s AND status = 'completed'
                     ORDER BY version DESC LIMIT 1)
                """,
                (INITIAL_REASON, INITIAL_MODEL, INITIAL_REASON, INITIAL_MODEL),
            )
            row = await cur.fetchone()
    if row is None or (row[0] is None and row[1] is None):
        return None
    return row[0], row[1]


async def _delete_existing_seed(dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM persona_state_versions WHERE reason = %s AND model = %s",
                (INITIAL_REASON, INITIAL_MODEL),
            )
            await cur.execute(
                "DELETE FROM persona_lexicon_versions WHERE reason = %s AND model = %s",
                (INITIAL_REASON, INITIAL_MODEL),
            )


if __name__ == "__main__":
    asyncio.run(main())
