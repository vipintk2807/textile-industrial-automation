# Operations

## Start / stop
`scripts\run.bat` starts the app; `Ctrl+C` stops it. Shutdown is orderly and bounded: workers stop scheduling, adapters are closed, a SHUTDOWN
event is stored, the database is closed. Closing the browser tab does **not** stop monitoring; stopping the process does.

## Where things are
Default folder `%LOCALAPPDATA%\TextileIndustrialAutomation\`: `textile_automation.db` (+ `-wal`/`-shm` while running) and `logs\`.
Override with `TEXTILE_AUTOMATION_DATA_DIR` or `[application] data_dir` / `[storage] database_path`.

## Backup and restore
```bat
python -m textile_automation backup D:\Backups\
```
Restore: stop the app, copy the backup over `textile_automation.db`, delete any leftover `textile_automation.db-wal` / `-shm`, start the app.

## Retention
Default: keep everything. Set `retention_days` for automatic purge (hourly check; applies to measurements and events), or run
`python -m textile_automation purge --days 90`. Purges are irreversible - back up first. Estimate growth: one row per accepted measurement per poll
(e.g. 3 measurements at 1 s ≈ 260,000 rows/day).

## Upgrading
1. Stop the app. 2. Back up the database. 3. Replace the code, run `pip install -e ".[dev]"`. 4. Start: Alembic migrates automatically.
If the message says the database was created by a newer version, do not edit it; use the newer code or another file.

## Recovering from common failures
* **Config error** - fix the TOML as listed in *Configuration*, restart.
* **Device in ERROR** - read the message on *Devices*; for retryable errors press **Retry this device** after fixing the cause; otherwise fix configuration and restart.
* **Restricted mode** - fix disk space/permissions/locks; persistence resumes by itself and an event records how many measurements were not saved.
* **Slow shutdown** - an adapter call that cannot be interrupted is abandoned after the bounded shutdown time (`shutdown_timeout_s`, default 5 s) and named in the SHUTDOWN event.

## Monitoring the monitor
*Diagnostics* shows runtime status, persistence mode, dropped-measurement count, active faults, and the event history (including "events not stored").
