from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_JUDGE_URL = "http://127.0.0.1:8081"
DEFAULT_JUDGE_MODEL = "mlx-community/gemma-4-31b-it-4bit"


def latest_scenario_artifact(logs_dir: Path) -> Path:
    candidates = sorted(
        logs_dir.glob("scenario-*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(f"no scenario artifact found in {logs_dir}")
    return candidates[-1]


def extract_transcript(artifact: Path) -> list[str]:
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    transcript: list[str] = []
    for item in payload.get("timeline", []):
        if not isinstance(item, dict):
            continue
        event_type = item.get("type")
        event_payload = item.get("payload")
        if not isinstance(event_payload, dict):
            continue
        if event_type == "transcript" and event_payload.get("is_final"):
            text = str(event_payload.get("text", "")).strip()
            if text:
                transcript.append(f"user: {text}")
        if event_type == "speech_order" and event_payload.get("mode") != "stop":
            text = str(event_payload.get("text", "")).strip()
            if text:
                transcript.append(f"tomoko: {text}")
    return transcript


def build_skip_record(
    *,
    artifact: Path,
    reason: str,
    transcript: list[str],
) -> dict[str, Any]:
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "artifact": str(artifact),
        "status": "skipped",
        "reason": reason,
        "transcript": transcript,
        "basic_checks": basic_checks(transcript),
    }


def basic_checks(transcript: list[str]) -> dict[str, Any]:
    tomoko_lines = [
        line.removeprefix("tomoko: ").strip()
        for line in transcript
        if line.startswith("tomoko: ")
    ]
    repeated = [
        text
        for index, text in enumerate(tomoko_lines[1:], start=1)
        if text and text == tomoko_lines[index - 1]
    ]
    return {
        "turns": len(transcript),
        "tomoko_turns": len(tomoko_lines),
        "adjacent_duplicate_tomoko": repeated,
    }


def judge_runtime_ready(url: str) -> bool:
    try:
        response = httpx.get(f"{url.rstrip('/')}/v1/models", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def call_judge(url: str, model: str, transcript: list[str]) -> dict[str, Any]:
    prompt = "\n".join(transcript)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict evaluator for a Japanese voice assistant named "
                    "Tomoko. Return JSON only with keys: naturalness, duplicate_speech, "
                    "missed_user_request, awkward_interruption, notes. Scores are 0..1, "
                    "where 1 means good for naturalness and bad for the issue keys."
                ),
            },
            {
                "role": "user",
                "content": f"Evaluate this transcript:\n{prompt}",
            },
        ],
        "stream": False,
        "max_tokens": 512,
        "temperature": 0.0,
    }
    response = httpx.post(
        f"{url.rstrip('/')}/v1/chat/completions",
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return parse_judge_content(content)


def parse_judge_content(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"raw": content}
    if not isinstance(parsed, dict):
        return {"raw": content}
    return parsed


def build_judge_record(
    *,
    artifact: Path,
    transcript: list[str],
    judge: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "artifact": str(artifact),
        "status": "judged",
        "transcript": transcript,
        "basic_checks": basic_checks(transcript),
        "judge": judge,
    }


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact")
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--url", default=DEFAULT_JUDGE_URL)
    parser.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--output", default="logs/llm-judge.jsonl")
    args = parser.parse_args()

    artifact = (
        Path(args.artifact)
        if args.artifact
        else latest_scenario_artifact(Path(args.logs_dir))
    )
    transcript = extract_transcript(artifact)
    if not transcript:
        record = build_skip_record(
            artifact=artifact,
            reason="empty_transcript",
            transcript=transcript,
        )
    elif not judge_runtime_ready(args.url):
        record = build_skip_record(
            artifact=artifact,
            reason="judge_runtime_unavailable",
            transcript=transcript,
        )
    else:
        record = build_judge_record(
            artifact=artifact,
            transcript=transcript,
            judge=call_judge(args.url, args.model, transcript),
        )
    append_jsonl(Path(args.output), record)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
