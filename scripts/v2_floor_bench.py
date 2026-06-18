from __future__ import annotations

import json

from server.floor_holding import HoldingStateMachine


def main() -> None:
    rows = []
    for pause_ms in [600, 800, 1000, 1200, 1500]:
        machine = HoldingStateMachine()
        action, score = machine.decide(
            pause_ms=pause_ms,
            desire=0.8,
            floor_available=0.9,
            fatigue=0.1,
            stop_pressure=0.0,
            user_speaking=False,
        )
        rows.append({"pause_ms": pause_ms, "action": action.value, "hold_score": score})
    print(json.dumps({"holding_bench": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
