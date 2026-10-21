from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

V2_NOTIFY_CHANNELS: frozenset[str] = frozenset(
    {
        "v2_stt_observation",
        "v2_prompt_request",
        "v2_speech_order",
        "v2_model_output",
        "v2_candidate",
        "v2_user_status",
        "v2_info_ready",
        "v2_summary_ready",
    }
)


@dataclass(frozen=True, slots=True)
class NotifyMessage:
    channel: str
    payload: UUID


def validate_channel(channel: str) -> str:
    if channel not in V2_NOTIFY_CHANNELS:
        raise ValueError(f"unknown v2 notify channel: {channel}")
    return channel


def parse_id_payload(payload: str) -> UUID:
    if payload.strip() != payload or "{" in payload or ":" in payload:
        raise ValueError("v2 NOTIFY payload must be a bare UUID string")
    return UUID(payload)


def build_notify_message(channel: str, payload_id: UUID) -> NotifyMessage:
    return NotifyMessage(channel=validate_channel(channel), payload=payload_id)


def notify_sql(channel: str, payload_id: UUID) -> tuple[str, dict[str, str]]:
    validate_channel(channel)
    print(
        "[tomoko:db] notify_send "
        f"channel={channel!r} payload={str(payload_id)!r}",
        flush=True,
    )
    return "SELECT pg_notify(%(channel)s, %(payload)s)", {
        "channel": channel,
        "payload": str(payload_id),
    }
