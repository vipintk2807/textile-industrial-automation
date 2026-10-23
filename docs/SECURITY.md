# Security

| Topic | Behaviour |
|---|---|
| Network exposure | Streamlit binds to `127.0.0.1` (`.streamlit/config.toml`) and usage statistics are disabled. The app has no authentication: **do not expose it to a network** without adding a reverse proxy with authentication. |
| Outbound connections | None. No telemetry, no CDN fonts/scripts, no update checks. Dependencies are installed once from PyPI by you. |
| Read-only | No machine-control, setpoint or register-write function exists; static tests enforce this. Fault injection works **only** on simulator adapters and is refused otherwise. |
| Secrets | Plain-text secret-like keys (`password`, `token`, `secret`, `api_key`, `credential`, `private_key`) in adapter settings or source mappings are **rejected** by configuration validation (and never echoed). Redaction is applied to events, logs and the persisted device configuration. Future adapters must read credentials from environment variables. |
| Input safety | Device-supplied strings are control-character-stripped, length-limited and HTML-escaped before display; Streamlit tables render text. CSV exports neutralise spreadsheet-formula prefixes. All SQL goes through SQLAlchemy with bound parameters. |
| Files | Database and logs live under `%LOCALAPPDATA%` (or your override), not in the source folder. `config\devices.toml`, `*.db` and `logs/` are Git-ignored. |
| Dependencies | Declared with version ranges. Run `pip list --outdated` periodically and `pip-audit` (free) before publishing a release. |
| Private documents | The planning documents used to create this project are **not** included and are listed in `.gitignore`. |

Report suspected vulnerabilities privately to the repository owner before publishing details.
