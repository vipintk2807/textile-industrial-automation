# Requirement traceability

Status legend: **E** = implemented and covered by tests executed by the author; **W** = implemented and tests written but
**not executed** in the author's sandbox (needs your machine); **D** = intentionally deferred; **O** = out of scope.

| Requirement (from the project specifications) | Implementation | Tests | Status |
|---|---|---|---|
| Simulator-first; deterministic, labelled synthetic data | `adapters/simulator.py` | `unit/test_simulator.py` | E |
| Simulator faults: timeout, disconnect, missing, invalid, slow, connect failure, recovery, operating state | `adapters/simulator.py` (config + `inject_fault`) | `unit/test_simulator.py`, `integration/test_worker_steps.py` | E |
| Common adapter contract, read-only | `adapters/base.py`, `adapters/registry.py` | `unit/test_security_readonly.py` | E |
| Physical (Modbus TCP) adapter | - | - | D |
| Validation: type, finite, range, unit, timestamp, missing, unknown, duplicate, device mismatch | `services/validation.py`, `services/pipeline.py` | `unit/test_validation.py`, `integration/test_pipeline_persistence.py` | E |
| Failed values never become plausible data; zero ≠ missing | `validation.py`, `domain/quality.py` | validation, quality tests | E |
| Quality model incl. staleness, previous-session restore | `domain/quality.py`, `services/device_state.py` | `unit/test_states_retry_quality.py`, worker/runtime tests | E |
| Communication state machine; connected ≠ valid data | `domain/states.py`, `services/device_state.py` | states tests, `test_worker_steps.py` | E |
| Comm state / operating state / quality kept separate | tracker snapshot, presenters | worker + presenter tests | E |
| Bounded retry/backoff, ERROR, operator retry | `services/retry.py`, `services/acquisition.py` | retry tests, worker tests | E |
| Timeouts, hung adapter, no overlapping requests, failure isolation | `services/timed_call.py`, `acquisition.py` | `test_worker_steps.py::GuardTests`, `test_manager_runtime.py` | E |
| Orderly bounded shutdown; idempotent start/stop | `acquisition.py`, `runtime.py` | `test_manager_runtime.py` | E |
| Event recording, dedup, redaction, bounded memory | `services/events.py`, `redaction.py` | `unit/test_events_logging.py`, resource tests | E |
| Logging: rotation, redaction, rate limit | `logging_setup.py` | `unit/test_events_logging.py` | E |
| Persistence gate: no false success, restricted mode, no backlog/duplicates | `services/persistence.py` | `integration/test_pipeline_persistence.py`, runtime tests | E |
| SQLite schema, Alembic migration, indexes, UTC timestamps, types round-trip | `storage/*` | `integration/test_sql_storage.py` | W |
| Transactions, atomic batch, rollback, concurrent writers | `storage/repositories.py` | `test_sql_storage.py` | W |
| Restart: history survives, devices start DISCONNECTED, restored values STALE | `runtime.py` | `test_manager_runtime.py` (fake storage), `test_sql_storage.py` (real) | E / W |
| Retention, backup, schema-version refusal, unusable path error | `runtime.py`, `storage/database.py`, `cli.py` | runtime retention test (E); backup/path/version tests | E / W |
| TOML configuration: required/unknown keys, bounds, duplicates, secrets, adapter-specific rules | `config/build.py`, `adapters/simulator.py` | `unit/test_config_build.py` | E |
| Pydantic structural validation, malformed TOML message | `config/schemas.py`, `loader.py` | `unit/test_config_schemas.py` | W (file/TOML errors: E) |
| Shipped examples valid, no secrets/addresses | `config/*.toml`, `examples/` | `test_config_build.py`, `test_security_readonly.py` | E |
| UI: Overview, Devices+details, History (gap-aware), Diagnostics, Configuration | `ui/*.py`, `app.py` | `integration/test_ui_app_smoke.py` | W |
| UI logic: formatting, labels, escaping, banners, gap segmentation, CSV safety | `ui/presenters.py` | `unit/test_ui_presenters_theme.py` | E |
| Theme tokens, offline CSS, WCAG contrast, non-colour cues | `ui/theme.py` | `unit/test_ui_presenters_theme.py` | E |
| Keyboard/screen-reader/zoom/narrow-window accessibility | `ui/theme.py` (focus, reduced motion) | manual | W (manual) |
| Local-only, no telemetry, no outbound calls, no secrets in repo | `.streamlit/config.toml`, tests | `test_security_readonly.py` | E |
| Windows setup/run/test scripts, README, GitHub guide | `scripts/`, `README.md`, `docs/` | manual | W |
| Authentication, alerts, OEE, ERP/MES, control, ML | - | - | O |
