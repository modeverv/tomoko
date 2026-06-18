from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import tempfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.edge.pipeline.vad import VADProcessor  # noqa: E402
from server.gateway.context import ContextSnapshotBuilder  # noqa: E402
from server.gateway.research import (  # noqa: E402
    ResearchCommandRunner,
    ResearchMcpClient,
    ResearchResultSummarizer,
)
from server.gateway.thinking.fast import ThinkFastMode  # noqa: E402
from server.session import TomoroSession  # noqa: E402
from server.shared.inference.backends.base import InferenceBackend  # noqa: E402
from server.shared.models import (  # noqa: E402
    ConnectedOutputState,
    ContextBuildPolicy,
    Transcript,
)
from server.shared.research_results import InMemoryResearchResultStore  # noqa: E402


class QuietVad:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class SmokeConversationBackend(InferenceBackend):
    name = "smoke_conversation_llm"
    privacy_allowed = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "trace_role": trace_role,
            }
        )
        if "RESPONSE DIRECTIVE" in system_prompt:
            yield "EMOTION:thinking\n調べてみるね。少し待って。"
            return
        yield "EMOTION:gentle\nうん。"


class SmokeRouter:
    def __init__(self, backend: InferenceBackend) -> None:
        self.backend = backend
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        self.selections.append((role, preference))
        return self.backend


class SmokeResearchSummaryBackend:
    name = "smoke_research_summary"
    privacy_allowed = True

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
    ):
        del system_prompt, trace_role
        content = messages[0]["content"]
        query = "unknown"
        for line in content.splitlines():
            if line.startswith("query: "):
                query = line.removeprefix("query: ")
                break
        yield f"{query} の外部調査結果をdeep context用に要約したメモです。"


