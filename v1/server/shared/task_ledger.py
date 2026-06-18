from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb

from server.shared.inference.trace import chat_stream_structured_with_trace_role
from server.shared.models import SessionEvent, TaskLedgerEntry, TransitionResult

logger = logging.getLogger(__name__)

TaskLedgerIntentKind = Literal["create", "complete", "unsupported"]
TaskLedgerUpdateStatus = Literal[
    "created",
    "completed",
    "skipped",
    "needs_confirmation",
    "unsupported",
]
TASK_LEDGER_EXTRACTION_MAX_TOKENS = 220
TASK_LEDGER_MIN_COMPLETION_CONFIDENCE = 0.72


class TaskLedgerStore(Protocol):
    async def upsert(
        self,
        *,
        task_id: str,
        title: str,
        status: str = "active",
        priority: int = 0,
        due_at: datetime | None = None,
        source: str = "unknown",
        details: str = "",
        tags: tuple[str, ...] = (),
    ) -> None: ...

    async def read_active_tasks(self, *, limit: int) -> list[TaskLedgerEntry]: ...

    async def complete_task(self, *, task_id: str) -> bool: ...


@dataclass(frozen=True)
class TaskLedgerIntent:
    kind: TaskLedgerIntentKind
    raw_text: str
    title: str = ""
    reason: str = ""


@dataclass(frozen=True)
class TaskLedgerCompletionCandidate:
    task_id: str
    confidence: float
    reason: str = ""


@dataclass(frozen=True)
class TaskLedgerCompletionExtraction:
    candidates: tuple[TaskLedgerCompletionCandidate, ...]
    decision: Literal["complete", "unclear"] = "unclear"
    reason: str = ""


@dataclass(frozen=True)
class TaskLedgerUpdateResult:
    status: TaskLedgerUpdateStatus
    operation: TaskLedgerIntentKind
    task_id: str | None = None
    title: str = ""
    reason: str = ""
    candidate_ids: tuple[str, ...] = ()


class TaskLedgerIntentDetector:
    def detect(self, text: str) -> TaskLedgerIntent | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        if _has_unsupported_task_cue(normalized):
            return TaskLedgerIntent(
                kind="unsupported",
                raw_text=text,
                title=_strip_task_cues(normalized),
                reason="update_cancel_not_supported",
            )
        if _has_completion_cue(normalized):
            return TaskLedgerIntent(
                kind="complete",
                raw_text=text,
                title=_strip_completion_cues(normalized),
                reason="completion_cue",
            )
        if _has_create_cue(normalized):
            title = _strip_create_cues(normalized)
            if title:
                return TaskLedgerIntent(
                    kind="create",
                    raw_text=text,
                    title=title,
                    reason="create_cue",
                )
        return None


class TaskLedgerCompletionExtractor:
    def __init__(self, backend: Any | None) -> None:
        self.backend = backend

    async def extract(
        self,
        *,
        transcript_text: str,
        active_tasks: list[TaskLedgerEntry],
    ) -> TaskLedgerCompletionExtraction:
        if self.backend is None or not hasattr(self.backend, "chat_stream_structured"):
            return TaskLedgerCompletionExtraction(
                candidates=(),
                decision="unclear",
                reason="structured_backend_unavailable",
            )
        try:
            system_prompt = _completion_extraction_system_prompt()
            messages = [
                {
                    "role": "user",
                    "content": _completion_extraction_user_prompt(
                        transcript_text=transcript_text,
                        active_tasks=active_tasks,
                    ),
                }
            ]
            logger.info(
                "task ledger completion extraction llm_prompt backend=%s payload=%s",
                getattr(self.backend, "name", "unknown"),
                json.dumps(
                    {
                        "system_prompt": system_prompt,
                        "messages": messages,
                        "max_tokens": TASK_LEDGER_EXTRACTION_MAX_TOKENS,
                    },
                    ensure_ascii=False,
                ),
            )
            raw_json = "".join(
                [
                    chunk
                    async for chunk in chat_stream_structured_with_trace_role(
                        self.backend,
                        system_prompt,
                        messages,
                        json_schema=_completion_extraction_schema(),
                        max_tokens=TASK_LEDGER_EXTRACTION_MAX_TOKENS,
                        trace_role="task_ledger_completion",
                    )
                ]
            )
            return _parse_completion_extraction(raw_json)
        except Exception:
            logger.warning("task ledger completion extraction failed", exc_info=True)
            return TaskLedgerCompletionExtraction(
                candidates=(),
                decision="unclear",
                reason="structured_extraction_failed",
            )


