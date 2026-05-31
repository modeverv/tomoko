from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from server.shared.models import SessionCommand, SessionEvent, TransitionResult

ResearchMode = Literal["quick", "deep"]
ResearchStatus = Literal["pending", "running", "completed", "failed", "needs_human", "timeout"]


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    query: str
    mode: ResearchMode = "quick"
    locale: str = "ja-JP"
    recency: str | None = None

    def normalized_query(self) -> str:
        return " ".join(self.query.split())

    def validate(self) -> None:
        if not self.normalized_query():
            raise ValueError("research query must not be empty")
        if self.mode not in {"quick", "deep"}:
            raise ValueError("research mode must be quick or deep")

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.normalized_query(),
            "mode": self.mode,
            "locale": self.locale,
        }
        if self.recency is not None:
            payload["recency"] = self.recency
        return payload


@dataclass(frozen=True, slots=True)
class ResearchCitation:
    title: str
    url: str
    source: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchResult:
    status: ResearchStatus
    query: str
    provider: str = "perplexity"
    short_answer: str = ""
    bullets: tuple[str, ...] = ()
    citations: tuple[ResearchCitation, ...] = ()
    confidence: float | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider_trace_id: str | None = None
    raw_artifact_path: str | None = None
    error_reason: str | None = None

    def is_speakable(self) -> bool:
        return self.status == "completed" and bool(self.short_answer.strip())


class ResearchIntentDetector:
    def __init__(self, *, locale: str = "ja-JP") -> None:
        self.locale = locale

    def detect(self, text: str) -> ResearchRequest | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        if not _has_research_cue(normalized):
            return None
        query = _strip_research_cues(normalized)
        if not query:
            return None
        mode: ResearchMode = (
            "deep" if any(cue in normalized for cue in ("詳しく", "深く")) else "quick"
        )
        recency = (
            "latest"
            if any(cue in normalized for cue in ("最新", "今日", "最近", "今"))
            else None
        )
        request = ResearchRequest(query=query, mode=mode, locale=self.locale, recency=recency)
        request.validate()
        return request


Runner = Callable[[list[str], str, float], Awaitable[str]]


class ResearchMcpClient:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        timeout_sec: float = 120.0,
        runner: Runner | None = None,
    ) -> None:
        self.command = command
        self.timeout_sec = timeout_sec
        self.runner = runner or _run_subprocess

    async def search(self, request: ResearchRequest) -> ResearchResult:
        request.validate()
        stdin_text = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "research.search",
                    "arguments": request.to_json(),
                },
            },
            ensure_ascii=False,
        )
        try:
            stdout = await self.runner(list(self.command), stdin_text + "\n", self.timeout_sec)
        except TimeoutError as exc:
            return ResearchResult(
                status="timeout",
                query=request.normalized_query(),
                error_reason=str(exc),
            )
        except OSError as exc:
            return ResearchResult(
                status="failed",
                query=request.normalized_query(),
                error_reason=str(exc),
            )
        return parse_mcp_tool_call_response(stdout, fallback_query=request.normalized_query())


class ResearchResultSummarizer:
    def __init__(self, *, backend) -> None:
        self.backend = backend

    async def summarize(self, result: ResearchResult) -> str:
        system_prompt = (
            "あなたはTomoko用の外部調査結果を短く索引化する要約器です。"
            "会話用の返答ではなく、あとでdeep contextに混ぜる参照メモを作ってください。"
            "日本語で2文以内。引用URLは書かず、事実関係と日付感だけを残す。"
        )
        citations = "\n".join(f"- {item.title}: {item.url}" for item in result.citations)
        user_prompt = "\n".join(
            [
                f"query: {result.query}",
                f"provider: {result.provider}",
                f"fetched_at: {result.fetched_at.isoformat()}",
                f"short_answer: {result.short_answer}",
                "bullets:",
                *(f"- {item}" for item in result.bullets),
                "citations:",
                citations or "- none",
            ]
        )
        chunks: list[str] = []
        async for chunk in self.backend.chat_stream(
            system_prompt,
            [{"role": "user", "content": user_prompt}],
        ):
            chunks.append(chunk)
        summary = " ".join("".join(chunks).split())
        return summary or _fallback_research_summary_text(result)


