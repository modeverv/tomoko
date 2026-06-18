from __future__ import annotations

import pytest

from server.journalist.main import _target_date, async_main


@pytest.mark.unit
def test_target_date_accepts_explicit_date() -> None:
    assert _target_date("2026-05-24").isoformat() == "2026-05-24"


@pytest.mark.unit
async def test_journalist_cli_help_is_available() -> None:
    with pytest.raises(SystemExit) as exc:
        await async_main(["--help"])

    assert exc.value.code == 0
