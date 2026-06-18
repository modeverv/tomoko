from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from server.shared.models import CancelPolicy, PromptRequest


@dataclass(slots=True)
class PromptLifecycleManager:
    active: dict[UUID, PromptRequest] = field(default_factory=dict)
    discarded: set[UUID] = field(default_factory=set)

    def add(self, request: PromptRequest) -> None:
        self.active[request.id] = request

    def cancel_for_user_speaking(self) -> list[UUID]:
        return self._cancel_by_policy(CancelPolicy.CANCEL_ON_USER_SPEAKING)

    def cancel_for_stop(self) -> list[UUID]:
        return self._cancel_by_policy(CancelPolicy.CANCEL_ON_STOP)

    def cancel_for_final_divergence(
        self,
        request_id: UUID,
        *,
        provisional: str,
        final: str,
    ) -> bool:
        request = self.active.get(request_id)
        if request is None or request.cancel_policy != CancelPolicy.CANCEL_ON_FINAL_DIVERGENCE:
            return False
        if final.startswith(provisional):
            return False
        self.discarded.add(request_id)
        self.active.pop(request_id, None)
        return True

    def _cancel_by_policy(self, policy: CancelPolicy) -> list[UUID]:
        cancelled: list[UUID] = []
        for request_id, request in list(self.active.items()):
            if request.cancel_policy == policy:
                cancelled.append(request_id)
                self.discarded.add(request_id)
                self.active.pop(request_id, None)
        return cancelled
