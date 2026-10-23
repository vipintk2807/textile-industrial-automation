# Architecture

A modular monolith: one Python process, one SQLite file, one Streamlit UI. No cloud, no broker.

```
 Streamlit pages (ui/*)  ──read──►  AppRuntime.snapshot() / history() / stored_events()
        ▲ presenters.py (pure)               │
        │                                    ▼
 app.py ── bootstrap ──► AppRuntime ──► AcquisitionManager ──► DeviceWorker (1 thread / device)
                           │                                      │  step(): connect/read via TimedCall
                           │                                      ▼
                           │                              DeviceAdapter (SimulatorAdapter)
                           │                                      │ AdapterReadout
                           │                                      ▼
                           │                           MeasurementPipeline: restamp → validate
                           │                              │ accepted            │ rejected/missing
                           │                              ▼                     ▼
                           │                       PersistenceGate         EventService (dedup)
                           │                              │                     │
                           └────────────────────► StorageBackend (SqlStorage: SQLAlchemy + Alembic + SQLite)
```

## Layers and rules

| Package | Responsibility | Depends on |
|---|---|---|
| `domain/` | Enums, typed dataclasses, quality assessment, state-transition table | stdlib only |
| `config/` | TOML → `AppConfig`. `schemas.py` (Pydantic: shape), `build.py` (semantics), `loader.py` | domain, adapters (for adapter-specific checks) |
| `adapters/` | Read-only adapter contract, registry, simulator | domain |
| `services/` | Validation, state tracking, retry, events, persistence gate, pipeline, acquisition | domain, adapters |
| `storage/` | SQLAlchemy models, Alembic migration, `SqlStorage`, backup | domain |
| `ui/` | `theme.py`, `presenters.py` (pure), Streamlit pages | runtime (read-only) |
| `runtime.py`, `bootstrap.py`, `cli.py` | Wiring and lifecycle | everything |

Pydantic is used **only** at the configuration boundary; inside the application, plain frozen dataclasses are used,
so adapters and services do not depend on the parsing library.

## Key design decisions

1. **Three separate concepts.** Communication state (`domain/states.py`), measurement quality (`domain/quality.py`) and machine operating state are never derived from each other.
2. **Evidence-based state machine.** `next_comm_state()` is a single explicit transition table; undefined transitions raise (a defect, not silent). `CONNECTED` requires a read in which **all** configured measurements were accepted; a reconnect alone does not make a device healthy. Partial/unusable data → `DEGRADED`.
3. **Quality is decided in the service layer.** Precedence: never received → `UNKNOWN`; latest attempt `INVALID`/`MISSING` while fresh → that quality; restored from a previous session → `STALE`; older than `poll_interval × stale_after_intervals` → `STALE`; else `VALID`. A real `0` is a value; absence is `MISSING`.
4. **One pipeline for every adapter.** Adapters return raw results; the pipeline restamps `received_at` and device identity (the application owns them), validates, persists **only VALID** results, and turns everything else into deduplicated events. There is no simulator shortcut.
5. **Hard timeouts and no overlap.** Each adapter call runs in a daemon thread with `timeout + grace` (`services/timed_call.py`). If an adapter ignores its own timeout the call is abandoned; while it is still running no new request is sent to that device (cycles are skipped and counted). Shutdown closes adapters *first* to release blocked calls, then joins threads within a bounded time.
6. **Bounded retries.** Backoff `min(max, initial × multiplier^(n−1))`, deterministic (no jitter). After `max_consecutive_failures` (or a non-retryable error) the device goes to `ERROR` and stops; the operator can retry. Non-retryable: configuration, mapping, data-type, resource and unexpected errors.
7. **Failure containment.** Unexpected exceptions are caught at the worker boundary, logged with traceback, and put only that device in `ERROR`.
8. **Restricted mode.** If a write fails the batch is dropped and counted, a banner and event say history is not being saved, acquisition continues, and the next cycle tries again (a successful write is the verification). No in-memory backlog, so no unbounded growth and no duplicate rows.
9. **Restart honesty.** On start the last accepted values are loaded from the database but flagged *previous session* and always shown `STALE` until re-acquired; devices start `DISCONNECTED`.
10. **Deterministic tests.** Clocks are injected (`clock.py`); `DeviceWorker.step()` performs one cycle and returns the next delay, so most behaviour is tested without threads or sleeping.

## Data model (SQLite)

`devices`, `measurement_definitions`, `measurements` (accepted records only: value as `value_type` + `value_number`/`value_text`, unit,
source and received timestamps, quality, **source_kind**), `events` (severity, category, device, message, JSON details).
Timestamps are stored UTC and returned timezone-aware. Indexes: `(device_id, measurement_key, received_at)`, `received_at`, event time/device/category.
Alembic revision `0001` creates the schema.

## Communication-state reference

| State | Meaning |
|---|---|
| `DISABLED` | Configured `enabled = false`; never polled, no adapter created. |
| `DISCONNECTED` | No active link (start-up, never-connected device between attempts, after shutdown). |
| `CONNECTING` | First connection attempt/verification of the session. |
| `CONNECTED` | Link works and the latest read accepted every configured measurement. |
| `DEGRADED` | Link responded but some/all measurements were rejected or missing, or a read just failed. |
| `RECOVERING` | A device that worked before is reconnecting/verifying. |
| `ERROR` | Retry limit reached or non-retryable error; polling stopped until operator action. |