class ResearchCommandRunner:
    def __init__(
        self,
        *,
        session,
        client: ResearchMcpClient,
        result_store=None,
        embedding_backend=None,
        summarizer=None,
    ) -> None:
        self.session = session
        self.client = client
        self.result_store = result_store
        self.embedding_backend = embedding_backend
        self.summarizer = summarizer

    async def run_result(self, result: TransitionResult) -> None:
        await self.session.send_transition_emissions(result)
        for command in result.commands:
            next_result = await self.run_command(command)
            if next_result is not None:
                await self.run_result(next_result)

    async def run_command(self, command: SessionCommand) -> TransitionResult | None:
        if command.type != "submit_research_request":
            return None
        request = command.payload.get("request")
        if not isinstance(request, ResearchRequest):
            return await self.session.post_event(
                SessionEvent(
                    type="research_result_ready",
                    payload={
                        "request_id": command.payload.get("request_id"),
                        "result": ResearchResult(
                            status="failed",
                            query="",
                            error_reason="invalid research request",
                        ),
                    },
                )
            )
        result = await self.client.search(request)
        await self._ingest_result(result)
        return await self.session.post_event(
            SessionEvent(
                type="research_result_ready",
                payload={
                    "request_id": command.payload.get("request_id"),
                    "result": result,
                },
            )
        )

    async def _ingest_result(self, result: ResearchResult) -> None:
        if (
            not result.is_speakable()
            or self.result_store is None
            or self.embedding_backend is None
            or self.summarizer is None
        ):
            return
        summary_text = await self.summarizer.summarize(result)
        embedding = await self.embedding_backend.embed_passage(summary_text)
        result_id = result.provider_trace_id or _research_result_id(result)
        await self.result_store.insert(
            result_id=result_id,
            query=result.query,
            summary_text=summary_text,
            embedding=embedding,
            provider=result.provider,
            fetched_at=result.fetched_at,
            short_answer=result.short_answer,
            citation_urls=tuple(citation.url for citation in result.citations),
            raw_artifact_path=result.raw_artifact_path,
        )


def parse_mcp_tool_call_response(
    stdout: str,
    *,
    fallback_query: str = "",
) -> ResearchResult:
    line = _last_json_line(stdout)
    if line is None:
        return ResearchResult(
            status="failed",
            query=fallback_query,
            error_reason="MCP response did not contain JSON",
        )
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        return ResearchResult(status="failed", query=fallback_query, error_reason=str(exc))
    error = payload.get("error")
    if isinstance(error, dict):
        return ResearchResult(
            status="failed",
            query=fallback_query,
            error_reason=str(error.get("message") or "MCP error"),
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        return ResearchResult(
            status="failed",
            query=fallback_query,
            error_reason="MCP result was missing",
        )
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return ResearchResult(
            status="failed",
            query=fallback_query,
            error_reason="MCP structuredContent was missing",
        )
    return _research_result_from_payload(structured, fallback_query=fallback_query)


async def _run_subprocess(command: list[str], stdin_text: str, timeout_sec: float) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_text.encode("utf-8")),
            timeout=timeout_sec,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TimeoutError(f"research MCP command timed out after {timeout_sec:.1f}s") from exc
    if process.returncode != 0:
        raise OSError(stderr.decode("utf-8", errors="replace").strip())
    return stdout.decode("utf-8", errors="replace")


