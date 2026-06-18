from __future__ import annotations

import json

from server.initiative import InitiativeInputs, InitiativeMotivationModel


def main() -> None:
    model = InitiativeMotivationModel()
    events = []
    for silence in [1, 4, 8, 12]:
        would_fire, scores = model.update(
            InitiativeInputs(
                silence_sec=silence,
                candidate_pressure=0.8,
                user_present=True,
                p_yielding=0.9,
                intrusion=0.1,
                rejection=0.0,
            )
        )
        events.append({"silence_sec": silence, "would_initiate": would_fire, "scores": scores})
    print(json.dumps({"events": events}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
