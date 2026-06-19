from __future__ import annotations

import hashlib
from pathlib import Path

from make_model.schema import CorpusUtterance, PrefixExample, read_jsonl

TEXT_KEYS = ("text", "utterance", "transcript", "content")


def load_corpus(path: Path) -> list[CorpusUtterance]:
    if path.suffix == ".jsonl":
        return _load_jsonl_corpus(path)
    return _load_text_corpus(path)


def _load_text_corpus(path: Path) -> list[CorpusUtterance]:
    utterances: list[CorpusUtterance] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            source = f"{path.name}:{line_number}"
            utterances.append(
                CorpusUtterance(
                    text=text,
                    source=source,
                    utterance_id=_utterance_id(source, text),
                )
            )
    return utterances


def _load_jsonl_corpus(path: Path) -> list[CorpusUtterance]:
    utterances: list[CorpusUtterance] = []
    for line_number, payload in enumerate(read_jsonl(path), start=1):
        text = _first_text_value(payload)
        if not text:
            continue
        source = str(payload.get("source") or f"{path.name}:{line_number}")
        utterance_id = str(payload.get("utterance_id") or _utterance_id(source, text))
        turn_index = payload.get("turn_index")
        utterances.append(
            CorpusUtterance(
                text=text,
                source=source,
                utterance_id=utterance_id,
                conversation_id=payload.get("conversation_id"),
                turn_index=int(turn_index) if turn_index is not None else None,
            )
        )
    return utterances


def _first_text_value(payload: dict[str, object]) -> str:
    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_prefix_examples(
    utterances: list[CorpusUtterance],
    *,
    min_chars: int = 1,
    stride_chars: int = 1,
    include_final: bool = True,
    max_prefixes_per_utterance: int | None = None,
) -> list[PrefixExample]:
    if min_chars < 1:
        raise ValueError("min_chars must be >= 1")
    if stride_chars < 1:
        raise ValueError("stride_chars must be >= 1")

    examples: list[PrefixExample] = []
    for utterance in utterances:
        text = utterance.text.strip()
        if not text:
            continue
        lengths = list(range(min_chars, len(text) + 1, stride_chars))
        if include_final and len(text) not in lengths:
            lengths.append(len(text))
        lengths = sorted(set(length for length in lengths if length <= len(text)))
        if max_prefixes_per_utterance is not None:
            lengths = lengths[:max_prefixes_per_utterance]
        for prefix_index, length in enumerate(lengths):
            examples.append(
                PrefixExample(
                    utterance_id=utterance.utterance_id or _utterance_id(utterance.source, text),
                    prefix_index=prefix_index,
                    prefix_text=text[:length],
                    full_text=text,
                    source=utterance.source,
                    conversation_id=utterance.conversation_id,
                    turn_index=utterance.turn_index,
                    is_final=length == len(text),
                )
            )
    return examples


def _utterance_id(source: str, text: str) -> str:
    digest = hashlib.sha1(f"{source}\0{text}".encode()).hexdigest()
    return digest[:16]
