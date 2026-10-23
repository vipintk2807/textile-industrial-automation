"""Centralised design tokens and CSS (app theme specification). Light theme only.

No external fonts, images or scripts: the system font stack is used and nothing is fetched.
"""

from __future__ import annotations

from dataclasses import dataclass

from textile_automation.domain.quality import Quality
from textile_automation.domain.states import CommunicationState, SourceKind


@dataclass(frozen=True)
class Tokens:
    background: str = "#F4F6F8"
    surface: str = "#FFFFFF"
    surface_muted: str = "#E9EEF3"
    text_primary: str = "#17212B"
    text_secondary: str = "#526171"
    border: str = "#D5DDE5"
    accent: str = "#245EA8"
    success: str = "#217346"
    warning: str = "#946200"
    danger: str = "#B42318"
    info: str = "#176B87"
    disabled: str = "#667085"
    font_sans: str = "'Segoe UI', Arial, sans-serif"
    font_mono: str = "Consolas, 'Courier New', monospace"
    space_1: str = "4px"
    space_2: str = "8px"
    space_3: str = "12px"
    space_4: str = "16px"
    space_6: str = "24px"
    space_8: str = "32px"
    radius: str = "6px"
    border_width: str = "1px"


TOKENS = Tokens()


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two #RRGGBB colours."""
    a, b = relative_luminance(foreground), relative_luminance(background)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@dataclass(frozen=True)
class BadgeStyle:
    css_class: str
    symbol: str  # a non-colour cue; the text label is always shown as well


COMM_STYLES: dict[CommunicationState, BadgeStyle] = {
    CommunicationState.DISABLED: BadgeStyle("tia-neutral", "⊘"),
    CommunicationState.DISCONNECTED: BadgeStyle("tia-neutral", "○"),
    CommunicationState.CONNECTING: BadgeStyle("tia-info", "…"),
    CommunicationState.CONNECTED: BadgeStyle("tia-ok", "✔"),
    CommunicationState.DEGRADED: BadgeStyle("tia-warn", "▲"),
    CommunicationState.RECOVERING: BadgeStyle("tia-warn", "↻"),
    CommunicationState.ERROR: BadgeStyle("tia-danger", "✖"),
}
QUALITY_STYLES: dict[Quality, BadgeStyle] = {
    Quality.VALID: BadgeStyle("tia-ok", "✔"),
    Quality.STALE: BadgeStyle("tia-warn", "⏱"),
    Quality.INVALID: BadgeStyle("tia-danger", "✖"),
    Quality.MISSING: BadgeStyle("tia-danger", "∅"),
    Quality.UNKNOWN: BadgeStyle("tia-neutral", "?"),
}
SOURCE_STYLES: dict[SourceKind, BadgeStyle] = {
    SourceKind.SIMULATOR: BadgeStyle("tia-sim", "◇"),
    SourceKind.PHYSICAL: BadgeStyle("tia-info", "◆"),
}

# (text colour, background) pairs that the CSS actually uses; verified by tests.
TEXT_PAIRS: dict[str, tuple[str, str]] = {
    "body text": (TOKENS.text_primary, TOKENS.surface),
    "body text on page": (TOKENS.text_primary, TOKENS.background),
    "secondary text": (TOKENS.text_secondary, TOKENS.surface),
    "secondary text on page": (TOKENS.text_secondary, TOKENS.background),
    "ok badge": (TOKENS.success, TOKENS.surface),
    "warning badge": (TOKENS.warning, TOKENS.surface),
    "danger badge": (TOKENS.danger, TOKENS.surface),
    "info badge": (TOKENS.info, TOKENS.surface),
    "neutral badge": (TOKENS.disabled, TOKENS.surface),
    "accent text": (TOKENS.accent, TOKENS.surface),
    "banner text": (TOKENS.text_primary, TOKENS.surface_muted),
}
NON_TEXT_PAIRS: dict[str, tuple[str, str]] = {
    "focus outline": (TOKENS.accent, TOKENS.surface),
    "focus outline on page": (TOKENS.accent, TOKENS.background),
}


def build_css(t: Tokens = TOKENS) -> str:
    return f"""
:root {{
  --color-background: {t.background}; --color-surface: {t.surface};
  --color-surface-muted: {t.surface_muted}; --color-text-primary: {t.text_primary};
  --color-text-secondary: {t.text_secondary}; --color-border: {t.border};
  --color-accent: {t.accent}; --color-success: {t.success}; --color-warning: {t.warning};
  --color-danger: {t.danger}; --color-info: {t.info}; --color-disabled: {t.disabled};
}}
html, body, [class*="css"] {{ font-family: {t.font_sans}; }}
.tia-mono {{ font-family: {t.font_mono}; font-size: 13px; }}
.tia-badge {{ display: inline-block; padding: 2px {t.space_2}; margin: 0 {t.space_1} {t.space_1} 0;
  border: 2px solid currentColor; border-radius: {t.radius}; background: var(--color-surface);
  font-weight: 600; font-size: 13px; white-space: nowrap; }}
.tia-ok {{ color: var(--color-success); }} .tia-warn {{ color: var(--color-warning); }}
.tia-danger {{ color: var(--color-danger); }} .tia-info {{ color: var(--color-info); }}
.tia-neutral {{ color: var(--color-disabled); }}
.tia-sim {{ color: var(--color-text-primary); border-style: dashed; }}
.tia-card {{ background: var(--color-surface); border: {t.border_width} solid var(--color-border);
  border-radius: {t.radius}; padding: {t.space_4}; margin-bottom: {t.space_3};
  color: var(--color-text-primary); overflow-wrap: anywhere; }}
.tia-card h4 {{ margin: 0 0 {t.space_2} 0; font-size: 16px; font-weight: 600; }}
.tia-value {{ font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.tia-value.tia-dim {{ color: var(--color-text-secondary); }}
.tia-meta {{ color: var(--color-text-secondary); font-size: 13px; }}
.tia-banner {{ background: var(--color-surface-muted); color: var(--color-text-primary);
  border-left: 6px solid var(--color-info); padding: {t.space_3} {t.space_4};
  border-radius: {t.radius}; margin-bottom: {t.space_3}; }}
.tia-banner.tia-warn-b {{ border-left-color: var(--color-warning); }}
.tia-banner.tia-danger-b {{ border-left-color: var(--color-danger); }}
.tia-banner.tia-sim-b {{ border-left-style: dashed; }}
:focus-visible {{ outline: 3px solid var(--color-accent); outline-offset: 2px; }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important;
  transition: none !important; }} }}
"""
