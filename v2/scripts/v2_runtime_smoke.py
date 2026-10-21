from __future__ import annotations

import asyncio
import json

from server.hot_path.model_executor import create_default_real_prompt_executor
from server.shared.models import CancelPolicy, PromptRequest, PromptScope


async def _run() -> dict[str, object]:
    request = PromptRequest(
        prompt_text="トモコ、短く一言で返事して。",
        scope=PromptScope.MAIN,
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=50,
        cancel_policy=CancelPolicy.CANCEL_ON_USER_SPEAKING,
    )
    result = await create_default_real_prompt_executor().execute(request)
    return {
        "model_events": len(result.model_events),
        "audio_chunks": len(result.audio_chunks),
        "first_audio_bytes": len(result.audio_chunks[0].chunk) if result.audio_chunks else 0,
        "text": next(
            (event.text for event in result.model_events if event.event_kind == "complete"),
            "",
        ),
    }


def main() -> None:
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
