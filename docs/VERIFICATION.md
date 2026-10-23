# Verification report

Honest record of what was and was **not** verified. Update it after you run the checks on your machine.

## Build environment of the author (important)

The project was built in a sandbox with Python 3.12.3, **no network access, and none of the project's third-party dependencies
installed** (no Streamlit, SQLAlchemy, Alembic, Pydantic, pytest, Ruff, mypy). Consequently:

* Everything that is plain Python (domain, simulator, validation, state machine, retry, acquisition, events, persistence gate,
  pipeline, runtime, configuration semantics, UI presenters/theme) was **executed and tested**.
* The thin layers that need those libraries were **written to their documented APIs but not executed**: Pydantic shape
  validation, the SQLAlchemy/Alembic storage layer, the Streamlit pages and `app.py`.

## Windows run by the project owner (Python 3.14.7, Streamlit 1.66.1, SQLAlchemy 2.1.6, Alembic 1.20.1, Pydantic 2.14.1, pytest 9.1.1)

| Run | Result |
|---|---|
| First `scripts\test.bat` (230 tests) | 228 passed, 2 failed. The 18 SQLite/Alembic tests, 6 Pydantic tests and the Streamlit app-load test body passed, so those layers work on real libraries. The 2 failures were a Windows-only test cleanup error (log file still open when deleting the temp folder); `tearDown` now shuts the runtime down and closes the log first |
| Second run | 229 passed, 1 failed: a real race in `TimedCall` (busy flag used `Thread.is_alive()`, so a finished call could look busy and a cycle was wrongly skipped). Fixed; two regression tests added, one of which fails on the old code |
| Re-run after the TimedCall fix | 230 passed, 2 failed: the owner's patch command (`Set-Content -Encoding utf8` in Windows PowerShell 5.1) wrote a UTF-8 BOM into `timed_call.py`, which the two source-scanning security tests could not parse. Tests and the static checker now read source with `utf-8-sig`, the config loader accepts BOM-prefixed TOML (Notepad can add one), and regression tests cover it |
| Re-run after the BOM fix | **234 passed, 0 failed, 0 skipped** on Windows (Python 3.14.7) |
| `scripts\quality.bat` on Windows | `ruff format` reformatted 46 files (expected: code was hand-formatted). `ruff check`: 6 findings (unused import x2, unused variable, `zip()` without `strict=`, legacy generic syntax); `mypy`: 7 typing findings (None-narrowing in `quality.py` and `simulator.py`, one variable reused for two types in `config/build.py`, `.rowcount` typing in `storage/repositories.py`). None affected test results; all fixed afterwards. Re-run `scripts\quality.bat` to confirm zero findings |

Still to record from your machine: `scripts\quality.bat` (ruff/mypy), the manual walk-through of the running app, accessibility checks.

## Checks performed in the author's sandbox (no network; results observed)

| Check | Result |
|---|---|
| `python -m unittest discover -s tests` (tests are `unittest.TestCase` classes, collected by pytest too) | **234 tests: 208 passed, 26 skipped, 0 failed, 0 errors** (the 26 skipped here ran and passed on Windows apart from the issues listed above) |
| Skipped tests and why | 18 SQLite storage + 6 Pydantic-schema + 2 Streamlit smoke tests - skip automatically when their library is missing; they run when you install the dependencies |
| Threaded manager/runtime tests repeated 15 times | 15/15 runs passed (no flakiness observed) |
| Mutation spot-check 1: removed the backoff cap | Tests failed (2 failures), then code restored |
| Mutation spot-check 2: made rejected values become `0.0` | Tests failed (8 failures), then code restored |
| `python scripts/static_check.py` on all 74 Python files (undefined names, unresolved first-party imports, unused imports) | 0 problems. The checker was itself validated by injecting 2 bugs (it reported 4 problems), then restored |
| `py_compile` of every source file | OK |
| Every Streamlit page function (Overview, Devices, History, Diagnostics, Configuration, plus the failed-start variants) executed against a **sandbox-only stand-in for `streamlit`** (not shipped) with a real runtime built from the real example config, with faults firing | All pages rendered with no exception. This run found and fixed a real bug (History read a non-existent field). It validates my page logic only; **real Streamlit behaviour was not exercised** |
| Both shipped example configs (`config/devices.example.toml`, `examples/simulator_config.toml`) loaded and run through the full semantic validation | Valid |
| WCAG contrast of every text/background pair in the specified palette (computed) | All ≥ 4.5:1 (lowest: neutral badge 4.97:1); focus outline ≥ 3:1 (5.98:1) |
| Bounded-resource test: 900 cycles of sustained read failure | ≤ 40 stored events, bounded ring buffer, 1 stored measurement, no fabricated data |
| Shutdown test with a read blocked for a 30 s timeout | Completed in < 5 s, threads stopped, second shutdown is a no-op |

Defects found and fixed during development by these checks: (1) shutdown joined worker threads before closing the adapters, so a blocked
read could outlive the shutdown window (adapters are now closed first); (2) the History page read a non-existent snapshot field;
(3) the Pydantic shape validator was restructured to per-field validators so that all errors are reported together; (4) on Windows, the busy-flag race in `TimedCall` (see above), not reproducible in the Linux sandbox; (5) config files saved with a UTF-8 BOM (common with Windows editors) were rejected as invalid TOML.

## Not performed - please run these locally

| Not run | Why | How |
|---|---|---|
| Installing dependencies / `setup.bat` | No network in sandbox | `scripts\setup.bat` |
| The 26 skipped tests (SQLite storage, Alembic migration, Pydantic shape validation, Streamlit AppTest) | Libraries not installable | `scripts\test.bat` - expect **0 skipped** |
| Starting the Streamlit app and visually checking every page | No Streamlit/browser | `scripts\run.bat`; walk through Overview, Devices (inject a fault), History, Diagnostics, Configuration |
| `ruff format`, `ruff check`, `mypy` | Tools not installable in the sandbox | Run on the owner's Windows machine (see above); fixes applied, final re-run pending |
| Keyboard/focus/screen-reader/zoom/narrow-window checks, browser compatibility | No browser | Manual; see testing checklist in your testing requirements |
| Long-run (24 h+) stability and memory growth | Not feasible in sandbox | Run `run-simulator` for a day; watch process memory |
| Windows-specific behaviour (paths, `.bat` scripts, file locking) | Linux sandbox | Run the quick start on Windows 10/11 |
| Any physical device communication | No adapter, no hardware | Out of scope this release |

### Highest-risk unexecuted code (look here first if something fails)

1. `config/schemas.py` - Pydantic v2 `model_validator(mode="before")` classmethods and error-to-issue conversion.
2. `storage/database.py` + `storage/migrations/env.py` - programmatic Alembic `upgrade` with a shared connection.
3. `storage/models.py` - the `UTCDateTime` `TypeDecorator`, and agreement between `models.py` and migration `0001` (`test_schema_matches_models` checks this).
4. `ui/*.py`, `app.py` - Streamlit calls (`st.fragment(run_every=...)`, `st.dataframe(..., hide_index=True)`, `st.altair_chart`). Streamlit's API changes between releases.

If a test fails, copy the failing output into your AI assistant together with the file named above.

## Dependency pinning

`pyproject.toml` uses version **ranges**; no resolved set was tested by the author. After your first green `scripts\test.bat`,
run `scripts\freeze.bat` and commit `requirements-lock.txt`.

## Simulator vs hardware

All behaviour above is simulated. No claim is made about any real device, protocol, timing, or accuracy.
