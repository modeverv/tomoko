from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.gateway.context import ContextSnapshotBuilder
from server.shared.models import ContextBuildPolicy, ConversationTurn


class FastConversationReader:
    def __init__(self) -> None:
        self.turns = [
            ConversationTurn(
                speaker="user",
                text=f"発話 {index}",
                timestamp=datetime(2026, 5, 24, 12, index, tzinfo=UTC),
            )
            for index in range(12)
        ]

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        return self.turns[-limit:]


@pytest.mark.perf
async def test_fast_context_snapshot_under_50ms() -> None:
    builder = ContextSnapshotBuilder(conversation_log_reader=FastConversationReader())

    snapshot = await builder.build(
        text="トモコ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("fast"),
    )

    assert snapshot.build_elapsed_ms < 50
