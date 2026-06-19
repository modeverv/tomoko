from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from make_model.corpus import build_prefix_examples
from make_model.schema import CorpusUtterance, write_jsonl

JDD_REPO_URL = "https://github.com/jqk09a/japanese-daily-dialogue.git"
JDD_LICENSE = "CC BY-NC-ND 4.0"
JDD_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-nd/4.0/"
JDD_README_URL = "https://github.com/jqk09a/japanese-daily-dialogue"


@dataclass(frozen=True, slots=True)
class JapaneseDailyDialogueSummary:
    source_dir: str
    corpus_out: str
    prefixes_out: str
    manifest_out: str
    utterance_count: int
    prefix_count: int
    license: str = JDD_LICENSE
    license_url: str = JDD_LICENSE_URL
    source_repo: str = JDD_REPO_URL
    note: str = (
        "Do not commit raw data, converted corpus, teacher labels, or model artifacts. "
        "Japanese Daily Dialogue is for non-commercial research use and should not be "
        "redistributed from this repository."
    )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def load_japanese_daily_dialogue(source_dir: Path) -> list[CorpusUtterance]:
    data_dir = source_dir / "data"
    if not data_dir.exists():
        raise FileNotFoundError(f"Japanese Daily Dialogue data directory not found: {data_dir}")

    utterances: list[CorpusUtterance] = []
    for json_path in sorted(data_dir.glob("*.json")):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        for dialogue in _iter_dialogues(payload):
            topic_id = dialogue.get("topic_id") or _topic_from_payload(payload, "topic_id")
            topic_name = dialogue.get("topic_name") or _topic_from_payload(payload, "topic_name")
            dialogue_id = dialogue.get("dialogue_id")
            conversation_id = f"jdd-topic{topic_id}-{dialogue_id}"
            for utterance in _iter_utterances(dialogue):
                text = str(utterance.get("utterance") or "").strip()
                if not text:
                    continue
                turn_index = _optional_int(utterance.get("turn_num"))
                speaker = str(utterance.get("speaker") or "")
                source = (
                    "japanese-daily-dialogue:"
                    f"{json_path.name}:dialogue={dialogue_id}:turn={turn_index}"
                )
                utterance_id = f"{conversation_id}-{turn_index or len(utterances) + 1}"
                utterances.append(
                    CorpusUtterance(
                        text=text,
                        source=source,
                        utterance_id=utterance_id,
                        conversation_id=conversation_id,
                        turn_index=turn_index,
                    )
                )
                _ = topic_name, speaker
    return utterances


def convert_japanese_daily_dialogue(
    source_dir: Path,
    *,
    corpus_out: Path,
    prefixes_out: Path,
    manifest_out: Path,
    min_chars: int = 1,
    stride_chars: int = 1,
    max_prefixes_per_utterance: int | None = None,
) -> JapaneseDailyDialogueSummary:
    utterances = load_japanese_daily_dialogue(source_dir)
    prefixes = build_prefix_examples(
        utterances,
        min_chars=min_chars,
        stride_chars=stride_chars,
        include_final=True,
        max_prefixes_per_utterance=max_prefixes_per_utterance,
    )
    write_jsonl(corpus_out, (_corpus_row(utterance) for utterance in utterances))
    write_jsonl(prefixes_out, (prefix.to_json() for prefix in prefixes))
    summary = JapaneseDailyDialogueSummary(
        source_dir=str(source_dir),
        corpus_out=str(corpus_out),
        prefixes_out=str(prefixes_out),
        manifest_out=str(manifest_out),
        utterance_count=len(utterances),
        prefix_count=len(prefixes),
    )
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(
        json.dumps(summary.to_json(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _corpus_row(utterance: CorpusUtterance) -> dict[str, Any]:
    return {
        "text": utterance.text,
        "source": utterance.source,
        "utterance_id": utterance.utterance_id,
        "conversation_id": utterance.conversation_id,
        "turn_index": utterance.turn_index,
    }


def _iter_dialogues(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dialogue for dialogue in payload if isinstance(dialogue, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("dialogues"), list):
        return [
            _with_topic_defaults(dialogue, payload)
            for dialogue in payload["dialogues"]
            if isinstance(dialogue, dict)
        ]
    if isinstance(payload.get("utterances"), list):
        return [payload]
    return []


def _iter_utterances(dialogue: dict[str, Any]) -> list[dict[str, Any]]:
    utterances = dialogue.get("utterances")
    if not isinstance(utterances, list):
        return []
    return [utterance for utterance in utterances if isinstance(utterance, dict)]


def _with_topic_defaults(
    dialogue: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(dialogue)
    for key in ("topic_id", "topic_name"):
        if key not in merged and key in payload:
            merged[key] = payload[key]
    return merged


def _topic_from_payload(payload: Any, key: str) -> object:
    if isinstance(payload, dict):
        return payload.get(key)
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
