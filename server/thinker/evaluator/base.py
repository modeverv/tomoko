from __future__ import annotations

import abc

from server.shared.candidate import (
    CandidateSeed,
    EvaluatedUtterance,
    ThinkerEvaluationContext,
)


class UtteranceEvaluator(abc.ABC):
    @abc.abstractmethod
    async def evaluate(
        self,
        seed: CandidateSeed,
        context: ThinkerEvaluationContext,
    ) -> EvaluatedUtterance | None:
        raise NotImplementedError

