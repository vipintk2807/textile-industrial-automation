# Textile Industrial Automation

A local, **read-only, simulator-first** monitoring foundation for textile machinery.
It acquires measurements from *device adapters*, validates them, tracks communication state and
data quality separately, stores history in SQLite, and shows everything in a Streamlit dashboard.

> **Safety and scope.** This release monitors only. It contains **no machine-control, setpoint or
> register-write capability**, is **not** a certified industrial HMI, and does **not** replace
> machine safety systems. **Only the built-in simulator is implemented.** All values it produces are
> **synthetic** and are labelled `SIMULATED` everywhere. No physical protocol has been verified
> against real equipment (see [docs/HARDWARE_INTEGRATION.md](docs/HARDWARE_INTEGRATION.md)).

> **Verification status (read before relying on it).** The core logic is covered by 208 passing automated tests in the author's sandbox (and the database, Pydantic and Streamlit tests passed on the owner's Windows machine, see docs/VERIFICATION.md). The
> SQLite/Alembic storage layer, Pydantic configuration shape-checks and the Streamlit pages were written to spec but could not be
> *executed* where this project was built (no network, libraries missing). Run `scripts\test.bat` first; the 26 tests that were skipped
> there will run on your machine. Details: [docs/VERIFICATION.md](docs/VERIFICATION.md).

## What you get

| Capability | Status in this release |
|---|---|
| Deterministic simulator (seeded values, scripted/injectable faults, synthetic operating state) | Implemented |
| Common read-only adapter contract (open / connect / read / close) | Implemented |
| Validation pipeline (types, finite numbers, ranges, units, timestamps, missing, unknown, duplicates) | Implemented |
| Separate **communication state** (7 states), **data quality** (5 values), **machine operating state** | Implemented |
| Bounded retry with exponential backoff, ERROR after N failures, operator "Retry" | Implemented |
| Timeout guard that also protects against adapters that hang; no overlapping requests | Implemented |
| SQLite history (SQLAlchemy 2 + Alembic migrations), events, retention, online backup | Implemented |
| Restricted mode when storage fails (monitoring continues, loudly flagged, no unbounded backlog) | Implemented |
| Streamlit UI: Overview, Devices (+ details), History (gap-aware chart, CSV), Diagnostics, Configuration | Implemented |
| TOML configuration with full validation and path-specific error messages | Implemented |
| Modbus TCP (or any physical) adapter | **Deferred** - see HARDWARE_INTEGRATION.md |
| Authentication, multi-user access, remote access, alerts, OEE, control | Out of scope |

## Requirements

