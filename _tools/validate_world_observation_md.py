from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from server.world_observations.raw_markdown import read_raw_markdown

    parser = argparse.ArgumentParser(description="Validate world observation Markdown.")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    has_error = False
    results = []
    for path in args.paths:
        document = read_raw_markdown(path)
        has_error = has_error or not document.is_valid
        results.append(
            {
                "path": path,
                "valid": document.is_valid,
                "metadata": document.metadata.to_json() if document.metadata else None,
                "issues": [issue.to_json() for issue in document.issues],
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.strict and has_error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