def _research_result_from_payload(
    payload: dict[str, Any],
    *,
    fallback_query: str,
) -> ResearchResult:
    status = str(payload.get("status") or "failed")
    if status not in {"pending", "running", "completed", "failed", "needs_human", "timeout"}:
        status = "failed"
    fetched_at = _parse_datetime(payload.get("fetched_at"))
    return ResearchResult(
        status=status,  # type: ignore[arg-type]
        query=str(payload.get("query") or fallback_query),
        provider=str(payload.get("provider") or "perplexity"),
        short_answer=str(payload.get("short_answer") or ""),
        bullets=tuple(str(item) for item in payload.get("bullets", ()) if str(item).strip()),
        citations=_dedupe_citations(payload.get("citations")),
        confidence=_optional_float(payload.get("confidence")),
        fetched_at=fetched_at,
        provider_trace_id=_optional_str(payload.get("provider_trace_id")),
        raw_artifact_path=_optional_str(payload.get("raw_artifact_path")),
        error_reason=_optional_str(payload.get("error_reason")),
    )


def _research_result_id(result: ResearchResult) -> str:
    seed = "|".join(
        [
            result.provider,
            result.query,
            result.short_answer,
            result.fetched_at.isoformat(),
        ]
    )
    return "research-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _fallback_research_summary_text(result: ResearchResult) -> str:
    parts = [result.short_answer.strip()]
    if result.bullets:
        parts.extend(item.strip() for item in result.bullets[:2] if item.strip())
    return " ".join(part for part in parts if part)


def _dedupe_citations(raw: Any) -> tuple[ResearchCitation, ...]:
    if not isinstance(raw, list):
        return ()
    seen: set[str] = set()
    citations: list[ResearchCitation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        citations.append(
            ResearchCitation(
                title=str(item.get("title") or url),
                url=url,
                source=_optional_str(item.get("source")),
            )
        )
    return tuple(citations)


def _last_json_line(stdout: str) -> str | None:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(text: str) -> str:
    return " ".join(text.replace("　", " ").strip().split())


def _has_research_cue(text: str) -> bool:
    return any(cue in text for cue in ("調べて", "検索して", "調査して", "最新", "今どうなって"))


def is_research_answer_request(text: str, *, query: str | None = None) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    compact = normalized.replace(" ", "").strip("、。,.!?！？")
    if compact in {"うん", "はい", "お願い", "聞く", "聞きたい", "おねがい"}:
        return True
    direct_answer_cue = any(
        cue in compact
        for cue in (
            "教えて",
            "聞かせて",
            "結果",
            "内容",
            "読んで",
            "お願い",
        )
    )
    knowledge_cue = any(cue in compact for cue in ("知ってる", "わかる", "分かる"))
    if not direct_answer_cue and not knowledge_cue:
        return False
    if knowledge_cue:
        if query is None:
            return False
        return _research_query_overlaps_text(query=query, text=compact)
    if query is None:
        return True
    return True


def _research_query_overlaps_text(*, query: str, text: str) -> bool:
    query_terms = _research_query_terms(query)
    if not query_terms:
        return False
    return any(term.casefold() in text.casefold() for term in query_terms)


def _research_query_terms(query: str) -> tuple[str, ...]:
    compact = _normalize_text(query).replace(" ", "")
    for noise in (
        "今日",
        "最近",
        "最新",
        "関連",
        "ニュース",
        "短く",
        "詳しく",
        "深く",
        "について",
        "こと",
        "ある",
    ):
        compact = compact.replace(noise, " ")
    terms = [
        term.strip(" 、。,.!?！？のをがにはとも")
        for term in compact.split()
        if term.strip()
    ]
    return tuple(term for term in terms if len(term) >= 2)


def _strip_research_cues(text: str) -> str:
    query = re.sub(r"^(ともこ|トモコ|Tomoko)[、,\s]*", "", text, flags=re.IGNORECASE)
    query = query.replace("調べておいて", "")
    query = query.replace("調べといて", "")
    query = query.replace("調べて", "")
    query = query.replace("検索して", "")
    query = query.replace("調査して", "")
    query = query.replace("ください", "")
    return query.strip(" 、。,.")
