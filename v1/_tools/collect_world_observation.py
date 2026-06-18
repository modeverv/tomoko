from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def async_main(argv: list[str] | None = None) -> int:
    from server.world_observations.operator_client import (
        build_daily_world_observation_request,
        create_default_world_observation_mcp_client,
        save_world_observation_markdown,
    )

    parser = argparse.ArgumentParser(
        description="Collect a world observation artifact through tomoko-research-operator."
    )
    parser.add_argument("--date", default=_today_jst())
    parser.add_argument("--observed-at")
    parser.add_argument("--prompt", default="informations/prompts/daily_world_observation.md")
    parser.add_argument("--output-dir", default="informations/work")
    parser.add_argument("--timeout-sec", type=float)
    parser.add_argument("--summary-json")
    args = parser.parse_args(argv)

    prompt_template = Path(args.prompt).read_text(encoding="utf-8")
    request = build_daily_world_observation_request(
        prompt_template=prompt_template,
        collection_date=args.date,
        observed_at=args.observed_at,
    )
    client = create_default_world_observation_mcp_client()
    if args.timeout_sec is not None:
        client.timeout_sec = args.timeout_sec
    result = await client.observe(request)
    summary: dict[str, object] = {
        "ok": result.is_completed(),
        "status": result.status,
        "title": result.title,
        "observed_at": result.observed_at,
        "provider_trace_id": result.provider_trace_id,
        "raw_artifact_path": result.raw_artifact_path,
        "error_reason": result.error_reason,
        "output_path": None,
    }
    ok = result.is_completed()
    if ok:
        try:
            output_path = save_world_observation_markdown(
                result,
                output_dir=Path(args.output_dir),
                collection_date=args.date,
            )
        except ValueError as exc:
            ok = False
            summary["error_reason"] = str(exc)
        else:
            summary["output_path"] = str(output_path)
            print(f"world_observation_collected {output_path}")
    if not ok:
        print(
            "world_observation_collect_failed "
            f"status={result.status} error={summary['error_reason']!r}",
            file=sys.stderr,
        )
    if args.summary_json:
        Path(args.summary_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if ok else 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


def _today_jst() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()


if __name__ == "__main__":
    main()
