# Tomoko LaunchAgent

This directory contains a user LaunchAgent template for running `make daily`
once per day on macOS.

The LaunchAgent plist is:

```text
/Users/seijiro/Sync/sync_work/by-llms/tomoko/_tools/launchagents/com.tomoko.daily.plist
```

The wrapper script is:

```text
/Users/seijiro/Sync/sync_work/by-llms/tomoko/_tools/run_daily_launchagent.sh
```

Install manually:

```bash
mkdir -p ~/Library/LaunchAgents
cp _tools/launchagents/com.tomoko.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tomoko.daily.plist
launchctl enable gui/$(id -u)/com.tomoko.daily
```

Run once manually through launchd:

```bash
launchctl kickstart -k gui/$(id -u)/com.tomoko.daily
```

Unload:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tomoko.daily.plist
```

The default schedule is 07:30 local time. Edit `Hour` / `Minute` in the copied
plist if another time is better.

Logs:

```text
logs/daily-launchagent.log
/tmp/tomoko-daily.launchd.out.log
/tmp/tomoko-daily.launchd.err.log
```

The wrapper uses an atomic directory lock at `${TMPDIR:-/tmp}/tomoko-daily.lock`
so a long `make daily` run does not overlap with the next launch.
