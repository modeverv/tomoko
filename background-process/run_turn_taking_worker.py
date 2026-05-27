from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.gateway.turn_taking.judge import RuleFirstTurnTakingJudge  # noqa: E402
from server.shared.inference.backends.mlx_lm import MLXLMBackend  # noqa: E402
from server.shared.inference.trace import (  # noqa: E402
    chat_stream_with_trace_role,
    trace_backend_call,
)
from server.shared.models import TurnTakingDecision, TurnTakingInput  # noqa: E402

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは音声対話の turn-taking 判定器です。
会話文は生成しません。次の enum JSON だけを返してください。

decision は必ず以下のどれか:
- ignore_as_noise
- continue_current_reply
- defer_output
- restart_with_new_input
- stop_speaking

判断基準:
- 空文字、息、低音量の短い音は current reply を消さない
- 明確な停止命令は stop_speaking
- 訂正、否定、長い追い発話、新しい質問は restart_with_new_input
- 短い相槌は continue_current_reply
- 話し始めた可能性が高いが未確定なら defer_output

出力例:
{"decision":"continue_current_reply","reason":"backchannel"}
"""


class WorkerJudge:
    def __init__(
        self,
        *,
        backend: MLXLMBackend | None,
        llm_timeout_ms: int,
    ) -> None:
        self.rule = RuleFirstTurnTakingJudge()
        self.backend = backend
        self.llm_timeout_ms = llm_timeout_ms

    async def judge(self, input: TurnTakingInput) -> TurnTakingDecision:
        rule_decision = await self.rule.judge(input)
        if self.backend is None or rule_decision.decision in {
            "ignore_as_noise",
            "continue_current_reply",
            "restart_with_new_input",
            "stop_speaking",
        }:
            return rule_decision
        started_at = time.perf_counter()
        request_id = f"turn-taking-worker-{time.time_ns()}"
        trace_backend_call(
            event="start",
            kind="turn_taking_judge",
            role="turn_taking_judge",
            backend=self.backend.name,
            model=self.backend.model_name,
            request_id=request_id,
            queue_key="turn_taking_worker_mlx",
        )
        try:
            payload = json.dumps(input.to_json(), ensure_ascii=False)
            text = await asyncio.wait_for(
                self._run_llm(payload),
                timeout=self.llm_timeout_ms / 1000,
            )
            decision = _parse_decision(text)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            trace_backend_call(
                event="done",
                kind="turn_taking_judge",
                role="turn_taking_judge",
                backend=self.backend.name,
                model=self.backend.model_name,
                request_id=request_id,
                queue_key="turn_taking_worker_mlx",
                total_ms=elapsed_ms,
            )
            return TurnTakingDecision(
                decision=decision.decision,
                reason=decision.reason,
                source="worker_llm",
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            trace_backend_call(
                event="timeout" if isinstance(exc, TimeoutError) else "error",
                kind="turn_taking_judge",
                role="turn_taking_judge",
                backend=self.backend.name,
                model=self.backend.model_name,
                request_id=request_id,
                queue_key="turn_taking_worker_mlx",
                total_ms=elapsed_ms,
                error=type(exc).__name__,
            )
            logger.info(
                "turn-taking worker LLM fallback reason=%s elapsed_ms=%.1f",
                type(exc).__name__,
                elapsed_ms,
            )
            return TurnTakingDecision(
                decision=rule_decision.decision,
                reason=f"llm_fallback:{rule_decision.reason}",
                source="rule_fallback",
                elapsed_ms=elapsed_ms + rule_decision.elapsed_ms,
            )

    async def _run_llm(self, payload: str) -> str:
        assert self.backend is not None
        chunks: list[str] = []
        async for chunk in chat_stream_with_trace_role(
            self.backend,
            SYSTEM_PROMPT,
            [{"role": "user", "content": payload}],
            trace_role="turn_taking_judge",
        ):
            chunks.append(chunk)
        return "".join(chunks)


def create_app(judge: WorkerJudge) -> FastAPI:
    app = FastAPI()

    @app.post("/judge")
    async def judge_turn(payload: dict[str, Any]) -> dict[str, Any]:
        input = TurnTakingInput.from_json(payload)
        decision = await judge.judge(input)
        return decision.to_json()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def run_once(args: argparse.Namespace) -> None:
    judge = _build_judge(args)
    sample = TurnTakingInput.from_json(
        {
            "pending_reply_state": "generating_not_started",
            "new_transcript": args.sample_text,
            "audio_metrics": {
                "segment_ms": 240,
                "rms_db": -38,
                "peak_db": -20,
                "active_frame_ratio": 0.42,
            },
            "attention_mode": "engaged",
            "playback_state": "idle",
            "recent_tomoko_text": "少し考えてから答えるね。",
        }
    )
    decision = await judge.judge(sample)
    print(json.dumps(decision.to_json(), ensure_ascii=False))


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=os.environ.get("TOMOKO_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.once:
        asyncio.run(run_once(args))
        return
    app = create_app(_build_judge(args))
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.uvicorn_log_level)


def _build_judge(args: argparse.Namespace) -> WorkerJudge:
    backend = None
    if not args.disable_llm:
        backend = MLXLMBackend(
            name="turn_taking_mlx",
            model=args.model,
            max_tokens=48,
        )
    return WorkerJudge(backend=backend, llm_timeout_ms=args.llm_timeout_ms)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "TOMOKO_TURN_TAKING_MODEL",
            "mlx-community/gemma-4-e2b-it-4bit",
        ),
    )
    parser.add_argument("--llm-timeout-ms", type=int, default=180)
    parser.add_argument("--disable-llm", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--sample-text", default="うん")
    parser.add_argument("--uvicorn-log-level", default="info")
    return parser.parse_args()


def _parse_decision(text: str) -> TurnTakingDecision:
    stripped = text.strip()
    try:
        return TurnTakingDecision.from_json(json.loads(stripped))
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return TurnTakingDecision.from_json(json.loads(stripped[start : end + 1]))
        raise


if __name__ == "__main__":
    main()
