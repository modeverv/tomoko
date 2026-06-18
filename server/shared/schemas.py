from __future__ import annotations

from dataclasses import dataclass

SHORT_REACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["emotion", "text"],
    "properties": {
        "emotion": {"type": "string"},
        "text": {"type": "string"},
    },
    "additionalProperties": False,
}

SUMMARY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["keyword", "conclusion"],
    "properties": {
        "keyword": {"type": "string"},
        "conclusion": {"type": "string"},
    },
    "additionalProperties": False,
}

STOP_INTENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["strength", "reason"],
    "properties": {
        "strength": {"type": "string", "enum": ["soft", "normal", "hard"]},
        "reason": {"type": "string"},
    },
    "additionalProperties": False,
}

SCREEN_ACTIVITY_FIXED_LINE_KEYS: tuple[str, ...] = (
    "SCREEN_ACTIVITY_LABEL",
    "CONFIDENCE",
    "WATCHING_VIDEO",
    "PLAYING_GAME",
)

HUMAN_PRESENCE_FIXED_LINE_KEYS: tuple[str, ...] = (
    "PRESENT",
    "CONFIDENCE",
)


@dataclass(frozen=True, slots=True)
class FixedLineSchema:
    name: str
    required_keys: tuple[str, ...]


SCREEN_ACTIVITY_FIXED_LINE_SCHEMA = FixedLineSchema(
    name="screen_activity",
    required_keys=SCREEN_ACTIVITY_FIXED_LINE_KEYS,
)

HUMAN_PRESENCE_FIXED_LINE_SCHEMA = FixedLineSchema(
    name="human_presence",
    required_keys=HUMAN_PRESENCE_FIXED_LINE_KEYS,
)


def parse_fixed_line_output(text: str, schema: FixedLineSchema) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    missing = [key for key in schema.required_keys if key not in values]
    if missing:
        raise ValueError(f"{schema.name} fixed-line output missing keys: {', '.join(missing)}")
    return values
