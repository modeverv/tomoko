from __future__ import annotations

import re
from collections import Counter

from server.shared.models import Transcript, TranscriptFilterDecision


class TranscriptFilter:
    WAKE_WORDS = ("トモコ", "ともこ", "tomoko", "智子", "朋子")
    KNOWN_HALLUCINATION_PHRASES = (
        "ご視聴ありがとうございました",
        "ご視聴頂きましてありがとうございました",
        "ご視聴ください",
        "字幕をご視聴",
        "字幕をご覧",
        "チャンネル登録",
        "高評価",
    )
    LOW_AUDIO_KNOWN_PHRASES = (
        "お疲れ様でした",
        "お疲れさまでした",
        "お疲れ様です",
        "お疲れさまです",
    )
    REPETITION_HINTS = (
        "日曜日の日曜日",
        "またまたまた",
    )
    LOW_AUDIO_DB = -24.0
    LOW_AUDIO_SHORT_MAX_CHARS = 20

    def evaluate(
        self,
        transcript: Transcript,
        *,
        is_partial: bool | None = None,
    ) -> TranscriptFilterDecision:
        partial = (not transcript.is_final) if is_partial is None else is_partial
        reason = self._drop_reason(transcript)
        if reason is None:
            return TranscriptFilterDecision(action="accept", reason="accepted")
        if partial:
            return TranscriptFilterDecision(action="suppress_partial", reason=reason)
        return TranscriptFilterDecision(action="drop", reason=reason)

    def _drop_reason(self, transcript: Transcript) -> str | None:
        text = transcript.text.strip()
        normalized = _normalize_text(text)
        if not normalized:
            return "empty"
        if _contains_wake_word(text):
            return None
        if len(normalized) <= 2:
            return "too_short"
        if any(phrase in text for phrase in self.KNOWN_HALLUCINATION_PHRASES):
            return "known_hallucination_phrase"
        if (
            any(phrase in text for phrase in self.LOW_AUDIO_KNOWN_PHRASES)
            and transcript.audio_level_db <= self.LOW_AUDIO_DB
        ):
            return "known_hallucination_phrase"
        if (
            transcript.audio_level_db <= -30.0
            and len(normalized) <= self.LOW_AUDIO_SHORT_MAX_CHARS
        ):
            return "low_audio_short_text"
        if _looks_like_mixed_language_loop(text):
            return "mixed_language_loop"
        if any(hint in normalized for hint in self.REPETITION_HINTS):
            return "repetition_loop"
        if _looks_like_repetition_loop(text):
            return "repetition_loop"
        return None


def _contains_wake_word(text: str) -> bool:
    folded = text.casefold()
    return any(wake_word.casefold() in folded for wake_word in TranscriptFilter.WAKE_WORDS)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.casefold())


def _looks_like_mixed_language_loop(text: str) -> bool:
    ascii_words = re.findall(r"[A-Za-z]+", text.casefold())
    if len(ascii_words) < 3:
        return False
    counts = Counter(ascii_words)
    most_common_count = counts.most_common(1)[0][1]
    return most_common_count >= 3 and len(counts) <= 3


def _looks_like_repetition_loop(text: str) -> bool:
    tokens = _tokenize_for_repetition(text)
    if len(tokens) >= 6:
        counts = Counter(tokens)
        if counts.most_common(1)[0][1] >= 4:
            return True
        if len(set(tokens)) / len(tokens) <= 0.35:
            return True

    normalized = _normalize_text(text)
    if len(normalized) < 8:
        return False
    for size in range(2, min(10, len(normalized) // 2 + 1)):
        counts = Counter(
            normalized[index : index + size]
            for index in range(0, len(normalized) - size + 1)
        )
        repeated, count = counts.most_common(1)[0]
        if count >= 3 and len(repeated) * count >= len(normalized) * 0.45:
            return True
    return False


def _tokenize_for_repetition(text: str) -> list[str]:
    ascii_words = re.findall(r"[A-Za-z]+", text.casefold())
    japanese_chunks = re.findall(r"[ぁ-んァ-ン一-龥ー]+", text)
    tokens: list[str] = []
    for chunk in japanese_chunks:
        tokens.extend(_split_japanese_chunk(chunk))
    tokens.extend(ascii_words)
    return [token for token in tokens if token]


def _split_japanese_chunk(chunk: str) -> list[str]:
    tokens = re.split(r"(また|日曜日|今日は|お疲れ様|お疲れさま)", chunk)
    return [token for token in tokens if token]