class TaskLedgerCommandRunner:
    def __init__(
        self,
        *,
        store: TaskLedgerStore | None,
        backend_provider: Any | None = None,
        session: Any | None = None,
    ) -> None:
        self.store = store
        self.backend_provider = backend_provider
        self.session = session

    async def run_result(self, result: TransitionResult) -> None:
        for command in result.commands:
            if command.type != "submit_task_ledger_update":
                continue
            intent = command.payload.get("intent")
            if not isinstance(intent, TaskLedgerIntent):
                update_result = TaskLedgerUpdateResult(
                    status="skipped",
                    operation="unsupported",
                    reason="invalid_intent",
                )
            else:
                update_result = await self.run(intent)
            if self.session is None:
                continue
            transition = await self.session.post_event(
                SessionEvent(
                    type="task_ledger_update_finished",
                    payload={
                        "request_id": command.payload.get("request_id"),
                        "result": update_result,
                    },
                )
            )
            await self.session.send_transition_emissions(transition)
            await self.session._run_internal_commands(transition.commands)

    async def run(self, intent: TaskLedgerIntent) -> TaskLedgerUpdateResult:
        if self.store is None:
            return TaskLedgerUpdateResult(
                status="skipped",
                operation=intent.kind,
                title=intent.title,
                reason="missing_task_ledger_store",
            )
        if intent.kind == "unsupported":
            return TaskLedgerUpdateResult(
                status="unsupported",
                operation=intent.kind,
                title=intent.title,
                reason=intent.reason or "unsupported_operation",
            )
        if intent.kind == "create":
            return await self._create_task(intent)
        if intent.kind == "complete":
            return await self._complete_task(intent)
        return TaskLedgerUpdateResult(
            status="skipped",
            operation=intent.kind,
            title=intent.title,
            reason="unknown_intent",
        )

    async def _create_task(self, intent: TaskLedgerIntent) -> TaskLedgerUpdateResult:
        title = intent.title.strip()
        if not title:
            return TaskLedgerUpdateResult(
                status="skipped",
                operation="create",
                reason="empty_title",
            )
        task_id = make_task_id(title)
        await self.store.upsert(
            task_id=task_id,
            title=title,
            status="active",
            priority=50,
            source="voice",
        )
        return TaskLedgerUpdateResult(
            status="created",
            operation="create",
            task_id=task_id,
            title=title,
            reason="created_from_transcript",
        )

    async def _complete_task(self, intent: TaskLedgerIntent) -> TaskLedgerUpdateResult:
        active_tasks = await self.store.read_active_tasks(limit=50)
        if not active_tasks:
            return TaskLedgerUpdateResult(
                status="skipped",
                operation="complete",
                title=intent.title,
                reason="no_active_tasks",
            )
        exact = _match_active_task(intent.title, active_tasks)
        if exact is not None:
            completed = await self.store.complete_task(task_id=exact.task_id)
            return TaskLedgerUpdateResult(
                status="completed" if completed else "skipped",
                operation="complete",
                task_id=exact.task_id,
                title=exact.title,
                reason="deterministic_match" if completed else "complete_failed",
            )
        backend = await _maybe_call_backend_provider(self.backend_provider)
        extraction = await TaskLedgerCompletionExtractor(backend).extract(
            transcript_text=intent.raw_text,
            active_tasks=active_tasks,
        )
        candidate = _validate_completion_extraction(extraction, active_tasks)
        if candidate is None:
            return TaskLedgerUpdateResult(
                status="needs_confirmation",
                operation="complete",
                title=intent.title,
                reason=extraction.reason or "ambiguous_completion_target",
                candidate_ids=tuple(candidate.task_id for candidate in extraction.candidates),
            )
        completed = await self.store.complete_task(task_id=candidate.task_id)
        return TaskLedgerUpdateResult(
            status="completed" if completed else "skipped",
            operation="complete",
            task_id=candidate.task_id,
            title=candidate.title,
            reason="structured_match" if completed else "complete_failed",
        )


@dataclass(frozen=True)
class StoredTaskLedgerEntry:
    task_id: str
    title: str
    status: str = "active"
    priority: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    due_at: datetime | None = None
    source: str = "unknown"
    details: str = ""
    tags: tuple[str, ...] = ()
    completed_at: datetime | None = None

    def to_context_entry(self) -> TaskLedgerEntry:
        now = datetime.now(UTC)
        return TaskLedgerEntry(
            task_id=self.task_id,
            title=self.title,
            status=_task_status(self.status),
            priority=self.priority,
            created_at=self.created_at or now,
            updated_at=self.updated_at or self.created_at or now,
            due_at=self.due_at,
            source=self.source,
            details=self.details,
            tags=self.tags,
        )