class SmokeEmbeddingBackend:
    name = "smoke_embedding"
    model = "smoke"
    dimensions = 3
    privacy_allowed = True

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def embed_passage(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        lower = text.casefold()
        return [
            1.0 if "オバマ" in text or "obama" in lower else 0.1,
            1.0 if "大統領" in text else 0.1,
            1.0,
        ]


async def run_tomoro_session_research_smoke(
    *,
    speech_text: str = "智子オバマ大統領について調べて",
    answer_followup_text: str | None = "教えて",
    command: tuple[str, ...] | None = None,
    timeout_sec: float = 10.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    conversation_backend = SmokeConversationBackend()
    embedding_backend = SmokeEmbeddingBackend()
    research_store = InMemoryResearchResultStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVad(), silence_ms=400),
        send_event=events.append,
        router=SmokeRouter(conversation_backend),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(prompt_log_path=None),
        embedding_backend=embedding_backend,  # type: ignore[arg-type]
        research_result_store=research_store,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )

    async def run_with_command(real_command: tuple[str, ...]) -> dict[str, Any]:
        research_done = asyncio.Event()
        runner = ResearchCommandRunner(
            session=session,
            client=ResearchMcpClient(command=real_command, timeout_sec=timeout_sec),
            result_store=research_store,
            embedding_backend=embedding_backend,
            summarizer=ResearchResultSummarizer(backend=SmokeResearchSummaryBackend()),
        )

        async def handle_research_transition(result) -> None:
            await runner.run_result(result)
            research_done.set()

        session.set_research_transition_handler(handle_research_transition)

        await session.process_transcript(_transcript(speech_text))
        await session._wait_for_reply_task()
        await asyncio.wait_for(research_done.wait(), timeout=timeout_sec + 1.0)

        events_after_request = len(events)
        if answer_followup_text is not None:
            await session.process_transcript(_transcript(answer_followup_text))
            await session._wait_for_reply_task()
        deep_snapshot = await ContextSnapshotBuilder(
            embedding_backend=embedding_backend,  # type: ignore[arg-type]
            research_result_store=research_store,
        ).build(
            text=speech_text,
            speaker=None,
            device_id="desk",
            active_session_id=None,
            policy=ContextBuildPolicy.for_depth("deep"),
        )
        return _summary(
            events=events,
            events_after_request=events_after_request,
            command=real_command,
            speech_text=speech_text,
            answer_followup_text=answer_followup_text,
            conversation_backend=conversation_backend,
            research_store=research_store,
            deep_summaries=[hit.summary_text for hit in deep_snapshot.research_results],
        )

    if command is not None:
        summary = await run_with_command(command)
    else:
        with tempfile.TemporaryDirectory(prefix="tomoko-research-session-") as temp_dir:
            fake_script = _write_fake_mcp_script(Path(temp_dir))
            summary = await run_with_command((sys.executable, str(fake_script)))

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def _summary(
    *,
    events: list[dict[str, Any]],
    events_after_request: int,
    command: tuple[str, ...],
    speech_text: str,
    answer_followup_text: str | None,
    conversation_backend: SmokeConversationBackend,
    research_store: InMemoryResearchResultStore,
    deep_summaries: list[str],
) -> dict[str, Any]:
    request_events = events[:events_after_request]
    followup_events = events[events_after_request:]
    ready_events = [event for event in events if event.get("type") == "research_result_ready"]
    ready = ready_events[-1] if ready_events else {}
    wait_reply_text = "".join(
        str(event.get("delta"))
        for event in request_events
        if event.get("type") == "reply_text"
    )
    answer_reply_text = "".join(
        str(event.get("delta"))
        for event in followup_events
        if event.get("type") == "reply_text"
    )
    wait_prompt = (
        conversation_backend.calls[0]["system_prompt"]
        if conversation_backend.calls
        else ""
    )
    wait_messages_text = json.dumps(
        conversation_backend.calls[0]["messages"] if conversation_backend.calls else [],
        ensure_ascii=False,
    )
    wait_prompt_payload = f"{wait_prompt}\n{wait_messages_text}"
    return {
        "ok": bool(ready.get("speakable")) and bool(answer_reply_text),
        "speech_text": speech_text,
        "answer_followup_text": answer_followup_text,
        "command": list(command),
        "event_types": [str(event.get("type")) for event in events],
        "request_event_types": [str(event.get("type")) for event in request_events],
        "followup_event_types": [str(event.get("type")) for event in followup_events],
        "detected_query": ready.get("query"),
        "status": ready.get("status"),
        "speakable": ready.get("speakable"),
        "notice_text": ready.get("notice_text"),
        "short_answer": ready.get("short_answer"),
        "wait_reply_text": wait_reply_text,
        "answer_requested": any(
            event.get("type") == "research_answer_requested" for event in followup_events
        ),
        "answer_reply_text": answer_reply_text,
        "reply_done_count": sum(1 for event in events if event.get("type") == "reply_done"),
        "conversation_llm_call_count": len(conversation_backend.calls),
        "wait_prompt_has_response_directive": (
            "RESPONSE DIRECTIVE" in wait_prompt_payload
        ),
        "wait_prompt_forbids_answering": "今は調査結果を答えず"
        in wait_prompt_payload,
        "ingested_research_count": len(research_store.rows),
        "deep_research_summaries": deep_summaries,
    }


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )


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
            "short_answer": (
                f"{query} について調べたよ。"
                "バラク・オバマはアメリカ合衆国の第44代大統領です。"
            ),
            "bullets": [
                "TomoroSession transcript path から MCP subprocess まで到達しました。",
                "follow-up の「教えて」で pending result を読めます。"
            ],
            "citations": [
                {
                    "title": "Smoke Fixture",
                    "url": "https://example.com/tomoko-research-session-smoke",
                    "source": "example.com"
                }
            ],
            "confidence": 1.0,
            "provider_trace_id": "fake-session-trace-1",
            "raw_artifact_path": "logs/fake-research-session-artifact.json"
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
            "Run a TomoroSession transcript-level research smoke: request, "
            "LLM wait reply, MCP result, and teach-me follow-up."
        )
    )
    parser.add_argument(
        "--speech",
        default="智子オバマ大統領について調べて",
        help="Finalized transcript text to feed into TomoroSession.process_transcript().",
    )
    parser.add_argument(
        "--answer-followup",
        default="教えて",
        help="Follow-up transcript after research_result_ready. Empty string skips it.",
    )
    parser.add_argument(
        "--command",
        default=None,
        help=(
            "Optional real MCP command, for example: "
            "'uv --directory ../tomoko-research-operator run tomoko-research-mcp'"
        ),
    )
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/research-tomoro-session-smoke.json"),
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    command = tuple(shlex.split(args.command)) if args.command else None
    summary = await run_tomoro_session_research_smoke(
        speech_text=args.speech,
        answer_followup_text=args.answer_followup or None,
        command=command,
        timeout_sec=args.timeout_sec,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