* Windows 10/11, **Python 3.12 or newer** ([python.org](https://www.python.org/downloads/)), internet access for the one-time install.
* No GPU, no paid service, no account. Everything is free and open source (MIT licence for this project).
* Declared dependency *ranges* (not yet pinned to a tested set) are in `pyproject.toml`: Streamlit, SQLAlchemy 2, Alembic, Pydantic 2 (runtime); pytest, Ruff, mypy (development).

## Quick start (Windows Command Prompt)

```bat
cd path\to\textile-industrial-automation
scripts\setup.bat
scripts\test.bat
scripts\run.bat
```

* `setup.bat` creates `.venv`, installs everything, and copies `config\devices.example.toml` to `config\devices.toml`.
* `test.bat` runs the automated tests. **Run this first** and read [docs/VERIFICATION.md](docs/VERIFICATION.md) (some layers were not executable where the project was built).
* `run.bat` starts the app at <http://127.0.0.1:8501> (local only). Stop it with `Ctrl+C` in the window.
* No browser? `scripts\simulate.bat` runs the simulator for 30 seconds and prints device status in the terminal.

The first run uses three simulated devices: one healthy, one that **scripts faults** (timeouts, a missing
measurement, a NaN value, a link drop) so you can watch the states change, and one disabled device.

## Using the dashboard

| Page | Purpose |
|---|---|
| **Overview** | Counts, device cards, latest measurements. A banner states when data is simulated. |
| **Devices** | Device table; pick a device for details, retry action, recent events and (simulated devices only) **Simulator test controls** to inject a synthetic fault. |
| **History** | Stored measurements for a device/measurement/time range. The chart breaks the line at gaps (no interpolation). CSV export keeps source identity. |
| **Diagnostics** | Monitoring/persistence status, active faults, rejected/missing/stale measurements, filterable event history. |
| **Configuration** | Validation status, loaded devices, data locations, scope statement. Read-only. |

### How to read the status labels

* **Communication state** - the monitoring *link*: `DISABLED`, `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `DEGRADED`, `RECOVERING`, `ERROR`.
  `CONNECTED` means the link works **and the latest read was fully accepted**. It does not mean the machine is running or safe.
* **Data quality** (per measurement) - `VALID`, `STALE` (older than `stale_after_intervals x poll_interval_s`), `INVALID`, `MISSING`, `UNKNOWN`.
  A stale or restored-from-last-session value keeps its old number but is labelled; it is never replaced by zero or a guess.
* **Machine state** - only what the source reports (the simulator reports a *synthetic* schedule). When communication is lost it shows *Unknown*, **not** "stopped".

Every status uses a text label **and** a symbol, never colour alone.

## Configuration

Edit `config\devices.toml` (a copy of `config\devices.example.toml`, ignored by Git), then restart the app.
Check a file without starting anything:

```bat
.venv\Scripts\activate.bat
python -m textile_automation validate-config --config config\devices.toml
```

Errors list **every** problem with its path, e.g. `devices[1].poll_interval_s: must be between 0.05 and 3600 (got 0)`.
Unknown keys are rejected so typos are not silently ignored. Secrets in the file are rejected; see [docs/SECURITY.md](docs/SECURITY.md).

Minimal valid example: `examples\simulator_config.toml` (use it with `set TEXTILE_AUTOMATION_CONFIG=examples\simulator_config.toml`).

Environment variables: `TEXTILE_AUTOMATION_CONFIG` (config file), `TEXTILE_AUTOMATION_DATA_DIR` (database and logs folder),
`TEXTILE_AUTOMATION_LOG_LEVEL` (`DEBUG`/`INFO`/`WARNING`/`ERROR`).

Runtime data defaults to `%LOCALAPPDATA%\TextileIndustrialAutomation\` (database `textile_automation.db`, logs in `logs\`).

## Command-line tools

```bat
python -m textile_automation validate-config --config config\devices.toml
python -m textile_automation init-db
python -m textile_automation run-simulator --seconds 30
python -m textile_automation backup D:\Backups\
python -m textile_automation purge --days 90
```

## Operations

* **Backup:** `python -m textile_automation backup <folder-or-file>` uses SQLite's online backup, safe while the app runs.
* **Retention:** by default **nothing is deleted**. Set `retention_days` in `[storage]` to purge automatically (checked hourly; each purge is recorded as an event), or run `purge --days N` manually.
* **Upgrades:** the database schema is migrated automatically with Alembic at startup. A database created by a *newer* version is refused with a clear message. Back up before upgrading.
* **Logs:** `logs\textile_automation.log` (rotating, 1 MB x 5, secrets redacted, repeated messages rate-limited).
* More in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Developing and testing

```bat
scripts\test.bat                      & rem all tests
scripts\test.bat tests\unit -q        & rem a subset
scripts\quality.bat                   & rem ruff format+check, mypy, static_check.py
```

Architecture, extension points and the requirement-to-test map are in `docs\`:
[ARCHITECTURE](docs/ARCHITECTURE.md) · [HARDWARE_INTEGRATION](docs/HARDWARE_INTEGRATION.md) ·
[SECURITY](docs/SECURITY.md) · [OPERATIONS](docs/OPERATIONS.md) · [VERIFICATION](docs/VERIFICATION.md) ·
[TRACEABILITY](docs/TRACEABILITY.md) · [PUBLISHING](docs/PUBLISHING.md).

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `setup.bat`: "py was not found" | Install Python 3.12+ from python.org with the *py launcher* option, open a **new** Command Prompt. |
| "This project needs Python 3.12 or newer" | Install 3.12+, delete the `.venv` folder, run `setup.bat` again. |
| `pip install` fails / times out | Check internet/proxy; retry. Corporate networks may need `pip config set global.proxy ...`. |
| Browser shows "Monitoring could not start" | Open **Configuration**: it lists the exact configuration or database problem. Fix it and restart. |
| "Database is not usable" / "Cannot open or initialise the database" | Folder not writable or file locked/corrupt. Set `TEXTILE_AUTOMATION_DATA_DIR` to a writable folder, or restore a backup. |
| "created by a newer or different version" | Use a matching version or point to a different database file. |
| Banner "RESTRICTED MODE" | Storage writes are failing (disk full, locked, permissions). Live values continue; history is **not** being saved. Fix the cause; it recovers automatically. |
| Port 8501 in use | `python -m streamlit run app.py --server.port 8502` |
| Everything shows STALE after restart | Expected: previous-session values are always STALE until re-acquired. |
| Windows Firewall prompt | The app binds to `127.0.0.1` only; you can decline network access. |

## Licence

MIT - see [LICENSE](LICENSE). Not certified for safety-related use.
