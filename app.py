"""Streamlit entry point:  streamlit run app.py

Acquisition lives in a single AppRuntime created once per process (st.cache_resource). Page
reruns only read its state; they never open connections or start polling loops.
"""

from __future__ import annotations

import atexit
import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run app.py` without installing the package (src layout).
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from textile_automation.bootstrap import build_runtime  # noqa: E402
from textile_automation.runtime import AppRuntime, RuntimeStatus  # noqa: E402
from textile_automation.ui import dashboard, device_detail, diagnostics, history, settings  # noqa: E402
from textile_automation.ui.components import banners, inject_theme  # noqa: E402

PAGES = {
    "Overview": dashboard.render,
    "Devices": device_detail.render,
    "History": history.render,
    "Diagnostics": diagnostics.render,
    "Configuration": settings.render,
}


@st.cache_resource(show_spinner="Starting monitoring...")
def get_runtime() -> AppRuntime:
    runtime = build_runtime(console_logging=True, search_root=Path(__file__).resolve().parent)
    runtime.start()  # idempotent
    atexit.register(runtime.shutdown)  # orderly, bounded shutdown when the process exits
    return runtime


def main() -> None:
    st.set_page_config(page_title="Textile Industrial Automation", layout="wide")
    inject_theme()
    runtime = get_runtime()
    st.title("Textile Industrial Automation — monitoring")
    st.caption("Local, read-only monitoring foundation. Not a machine-control interface.")
    page = st.sidebar.radio("Navigation", list(PAGES), key="nav")
    st.sidebar.caption("Overview is always one click away.")
    if runtime.status is RuntimeStatus.FAILED and page not in ("Diagnostics", "Configuration"):
        banners(runtime.snapshot())
        st.error(
            "Monitoring could not start. Open **Configuration** for the details and fix them, "
            "then restart the application."
        )
        for issue in runtime.snapshot().startup_issues:
            st.write(f"- {issue}")
        return
    PAGES[page](runtime)


main()
