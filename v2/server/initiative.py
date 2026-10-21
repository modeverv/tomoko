from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class InitiativeInputs:
    silence_sec: float
    candidate_pressure: float
    user_present: bool
    p_yielding: float
    intrusion: float
    rejection: float


@dataclass(slots=True)
class InitiativeMotivationModel:
    alpha: float = 0.3
    threshold: float = 0.65
    pressures: dict[str, float] = field(
        default_factory=lambda: {
            "curiosity": 0.0,
            "teasing": 0.0,
            "attachment": 0.0,
            "unspoken": 0.0,
            "candidate": 0.0,
            "floor": 0.0,
            "intrusion": 0.0,
            "rejection": 0.0,
        }
    )

    def update(self, inputs: InitiativeInputs) -> tuple[bool, dict[str, float]]:
        targets = {
            "candidate": inputs.candidate_pressure,
            "floor": min(1.0, inputs.silence_sec / 12.0) * inputs.p_yielding,
            "intrusion": inputs.intrusion,
            "rejection": inputs.rejection,
            "attachment": 0.2 if inputs.user_present else 0.0,
            "curiosity": inputs.candidate_pressure * 0.4,
            "teasing": max(0.0, inputs.candidate_pressure - 0.5) * 0.2,
            "unspoken": min(1.0, inputs.silence_sec / 30.0) * inputs.candidate_pressure,
        }
        for key, target in targets.items():
            self.pressures[key] = self.pressures[key] * (1.0 - self.alpha) + target * self.alpha
        speakability = (
            self.pressures["candidate"]
            + self.pressures["floor"]
            + self.pressures["attachment"]
            + self.pressures["curiosity"]
            - self.pressures["intrusion"]
            - self.pressures["rejection"]
        )
        return speakability >= self.threshold, {**self.pressures, "speakability": speakability}
