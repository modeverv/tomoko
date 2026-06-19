from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CorpusUtterance:
    text: str
    source: str
    utterance_id: str = ""
    conversation_id: str | None = None
    turn_index: int | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PrefixExample:
    utterance_id: str
    prefix_index: int
    prefix_text: str
    full_text: str
    source: str
    conversation_id: str | None = None
    turn_index: int | None = None
    is_final: bool = False

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PrefixExample:
        return cls(
            utterance_id=str(payload["utterance_id"]),
            prefix_index=int(payload["prefix_index"]),
            prefix_text=str(payload["prefix_text"]),
            full_text=str(payload["full_text"]),
            source=str(payload.get("source", "")),
            conversation_id=payload.get("conversation_id"),
            turn_index=(
                int(payload["turn_index"]) if payload.get("turn_index") is not None else None
            ),
            is_final=bool(payload.get("is_final", False)),
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TeacherLabel:
    utterance_id: str
    prefix_index: int
    prefix_text: str
    full_text: str
    saturation: float
    teacher_model: str
    source: str = ""
    conversation_id: str | None = None
    turn_index: int | None = None
    is_final: bool = False
    label_source: str = "teacher_llm"
    raw_output: str = ""

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TeacherLabel:
        return cls(
            utterance_id=str(payload["utterance_id"]),
            prefix_index=int(payload["prefix_index"]),
            prefix_text=str(payload["prefix_text"]),
            full_text=str(payload["full_text"]),
            saturation=float(payload["saturation"]),
            teacher_model=str(payload.get("teacher_model", "")),
            source=str(payload.get("source", "")),
            conversation_id=payload.get("conversation_id"),
            turn_index=(
                int(payload["turn_index"]) if payload.get("turn_index") is not None else None
            ),
            is_final=bool(payload.get("is_final", False)),
            label_source=str(payload.get("label_source", "teacher_llm")),
            raw_output=str(payload.get("raw_output", "")),
        )

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["saturation"] = max(0.0, min(1.0, float(self.saturation)))
        return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL row") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: JSONL row must be an object")
            rows.append(payload)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            file.write("\n")
