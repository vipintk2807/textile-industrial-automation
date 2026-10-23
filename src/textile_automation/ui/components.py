"""Thin Streamlit helpers (rendering only; all decisions come from presenters/services)."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from textile_automation.ui import presenters as P
from textile_automation.ui.theme import build_css

_BANNER_CLASS = {"info": "", "warn": " tia-warn-b", "danger": " tia-danger-b", "sim": " tia-sim-b"}


def inject_theme() -> None:
    st.markdown(f"<style>{build_css()}</style>", unsafe_allow_html=True)


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)  # callers pass only escaped/presenter output


def banners(snapshot: object) -> None:
    for spec in P.banner_specs(snapshot):
        html(
            f'<div class="tia-banner{_BANNER_CLASS[spec.kind]}" role="note">{P.esc(spec.text)}</div>'
        )


def refreshing(render: Callable[[], None], interval_s: float | None) -> None:
    """Re-render from application state on a timer. This only READS state; it never polls
    devices and never starts acquisition (acquisition runs in the runtime's own threads)."""
    if interval_s:
        st.fragment(run_every=interval_s)(render)()
    else:
        render()


def table(rows: list[dict[str, str]], empty_message: str) -> None:
    if rows:
        st.dataframe(rows, hide_index=True)
    else:
        st.info(empty_message)
