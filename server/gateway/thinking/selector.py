from __future__ import annotations

DEEP_MEMORY_CUES = (
    "覚えて",
    "覚えてる",
    "思い出",
    "前回",
    "この前",
    "こないだ",
    "昔",
    "以前",
    "先週",
    "数日前",
    "あの時",
    "あのとき",
    "話してた",
    "話した",
    "続き",
    "その後",
    "どうなった",
)

SHORT_FAST_LIMIT = 18
DEEP_LENGTH_THRESHOLD = 30


def should_use_deep_memory(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if any(cue in normalized for cue in DEEP_MEMORY_CUES):
        return True
    if len(normalized) <= SHORT_FAST_LIMIT:
        return False
    return len(normalized) >= DEEP_LENGTH_THRESHOLD
