from __future__ import annotations

import unicodedata


class ReplyTextSanitizer:
    """Keeps generated reply text inside the Japanese-only output contract."""

    def sanitize_delta(self, delta: str) -> str:
        return "".join(char for char in delta if _is_allowed_reply_char(char))


def _is_allowed_reply_char(char: str) -> bool:
    if char.isspace():
        return False
    category = unicodedata.category(char)
    if category.startswith("C"):
        return False
    if char.isascii():
        return char.isdigit()
    name = unicodedata.name(char, "")
    if any(
        script in name
        for script in (
            "CJK UNIFIED",
            "HIRAGANA",
            "KATAKANA",
            "IDEOGRAPHIC",
            "FULLWIDTH",
            "HALFWIDTH KATAKANA",
        )
    ):
        return True
    if category.startswith(("P", "S", "N")):
        return True
    return False
