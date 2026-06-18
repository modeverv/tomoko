from __future__ import annotations

import json

from server.user_status.ocr_runtime import capture_ocr_observation_once, ocr_runtime_available


def main() -> None:
    availability = ocr_runtime_available()
    result = capture_ocr_observation_once()
    print(
        json.dumps(
            {
                "availability": availability,
                "screenshot_path": str(result.screenshot_path),
                "text_chars": len(result.text),
                "activity_label": result.activity_label,
                "present": result.present,
                "metadata": {
                    "app_name": result.metadata.app_name,
                    "window_title": result.metadata.window_title,
                    "url": result.metadata.url,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
