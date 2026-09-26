"""Collect paired, synthetic-only labels without access to reference labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from server.tomoko.semantic import saturation_prompt  # noqa: E402

SEED = 20260927
GEMMA_MODEL = str(REPO / "v1/loras/lora/fused_model")
CODEX_MODEL = "gpt-6-astra"
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["labels"],
    "properties": {"labels": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "score"],
        "properties": {"id": {"type": "string"},
                       "score": {"type": "number", "minimum": 0, "maximum": 1}},
    }}},
}


def batch_prompt(rows: list[dict[str, Any]]) -> str:
    """Project away all corpus metadata before either teacher sees the data."""
    rubric = saturation_prompt("").removesuffix("TEXT=")
    inputs = [{"id": row["id"], "text": row["text"]} for row in rows]
    return (
        rubric + "\n以下の各入力を独立した発話として判定してください。"
        "入力同士は会話ではなく、前後関係もありません。"
        "提示されていない履歴や続きを推測しないでください。\n"
        "ツール、ファイル、ネットワーク、検索は使わず、この入力だけを採点してください。\n"
        '出力は {"labels":[{"id":"入力のid","score":0.0}]} 形式のJSONのみ。'
        "各idを正確に一度ずつ出力し、説明やMarkdownは出力しないでください。\n"
        "INPUTS=" + json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    )


def ordered_batches(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if any(row["split"] not in {"train", "eval"} for row in rows):
        raise ValueError("unknown split")
    batches = []
    rng = random.Random(SEED)
    for split in ("train", "eval"):
        selected = sorted((row for row in rows if row["split"] == split), key=lambda r: r["id"])
        rng.shuffle(selected)
        batches.extend(selected[i:i + batch_size] for i in range(0, len(selected), batch_size))
    return batches


def parse_labels(content: str, expected_ids: list[str]) -> dict[str, float]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"labels"}:
        raise ValueError("expected labels object")
    if not isinstance(payload["labels"], list):
        raise ValueError("labels must be an array")
    scores = {}
    for row in payload["labels"]:
        if not isinstance(row, dict) or set(row) != {"id", "score"}:
            raise ValueError("expected id and score only")
        key, score = row["id"], row["score"]
        if not isinstance(key, str) or key in scores:
            raise ValueError("invalid or duplicate id")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("score must be finite numeric value in [0, 1]")
        scores[key] = float(score)
    if set(scores) != set(expected_ids) or len(expected_ids) != len(set(expected_ids)):
        raise ValueError("missing or extra ids")
    return {key: scores[key] for key in expected_ids}


def local_id_inputs(rows: list[dict[str, Any]]) -> tuple[list[dict[str, str]], dict[str, str]]:
    mapping = {str(index): row["id"] for index, row in enumerate(rows, 1)}
    inputs = [{"id": str(index), "text": row["text"]} for index, row in enumerate(rows, 1)]
    return inputs, mapping


def mapped_labels(content: str, mapping: dict[str, str]) -> dict[str, float]:
    scores = parse_labels(content, list(mapping))
    return {mapping[key]: score for key, score in scores.items()}


def parse_codex_trace(stdout: str) -> tuple[str, dict[str, Any]]:
    final = None
    usage = None
    for line in stdout.splitlines():
        event = json.loads(line)
        if event.get("type") in {"error", "turn.failed"}:
            raise ValueError("Codex turn failed")
        item = event.get("item")
        if item is not None:
            if item.get("type") not in {"agent_message", "reasoning"}:
                raise ValueError(f"Codex used a tool: {item.get('type')}")
            if event.get("type") == "item.completed" and item["type"] == "agent_message":
                final = item["text"]
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    if final is None or usage is None:
        raise ValueError("Codex final message or completed turn missing")
    return final, usage


def codex_command(workspace: Path, schema_path: Path) -> list[str]:
    return [
        "codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
        "-C", str(workspace), "-m", CODEX_MODEL, "-c", 'model_reasoning_effort="medium"',
        "--json", "--output-schema", str(schema_path), "-",
    ]


def run_batch(prompt: str, teacher: str, raw: dict[str, Any], workspace: Path) -> str:
    if teacher == "gemma":
        payload = {
            "model": GEMMA_MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 1600,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        raw["request"] = payload
        response = httpx.post(
            "http://127.0.0.1:8084/v1/chat/completions", json=payload, timeout=180,
        )
        raw.update(status_code=response.status_code, response_text=response.text)
        response.raise_for_status()
        result = response.json()
        raw["usage"] = result.get("usage", {})
        return result["choices"][0]["message"]["content"]
    command = codex_command(workspace, workspace / "schema.json")
    raw["command"] = command
    try:
        result = subprocess.run(
            command, input=prompt, text=True, capture_output=True, timeout=600, check=False,
            cwd=workspace,
        )
    except subprocess.TimeoutExpired as exc:
        raw.update(stdout=str(exc.stdout or ""), stderr=str(exc.stderr or ""))
        raise
    raw.update(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)
    if result.returncode:
        raise RuntimeError(f"Codex exited with status {result.returncode}")
    content, usage = parse_codex_trace(result.stdout)
    raw["usage"] = usage
    return content


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect(root: Path, teacher: str, batch_size: int) -> None:
    started = time.perf_counter()
    source = root / "corpus/inputs.jsonl"
    source_bytes = source.read_bytes()
    rows = [json.loads(line) for line in source_bytes.decode("utf-8").splitlines() if line]
    ids = [row["id"] for row in rows]
    if not rows or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
        raise ValueError("input ids must be nonempty and unique strings")
    if any(not isinstance(row["text"], str) for row in rows):
        raise ValueError("input text must be a string")
    batches = ordered_batches(rows, batch_size)
    raw_dir = root / "batches" / teacher
    raw_dir.mkdir(parents=True, exist_ok=False)
    label_dir = root / "labels"
    label_dir.mkdir(parents=True, exist_ok=True)
    destination = label_dir / f"{teacher}.jsonl"
    if destination.exists():
        raise FileExistsError(destination)
    labels, timings = [], []
    with tempfile.TemporaryDirectory(prefix="tomoko-teacher-") as scratch:
        workspace = Path(scratch)
        save_json(workspace / "schema.json", SCHEMA)
        for batch_id, batch in enumerate(batches):
            inputs, mapping = local_id_inputs(batch)
            prompt = batch_prompt(inputs)
            raw: dict[str, Any] = {"batch_id": batch_id, "prompt": prompt, "id_mapping": mapping}
            batch_started = time.perf_counter()
            try:
                content = run_batch(prompt, teacher, raw, workspace)
                scores = mapped_labels(content, mapping)
            except Exception as exc:
                raw["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                elapsed = (time.perf_counter() - batch_started) * 1000
                raw["elapsed_ms"] = elapsed
                save_json(raw_dir / f"{batch_id:03d}.json", raw)
            labels.extend({"id": key, "score": score, "elapsed_ms": elapsed / len(batch),
                           "batch_id": batch_id} for key, score in scores.items())
            timing = {"batch_id": batch_id, "count": len(batch), "elapsed_ms": elapsed}
            timings.append(timing)
            progress = json.dumps({**timing, "teacher": teacher, "labels_completed": len(labels)})
            with (raw_dir / "progress.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(progress + "\n")
            print(progress, flush=True)
    temporary = destination.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row) + "\n" for row in labels), encoding="utf-8")
    temporary.replace(destination)
    save_json(label_dir / f"{teacher}.metadata.json", {
        "teacher": teacher, "model": GEMMA_MODEL if teacher == "gemma" else CODEX_MODEL,
        "reasoning": "disabled" if teacher == "gemma" else "medium",
        "input_sha256": hashlib.sha256(source_bytes).hexdigest(), "seed": SEED,
        "prompt_id_scheme": "batch-local decimal ids mapped by explicit lookup",
        "label_count": len(labels), "batch_size": batch_size, "batches": timings,
        "label_total_ms": sum(timing["elapsed_ms"] for timing in timings),
        "wall_total_ms": (time.perf_counter() - started) * 1000,
        "timing_definition": "elapsed_ms per label is amortized batch cost, not item latency",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--teacher", choices=("gemma", "codex"), required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    collect(args.root.resolve(), args.teacher, args.batch_size)


if __name__ == "__main__":
    main()
