from __future__ import annotations

TTS_FLUSH_PUNCTUATION = "。！？"


class ReplyAudioPlanner:
    """Tracks reply text buffering and emits TTS sentence flushes."""

    def __init__(self) -> None:
        self._tts_buffer = ""

    def append_delta(self, delta: str) -> list[str]:
        self._tts_buffer += delta
        sentences, self._tts_buffer = _split_flushable_sentences(self._tts_buffer)
        return sentences

    def flush_remainder(self) -> str | None:
        text = self._tts_buffer.strip()
        self._tts_buffer = ""
        return text or None


def _split_flushable_sentences(text: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    remainder = text
    while True:
        flush_index = _first_sentence_end_index(remainder)
        if flush_index is None:
            return sentences, remainder
        sentence = remainder[: flush_index + 1].strip()
        remainder = remainder[flush_index + 1 :]
        if sentence:
            sentences.append(sentence)


def _first_sentence_end_index(text: str) -> int | None:
    indexes = [text.find(punctuation) for punctuation in TTS_FLUSH_PUNCTUATION]
    found = [index for index in indexes if index >= 0]
    if not found:
        return None
    return min(found)
