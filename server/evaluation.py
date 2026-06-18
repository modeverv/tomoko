from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from server.shared.models import EvalScore, EvalTurn


@dataclass(slots=True)
class EvaluationLogger:
    path: Path

    def append_turn(self, turn: EvalTurn) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": "turn", **turn.to_dict()}, ensure_ascii=False) + "\n")

    def append_score(self, score: EvalScore) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            payload = {"kind": "score", **score.to_dict()}
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def joined_report(self, session_id: UUID) -> dict[str, object]:
        turns: dict[str, dict[str, object]] = {}
        scores: list[dict[str, object]] = []
        if not self.path.exists():
            return {"session_id": str(session_id), "turns": [], "scores": []}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            if payload.get("kind") == "turn" and payload.get("session_id") == str(session_id):
                turns[payload["id"]] = payload
            elif payload.get("kind") == "score":
                scores.append(payload)
        joined = [
            {
                **turn,
                "score": next(
                    (score for score in scores if score.get("eval_turn_id") == turn_id),
                    None,
                ),
            }
            for turn_id, turn in turns.items()
        ]
        return {"session_id": str(session_id), "turns": joined}
