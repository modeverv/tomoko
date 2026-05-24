from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from server.gateway.dedup import DuplicateSpeechFilter, RecentTranscript
from server.gateway.presence import PresenceManager
from server.gateway.resolver import DirectSpeakerResolver
from server.shared.config import NodeConfig
from server.shared.presence import EdgeStatus, InMemoryPresenceStore, PresenceReport


class FakeRecentTranscriptReader:
    def __init__(self, transcripts: tuple[RecentTranscript, ...]) -> None:
        self.transcripts = transcripts
        self.calls: list[tuple[datetime, str, int]] = []

    async def read_recent_transcripts(
        self,
        *,
        since: datetime,
        exclude_device_id: str,
        limit: int,
    ) -> tuple[RecentTranscript, ...]:
        self.calls.append((since, exclude_device_id, limit))
        return tuple(
            transcript
            for transcript in self.transcripts
            if transcript.recorded_at >= since
            and transcript.device_id != exclude_device_id
        )[:limit]


@pytest.mark.unit
async def test_presence_store_keeps_audio_level_without_audio_bytes() -> None:
    now = datetime(2026, 5, 24, 23, 30, tzinfo=UTC)
    store = InMemoryPresenceStore()
    report = await store.insert_presence_report(
        device_id="kitchen",
        audio_level_db=-18.5,
        observed_at=now,
        transcript_id=uuid4(),
        transcript_text="今日いい天気",
    )
    status = await store.upsert_edge_status(
        device_id="kitchen",
        status="online",
        last_seen_at=now,
    )
    fetched = await store.fetch_recent_presence_reports(
        since=now - timedelta(seconds=1),
        limit=10,
    )

    assert fetched == (report,)
    assert status == EdgeStatus(
        device_id="kitchen",
        status="online",
        last_seen_at=now,
    )
    assert not hasattr(report, "audio")
    assert not hasattr(report, "audio_bytes")


@pytest.mark.unit
def test_loudest_edge_is_primary_with_recency_tie_break() -> None:
    now = datetime(2026, 5, 24, 23, 30, tzinfo=UTC)
    resolver = DirectSpeakerResolver()
    kitchen = PresenceReport(
        id=uuid4(),
        device_id="kitchen",
        observed_at=now,
        audio_level_db=-20,
    )
    living = PresenceReport(
        id=uuid4(),
        device_id="living",
        observed_at=now - timedelta(milliseconds=10),
        audio_level_db=-10,
    )

    assert resolver.resolve([kitchen, living]) == living

    newer = PresenceReport(
        id=uuid4(),
        device_id="desk",
        observed_at=now + timedelta(milliseconds=1),
        audio_level_db=-10,
    )
    assert resolver.resolve([living, newer]) == newer


@pytest.mark.unit
async def test_presence_manager_reports_and_resolves_primary_edge() -> None:
    now = datetime(2026, 5, 24, 23, 30, tzinfo=UTC)
    store = InMemoryPresenceStore()
    manager = PresenceManager(
        store=store,
        resolver=DirectSpeakerResolver(),
        resolve_window=timedelta(seconds=1),
    )
    await manager.report(
        device_id="kitchen",
        audio_level_db=-25,
        observed_at=now,
    )
    living = await manager.report(
        device_id="living",
        audio_level_db=-15,
        observed_at=now + timedelta(milliseconds=1),
    )

    assert await manager.resolve_primary(now=now + timedelta(milliseconds=2)) == living


@pytest.mark.unit
async def test_duplicate_speech_filtered_across_edges() -> None:
    now = datetime(2026, 5, 24, 23, 30, tzinfo=UTC)
    reader = FakeRecentTranscriptReader(
        (
            RecentTranscript(
                text="今日いい天気",
                device_id="living",
                recorded_at=now - timedelta(milliseconds=300),
            ),
        )
    )
    duplicate_filter = DuplicateSpeechFilter(reader=reader)

    assert await duplicate_filter.is_duplicate(
        "今日、いい天気。",
        device_id="kitchen",
        observed_at=now,
    )
    assert reader.calls[0][1] == "kitchen"


@pytest.mark.unit
async def test_hard_interrupt_is_not_duplicate_even_if_other_edge_heard_it() -> None:
    now = datetime(2026, 5, 24, 23, 30, tzinfo=UTC)
    duplicate_filter = DuplicateSpeechFilter(
        reader=FakeRecentTranscriptReader(
            (
                RecentTranscript(
                    text="ストップ",
                    device_id="living",
                    recorded_at=now,
                ),
            )
        )
    )

    assert not await duplicate_filter.is_duplicate(
        "ストップ",
        device_id="kitchen",
        observed_at=now,
    )


@pytest.mark.unit
def test_edge_kitchen_config_marks_node_as_edge() -> None:
    config = NodeConfig.load("config/edge_kitchen.toml")

    assert config.node.role == "edge"
    assert config.node.device_id == "kitchen"
    assert config.inference.stt_backend == "local_whisper_mlx_small"
    assert config.inference.tts_backend == "kokoro_mlx"
