from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

REPO_ROOT = Path("/Users/seijiro/Sync/sync_work/by-llms/tomoko")
WRAPPER_PATH = REPO_ROOT / "_tools" / "run_daily_launchagent.sh"
PLIST_PATH = REPO_ROOT / "_tools" / "launchagents" / "com.tomoko.daily.plist"
README_PATH = REPO_ROOT / "_tools" / "launchagents" / "README.md"


@pytest.mark.unit
def test_daily_launchagent_wrapper_runs_make_daily_with_repo_lock() -> None:
    text = Path("_tools/run_daily_launchagent.sh").read_text()

    assert "cd \"$REPO_ROOT\"" in text
    assert "/usr/bin/make daily" in text
    assert "LOCK_DIR=\"${TMPDIR:-/tmp}/tomoko-daily.lock\"" in text
    assert "mkdir \"$LOCK_DIR\"" in text
    assert "rmdir \"$LOCK_DIR\"" in text
    assert "exec >> \"$LOG_DIR/daily-launchagent.log\" 2>&1" in text
    assert "mise/shims" in text
    assert "set +e\n/usr/bin/make daily\nstatus=$?\nset -e" in text


@pytest.mark.unit
def test_daily_launchagent_plist_points_to_wrapper_once_per_day() -> None:
    with Path("_tools/launchagents/com.tomoko.daily.plist").open("rb") as file:
        plist = plistlib.load(file)

    assert plist["Label"] == "com.tomoko.daily"
    assert plist["ProgramArguments"] == ["/bin/zsh", str(WRAPPER_PATH)]
    assert plist["WorkingDirectory"] == str(REPO_ROOT)
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert plist["RunAtLoad"] is False
    assert plist["StandardOutPath"] == "/tmp/tomoko-daily.launchd.out.log"
    assert plist["StandardErrorPath"] == "/tmp/tomoko-daily.launchd.err.log"


@pytest.mark.unit
def test_daily_launchagent_readme_documents_copy_install_commands() -> None:
    text = Path("_tools/launchagents/README.md").read_text()

    assert str(PLIST_PATH) in text
    assert str(WRAPPER_PATH) in text
    assert "cp _tools/launchagents/com.tomoko.daily.plist ~/Library/LaunchAgents/" in text
    assert "launchctl bootstrap gui/$(id -u)" in text
    assert "launchctl kickstart -k gui/$(id -u)/com.tomoko.daily" in text
