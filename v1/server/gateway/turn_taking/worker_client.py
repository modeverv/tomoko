from __future__ import annotations

import logging
import time
from uuid import uuid4

import httpx

from server.gateway.turn_taking.judge import RuleFirstTurnTakingJudge, TurnTakingJudge
from server.shared.inference.trace import trace_backend_call
from server.shared.models import TurnTakingDecision, TurnTakingInput

logger = logging.getLogger(__name__)


class TurnTakingWorkerClient:
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:8765/judge",
        timeout_ms: int = 180,
        fallback: TurnTakingJudge | None = None,
    ) -> None:
        self.url = url
        self.timeout_ms = timeout_ms
        self.fallback = fallback or RuleFirstTurnTakingJudge()

    async def judge(self, input: TurnTakingInput) -> TurnTakingDecision:
        preflight_decision = await self.fallback.judge(input)
        if preflight_decision.decision != "defer_output":
            return preflight_decision

        request_id = str(uuid4())
        started_at = time.perf_counter()
        trace_backend_call(
            event="start",
            kind="turn_taking_judge",
            role="turn_taking_judge",
            backend="turn_taking_worker",
            request_id=request_id,
            queue_key="turn_taking_worker",
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
                response = await client.post(self.url, json=input.to_json())
                response.raise_for_status()
            decision = TurnTakingDecision.from_json(response.json())
            total_ms = (time.perf_counter() - started_at) * 1000
            trace_backend_call(
                event="done",
                kind="turn_taking_judge",
                role="turn_taking_judge",
                backend="turn_taking_worker",
                request_id=request_id,
                queue_key="turn_taking_worker",
                total_ms=total_ms,
            )
            return TurnTakingDecision(
                decision=decision.decision,
                reason=decision.reason,
                source="worker",
                elapsed_ms=total_ms,
            )
        except Exception as exc:
            total_ms = (time.perf_counter() - started_at) * 1000
            event = "timeout" if isinstance(exc, httpx.TimeoutException) else "error"
            trace_backend_call(
                event=event,
                kind="turn_taking_judge",
                role="turn_taking_judge",
                backend="turn_taking_worker",
                request_id=request_id,
                queue_key="turn_taking_worker",
                total_ms=total_ms,
                error=type(exc).__name__,
            )
            logger.info(
                "TurnTakingWorkerClient fallback reason=%s elapsed_ms=%.1f",
                type(exc).__name__,
                total_ms,
            )
            fallback_decision = await self.fallback.judge(input)
            return TurnTakingDecision(
                decision=fallback_decision.decision,
                reason=f"worker_{event}:{fallback_decision.reason}",
                source="rule_fallback",
                elapsed_ms=total_ms + fallback_decision.elapsed_ms,
            )
