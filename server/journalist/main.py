from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from server.journalist.input import (
    JournalistInputBuilder,
    JournalistInputSnapshot,
    PostgresJournalistSourceReader,
)
from server.shared.config import NodeConfig
from server.shared.diary import DiaryEntry, DiaryStore, PostgresDiaryStore
from server.shared.inference.router import InferenceRouter

logger = logging.getLogger(__name__)


DIARY_SYSTEM_PROMPT = """\
あなたは Tomoko の日記を書く係です。
会話原本や候補ログは別に保存されています。
ここでは、その日の出来事を Tomoko の内側から短く振り返る日記だけを書いてください。

ルール:
- 日本語で書く
- 3〜6文
- 人間との会話、印象的な ambient の気配、言えなかった候補を自然に織り込む
- 事実を捏造しない
- 箇条書きにしない
- 「日記:」のような見出しを付けない
"""


@dataclass(frozen=True)
class DiaryWriteResult:
    entry: DiaryEntry | None
    error_count: int = 0


class DiaryWriter:
    def __init__(
        self,
        *,
        input_builder: JournalistInputBuilder,
        diary_store: DiaryStore,
        router: InferenceRouter,
    ) -> None:
        self.input_builder = input_builder
        self.diary_store = diary_store
        self.router = router

    async def write_for_date(self, diary_date: date) -> DiaryWriteResult:
        snapshot = await self.input_builder.build(diary_date)
        try:
            body_text = await self._generate_body(snapshot)
        except Exception as exc:
            logger.error(
                "DiaryWriter generation failed diary_date=%s error=%s",
                diary_date,
                type(exc).__name__,
            )
            return DiaryWriteResult(entry=None, error_count=1)

        if not body_text:
            logger.error("DiaryWriter empty output diary_date=%s", diary_date)
            return DiaryWriteResult(entry=None, error_count=1)

        entry = await self.diary_store.insert_entry(
            diary_date=diary_date,
            body_text=body_text,
            source_session_ids=snapshot.source_session_ids,
            source_candidate_ids=snapshot.source_candidate_ids,
            source_world_observation_interpretation_ids=(
                snapshot.source_world_observation_interpretation_ids
            ),
        )
        logger.info(
            "DiaryWriter saved diary_date=%s chars=%s sessions=%s candidates=%s",
            diary_date,
            len(entry.body_text),
            len(entry.source_session_ids),
            len(entry.source_candidate_ids),
        )
        return DiaryWriteResult(entry=entry)

    async def _generate_body(self, snapshot: JournalistInputSnapshot) -> str:
        backend = await self.router.select("diary", "privacy")
        prompt = _format_snapshot_for_prompt(snapshot)
        chunks: list[str] = []
        async for chunk in backend.chat_stream(
            DIARY_SYSTEM_PROMPT,
            [{"role": "user", "content": prompt}],
        ):
            chunks.append(chunk)
        return _clean_diary_text("".join(chunks))


def build_default_writer(config: NodeConfig) -> DiaryWriter:
    reader = PostgresJournalistSourceReader(config.database.dsn)
    return DiaryWriter(
        input_builder=JournalistInputBuilder(reader=reader),
        diary_store=PostgresDiaryStore(config.database.dsn),
        router=InferenceRouter(config=config),
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Tomoko journalist process.")
    parser.add_argument(
        "--config",
        default="config/central_realtime.toml",
        help="Path to TOML config.",
    )
    parser.add_argument(
        "--date",
        help="Diary date in YYYY-MM-DD. Defaults to yesterday in UTC.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=3600.0)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    config = NodeConfig.load(args.config)
    writer = build_default_writer(config)

    if args.watch:
        while True:
            result = await writer.write_for_date(_target_date(args.date))
            print(_format_result(result))
            await asyncio.sleep(args.interval_sec)

    result = await writer.write_for_date(_target_date(args.date))
    print(_format_result(result))
    return 0


def _target_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return (datetime.now(UTC) - timedelta(days=1)).date()


def _format_result(result: DiaryWriteResult) -> str:
    if result.entry is None:
        return f"diary_saved=0 error_count={result.error_count}"
    return f"diary_saved=1 diary_id={result.entry.id} error_count={result.error_count}"


def _format_snapshot_for_prompt(snapshot: JournalistInputSnapshot) -> str:
    lines = [
        f"日付: {snapshot.diary_date.isoformat()}",
        "",
        "会話セッション要約:",
    ]
    if snapshot.session_summaries:
        lines.extend(
            f"- {summary.summary_text}"
            for summary in snapshot.session_summaries
            if summary.summary_text.strip()
        )
    else:
        lines.append("- まとまった会話セッション要約はない。")

    lines.extend(["", "会話ターン:"])
    if snapshot.conversation_turns:
        lines.extend(_format_turn_for_prompt(turn) for turn in snapshot.conversation_turns)
    else:
        lines.append("- 会話ターンはない。")

    lines.extend(["", "ambient の気配:"])
    lines.append(f"- 記録数: {snapshot.ambient_digest.total_count}")
    lines.extend(f"- 抜粋: {excerpt}" for excerpt in snapshot.ambient_digest.excerpts)

    lines.extend(["", "言えなかった候補:"])
    if snapshot.dismissed_candidates:
        lines.extend(
            f"- {candidate.generated_text or candidate.seed}"
            for candidate in snapshot.dismissed_candidates
        )
    else:
        lines.append("- なし。")

    lines.extend(["", "外部観測から Tomoko が気にしたこと:"])
    if snapshot.world_observations:
        lines.extend(
            _format_world_observation_for_prompt(item)
            for item in snapshot.world_observations
        )
    else:
        lines.append("- なし。")

    return "\n".join(lines)


def _format_turn_for_prompt(turn) -> str:
    speaker = "人間" if turn.role == "user" else "Tomoko"
    status = "" if turn.status == "completed" else f" ({turn.status})"
    emotion = f" emotion={turn.emotion}" if turn.emotion else ""
    return f"- {speaker}{status}{emotion}: {turn.text}"


def _format_world_observation_for_prompt(item) -> str:
    reason = f" reason={item.reason}" if item.reason else ""
    return (
        f"- [{item.topic}/{item.freshness}/confidence={item.confidence:.2f}] "
        f"{item.title}: {item.interpretation_text}{reason}"
    )


def _clean_diary_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
