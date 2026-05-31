from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.edge.pipeline.vad import VADProcessor  # noqa: E402
from server.gateway.research import (  # noqa: E402
    ResearchCommandRunner,
    ResearchIntentDetector,
    ResearchMcpClient,
)
from server.session import TomoroSession  # noqa: E402
from server.shared.models import ConnectedOutputState, SessionEvent, Transcript  # noqa: E402


class QuietVad:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


async def run_research_smoke(
    *,
    speech_text: str,
    command: tuple[str, ...] | None = None,
    answer_followup_text: str | None = "教えて",
    timeout_sec: float = 10.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    request = ResearchIntentDetector().detect(speech_text)
    if request is None:
        raise ValueError(f"speech_text did not contain a research intent: {speech_text}")

    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVad(), silence_ms=400),
        send_event=events.append,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )

    if command is None:
        with tempfile.TemporaryDirectory(prefix="tomoko-research-mcp-") as temp_dir:
            fake_script = _write_fake_mcp_script(Path(temp_dir))
            summary = await _run_flow(
                session=session,
                events=events,
                request=request,
                command=(sys.executable, str(fake_script)),
                answer_followup_text=answer_followup_text,
                timeout_sec=timeout_sec,
            )
    else:
        summary = await _run_flow(
            session=session,
            events=events,
            request=request,
            command=command,
            answer_followup_text=answer_followup_text,
            timeout_sec=timeout_sec,
        )

    summary["speech_text"] = speech_text
    summary["detected_query"] = request.normalized_query()
    summary["detected_mode"] = request.mode
    summary["detected_locale"] = request.locale
    summary["detected_recency"] = request.recency

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


async def _run_flow(
    *,
    session: TomoroSession,
    events: list[dict[str, Any]],
    request,
    command: tuple[str, ...],
    answer_followup_text: str | None,
    timeout_sec: float,
) -> dict[str, Any]:
    accepted = await session.post_event(
        SessionEvent(type="research_requested", payload={"request": request})
    )
    runner = ResearchCommandRunner(
        session=session,
        client=ResearchMcpClient(command=command, timeout_sec=timeout_sec),
    )
    await runner.run_result(accepted)
    if answer_followup_text is not None:
        await session.process_transcript(
            Transcript(
                text=answer_followup_text,
                device_id="desk",
                speaker=None,
                audio_level_db=-20.0,
                recorded_at=datetime.now(UTC),
                is_final=True,
            )
        )

    result_events = [event for event in events if event.get("type") == "research_result_ready"]
    ready = result_events[-1] if result_events else {}
    reply_text_deltas = [
        str(event.get("delta")) for event in events if event.get("type") == "reply_text"
    ]
    return {
        "ok": bool(ready.get("speakable")),
        "command": list(command),
        "command_count": len(accepted.commands),
        "event_types": [str(event.get("type")) for event in events],
        "answer_followup_text": answer_followup_text,
        "answer_requested": any(
            event.get("type") == "research_answer_requested" for event in events
        ),
        "reply_text_deltas": reply_text_deltas,
        "reply_done_count": sum(1 for event in events if event.get("type") == "reply_done"),
        "status": ready.get("status"),
        "speakable": ready.get("speakable"),
        "notice_text": ready.get("notice_text"),
        "short_answer": ready.get("short_answer"),
        "citation_count": ready.get("citation_count"),
        "provider_trace_id": ready.get("provider_trace_id"),
        "raw_artifact_path": ready.get("raw_artifact_path"),
        "error_reason": ready.get("error_reason"),
    }


def _write_fake_mcp_script(directory: Path) -> Path:
    script = directory / "fake_research_mcp.py"
    script.write_text(
        """
from __future__ import annotations

import json
import sys

line = sys.stdin.readline()
request = json.loads(line)
arguments = request["params"]["arguments"]
query = arguments["query"]
print(json.dumps({
    "jsonrpc": "2.0",
    "id": request.get("id"),
    "result": {
        "structuredContent": {
            "status": "completed",
            "query": query,
            "provider": "fake-perplexity",
            "short_answer": f"{query} についての smoke 応答です。",
            "bullets": ["Session command から MCP subprocess まで到達しました。"],
            "citations": [
                {
                    "title": "Smoke Fixture",
                    "url": "https://example.com/tomoko-research-smoke",
                    "source": "example.com"
                }
            ],
            "confidence": 1.0,
            "provider_trace_id": "fake-trace-1",
            "raw_artifact_path": "logs/fake-research-artifact.json"
        },
        "isError": False
    }
}, ensure_ascii=False))
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the TomoroSession research command flow through a JSON-RPC MCP "
            "subprocess. Defaults to a deterministic fake MCP process."
        )
    )
    parser.add_argument(
        "--speech",
        default="ともこ、今日のOpenAI関連ニュースを短く調べて",
        help="Speech-like text that should trigger the rule-based research intent.",
    )
    parser.add_argument(
        "--command",
        default=None,
        help=(
            "Optional real MCP command, for example: "
            "'uv --directory ../tomoko-research-operator run tomoko-research-mcp'"
        ),
    )
    parser.add_argument(
        "--answer-followup",
        default="教えて",
        help=(
            "Optional follow-up text to simulate after result-ready. "
            "Use an empty string to skip answer simulation."
        ),
    )
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/research-mcp-smoke.json"),
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    command = tuple(shlex.split(args.command)) if args.command else None
    summary = await run_research_smoke(
        speech_text=args.speech,
        command=command,
        answer_followup_text=args.answer_followup or None,
        timeout_sec=args.timeout_sec,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
