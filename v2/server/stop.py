from __future__ import annotations

from dataclasses import dataclass

from server.shared.models import StopArbitration, StopStrength


def classify_stop_intent(text: str, *, explicit_ui_stop: bool = False) -> StopStrength | None:
    if explicit_ui_stop:
        return StopStrength.SYSTEM
    lowered = text.lower()
    if "黙って" in text or "stop" in lowered:
        return StopStrength.HARD
    if "ちょっと待って" in text or "wait" in lowered:
        return StopStrength.NORMAL
    if "あとで" in text:
        return StopStrength.SOFT
    return None


@dataclass(slots=True)
class ObedienceArbitrator:
    compliance_pressure: float = 0.0
    prior_disobedience_count: int = 0

    def arbitrate(
        self,
        strength: StopStrength,
        *,
        desire_score: float,
    ) -> tuple[StopArbitration, dict[str, float]]:
        if strength == StopStrength.SYSTEM:
            self.compliance_pressure = 1.0
            return StopArbitration.OBEY, {"system": 1.0}
        obey_weight = {"soft": 0.45, "normal": 0.7, "hard": 0.95}[strength.value]
        obey_score = min(
            1.0,
            obey_weight + self.compliance_pressure + self.prior_disobedience_count * 0.6,
        )
        if self.prior_disobedience_count >= 1 or obey_score >= desire_score:
            self.compliance_pressure = min(1.0, self.compliance_pressure + 0.4)
            return StopArbitration.OBEY, {
                "obey_score": obey_score,
                "desire_score": desire_score,
                "compliance_pressure": self.compliance_pressure,
            }
        self.prior_disobedience_count += 1
        self.compliance_pressure = min(1.0, self.compliance_pressure + 0.5)
        return StopArbitration.ALLOW_ONE_MORE, {
            "obey_score": obey_score,
            "desire_score": desire_score,
            "compliance_pressure": self.compliance_pressure,
        }
