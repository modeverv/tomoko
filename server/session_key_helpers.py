from __future__ import annotations

from typing import Literal

CandidateRequestKind = Literal["initiative", "arrival"]


def candidate_request_id(kind: CandidateRequestKind, sequence: int) -> str:
    return f"{kind}-{sequence}"
