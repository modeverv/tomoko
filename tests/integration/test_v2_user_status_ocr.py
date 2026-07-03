from __future__ import annotations

from pathlib import Path

import pytest

from server.user_status.main import OSMetadata
from server.user_status.ocr_runtime import (
    observation_from_ocr_artifact,
    ocr_runtime_available,
)

pytestmark = pytest.mark.integration


def test_user_status_ocr_fixture_image_builds_observation(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    availability = ocr_runtime_available()
    if not (availability["vision_ocr"] or availability["tesseract"]):
        pytest.skip("Vision OCR or tesseract is required for OCR fixture integration")
    font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    if not font_path.exists():
        pytest.skip("Arial.ttf fixture font is required for stable OCR integration")

    image_path = tmp_path / "user-status-ocr-fixture.png"
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 54)
    draw.text((40, 60), "pytest failed in Codex terminal", fill="black", font=font)
    image.save(image_path)

    observation = observation_from_ocr_artifact(
        image_path,
        metadata=OSMetadata(app_name="Codex", window_title="pytest"),
        present=True,
    )

    assert observation.present is True
    assert observation.activity_label == "coding_or_terminal"
    assert observation.artifact_path == str(image_path)
    assert "pytest" in observation.visible_text.lower()
    assert "terminal" in observation.visible_text.lower()