class InMemoryTaskLedgerStore:
    def __init__(self) -> None:
        self.rows: dict[str, StoredTaskLedgerEntry] = {}

    async def upsert(
        self,
        *,
        task_id: str,
        title: str,
        status: str = "active",
        priority: int = 0,
        due_at: datetime | None = None,
        source: str = "unknown",
        details: str = "",
        tags: tuple[str, ...] = (),
    ) -> None:
        now = datetime.now(UTC)
        existing = self.rows.get(task_id)
        self.rows[task_id] = StoredTaskLedgerEntry(
            task_id=task_id,
            title=title,
            status=status,
            priority=priority,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            due_at=due_at,
            source=source,
            details=details,
            tags=tags,
        )

    async def read_active_tasks(self, *, limit: int) -> list[TaskLedgerEntry]:
        entries = [
            row.to_context_entry()
            for row in self.rows.values()
            if row.status == "active"
        ]
        entries.sort(key=lambda item: (-item.priority, item.due_at or item.updated_at))
        return entries[:limit]

    async def complete_task(self, *, task_id: str) -> bool:
        row = self.rows.get(task_id)
        if row is None or row.status != "active":
            return False
        now = datetime.now(UTC)
        self.rows[task_id] = StoredTaskLedgerEntry(
            task_id=row.task_id,
            title=row.title,
            status="completed",
            priority=row.priority,
            created_at=row.created_at,
            updated_at=now,
            due_at=row.due_at,
            source=row.source,
            details=row.details,
            tags=row.tags,
            completed_at=now,
        )
        return True


class PostgresTaskLedgerStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def upsert(
        self,
        *,
        task_id: str,
        title: str,
        status: str = "active",
        priority: int = 0,
        due_at: datetime | None = None,
        source: str = "unknown",
        details: str = "",
        tags: tuple[str, ...] = (),
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO task_ledger_entries (
                        id,
                        title,
                        status,
                        priority,
                        due_at,
                        source,
                        details,
                        tags,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                    ON CONFLICT (id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        priority = EXCLUDED.priority,
                        due_at = EXCLUDED.due_at,
                        source = EXCLUDED.source,
                        details = EXCLUDED.details,
                        tags = EXCLUDED.tags,
                        updated_at = now()
                    """,
                    (
                        task_id,
                        title,
                        _task_status(status),
                        priority,
                        due_at,
                        source,
                        details,
                        Jsonb(list(tags)),
                    ),
                )

    async def read_active_tasks(self, *, limit: int) -> list[TaskLedgerEntry]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        status,
                        priority,
                        created_at,
                        updated_at,
                        due_at,
                        source,
                        details,
                        tags
                    FROM task_ledger_entries
                    WHERE status = 'active'
                    ORDER BY priority DESC, due_at NULLS LAST, updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()

        return [
            TaskLedgerEntry(
                task_id=task_id,
                title=title,
                status=_task_status(status),
                priority=priority,
                created_at=created_at,
                updated_at=updated_at,
                due_at=due_at,
                source=source,
                details=details or "",
                tags=tuple(tags or ()),
            )
            for (
                task_id,
                title,
                status,
                priority,
                created_at,
                updated_at,
                due_at,
                source,
                details,
                tags,
            ) in rows
        ]

    async def complete_task(self, *, task_id: str) -> bool:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE task_ledger_entries
                    SET
                        status = 'completed',
                        completed_at = now(),
                        updated_at = now()
                    WHERE id = %s
                      AND status = 'active'
                    """,
                    (task_id,),
                )
                return cur.rowcount == 1


def _task_status(value: str) -> str:
    if value in {"active", "completed", "cancelled", "blocked"}:
        return value
    return "active"


def make_task_id(title: str) -> str:
    digest = hashlib.sha1(_normalize_for_match(title).encode("utf-8")).hexdigest()
    return f"task-{digest[:16]}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_for_match(text: str) -> str:
    return re.sub(r"[\s、。,.・「」『』（）()\\[\\]【】]+", "", text).lower()


def _has_create_cue(text: str) -> bool:
    cues = (
        "タスクにして",
        "タスクとして",
        "タスク追加",
        "タスクに追加",
        "やることに入れて",
        "やることに追加",
        "todoに追加",
        "todo に追加",
    )
    return any(cue in text.lower() for cue in cues)


def _has_completion_cue(text: str) -> bool:
    cues = ("終わった", "完了", "done", "片付いた", "済んだ", "済ませた")
    return any(cue in text.lower() for cue in cues)


def _has_unsupported_task_cue(text: str) -> bool:
    if not _looks_task_related(text):
        return False
    cues = (
        "キャンセル",
        "取り消",
        "消して",
        "やめた",
        "変更",
        "修正",
        "更新",
        "リネーム",
    )
    return any(cue in text.lower() for cue in cues)


def _looks_task_related(text: str) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in ("タスク", "todo", "やること"))


def _strip_create_cues(text: str) -> str:
    stripped = _strip_wake_name(text)
    replacements = (
        "タスクにして",
        "タスクとして覚えて",
        "タスクとして",
        "タスクに追加して",
        "タスクに追加",
        "タスク追加",
        "やることに入れて",
        "やることに追加して",
        "やることに追加",
        "todo に追加して",
        "todo に追加",
        "todoに追加して",
        "todoに追加",
        "お願い",
    )
    for cue in replacements:
        stripped = stripped.replace(cue, " ")
    return _strip_edge_particles(_normalize_text(stripped).strip(" 、。"))


def _strip_completion_cues(text: str) -> str:
    stripped = _strip_wake_name(text)
    for cue in ("終わった", "完了した", "完了", "done", "片付いた", "済んだ", "済ませた"):
        stripped = stripped.replace(cue, " ")
    return _strip_edge_particles(_normalize_text(stripped).strip(" 、。"))


def _strip_task_cues(text: str) -> str:
    stripped = _strip_wake_name(text)
    for cue in ("タスク", "todo", "やること"):
        stripped = stripped.replace(cue, " ")
    return _strip_edge_particles(_normalize_text(stripped).strip(" 、。"))


def _strip_wake_name(text: str) -> str:
    stripped = text
    for cue in ("ともこ", "トモコ", "智子"):
        stripped = stripped.replace(cue, " ")
    return _normalize_text(stripped)


def _strip_edge_particles(text: str) -> str:
    stripped = text.strip(" 、。")
    stripped = re.sub(r"^(を|は|が|の|この|その|あの)\s*", "", stripped)
    stripped = re.sub(r"\s*(を|は|が|の|だ|です|して|お願い)$", "", stripped)
    return stripped.strip(" 、。")


def _match_active_task(
    title_fragment: str,
    active_tasks: list[TaskLedgerEntry],
) -> TaskLedgerEntry | None:
    fragment = _normalize_for_match(title_fragment)
    if not fragment:
        return None
    matches = [
        task
        for task in active_tasks
        if fragment == _normalize_for_match(task.title)
        or fragment in _normalize_for_match(task.title)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


async def _maybe_call_backend_provider(provider: Any | None) -> Any | None:
    if provider is None:
        return None
    if callable(provider):
        result = provider()
        if hasattr(result, "__await__"):
            return await result
        return result
    return provider


def _validate_completion_extraction(
    extraction: TaskLedgerCompletionExtraction,
    active_tasks: list[TaskLedgerEntry],
) -> TaskLedgerEntry | None:
    if extraction.decision != "complete" or len(extraction.candidates) != 1:
        return None
    candidate = extraction.candidates[0]
    if candidate.confidence < TASK_LEDGER_MIN_COMPLETION_CONFIDENCE:
        return None
    active_by_id = {task.task_id: task for task in active_tasks}
    return active_by_id.get(candidate.task_id)


def _completion_extraction_system_prompt() -> str:
    return (
        "You identify which existing active task the user says is completed.\n"
        "Return only task IDs from the provided active task list.\n"
        "Do not create, update, cancel, or invent tasks.\n"
        "If the target is ambiguous, missing, or multiple tasks match, return "
        "decision='unclear' and no candidates."
    )


def _completion_extraction_user_prompt(
    *,
    transcript_text: str,
    active_tasks: list[TaskLedgerEntry],
) -> str:
    tasks = [
        {
            "id": task.task_id,
            "title": task.title,
            "priority": task.priority,
            "details": task.details,
            "tags": list(task.tags),
        }
        for task in active_tasks
    ]
    return json.dumps(
        {
            "transcript": transcript_text,
            "active_tasks": tasks,
        },
        ensure_ascii=False,
    )


def _completion_extraction_schema() -> dict[str, Any]:
    return {
        "name": "task_ledger_completion",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision": {"type": "string", "enum": ["complete", "unclear"]},
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "task_id": {"type": "string"},
                            "confidence": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["task_id", "confidence", "reason"],
                    },
                },
                "reason": {"type": "string"},
            },
            "required": ["decision", "candidates", "reason"],
        },
    }


def _parse_completion_extraction(raw_json: str) -> TaskLedgerCompletionExtraction:
    payload = json.loads(raw_json)
    decision = str(payload.get("decision") or "unclear")
    if decision not in {"complete", "unclear"}:
        decision = "unclear"
    candidates = []
    for item in payload.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(
            TaskLedgerCompletionCandidate(
                task_id=str(item.get("task_id") or ""),
                confidence=float(item.get("confidence") or 0.0),
                reason=str(item.get("reason") or ""),
            )
        )
    return TaskLedgerCompletionExtraction(
        candidates=tuple(candidate for candidate in candidates if candidate.task_id),
        decision=decision,  # type: ignore[arg-type]
        reason=str(payload.get("reason") or ""),
    )
