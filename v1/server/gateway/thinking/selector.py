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

CALENDAR_CUES = (
    "予定",
    "スケジュール",
    "カレンダー",
    "今日",
    "明日",
    "あした",
    "明後日",
    "あさって",
    "今週",
    "来週",
    "空いてる",
    "空き",
    "会議",
    "ミーティング",
    "mtg",
    "MTG",
)

SHORT_FAST_LIMIT = 18
DEEP_LENGTH_THRESHOLD = 30


def should_use_deep_memory(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if has_deep_memory_cue(normalized):
        return True
    if len(normalized) <= SHORT_FAST_LIMIT:
        return False
    return len(normalized) >= DEEP_LENGTH_THRESHOLD


def has_deep_memory_cue(text: str) -> bool:
    normalized = text.strip()
    return any(cue in normalized for cue in DEEP_MEMORY_CUES)


def has_calendar_cue(text: str) -> bool:
    normalized = text.strip()
    if _looks_like_clock_query(normalized):
        return False
    return any(cue in normalized for cue in CALENDAR_CUES)


def _looks_like_clock_query(text: str) -> bool:
    normalized = text.replace(" ", "").replace("　", "")
    return (
        "今何時" in normalized
        or "いま何時" in normalized
        or "何時ぐらい" in normalized
        or "何時くらい" in normalized
        or "何時かわかる" in normalized
        or normalized in {"何時", "時刻", "現在時刻"}
    )
