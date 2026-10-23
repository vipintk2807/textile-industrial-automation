import csv
import io
import unittest
from datetime import UTC, datetime, timedelta, timezone

from textile_automation.domain.models import MeasurementResult
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import CommunicationState as S, SourceKind
from textile_automation.ui import presenters as P
from textile_automation.ui.theme import (
    COMM_STYLES,
    NON_TEXT_PAIRS,
    QUALITY_STYLES,
    TEXT_PAIRS,
    TOKENS,
    build_css,
    contrast_ratio,
)

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


class FormattingTests(unittest.TestCase):
    def test_values_with_units(self):
        self.assertEqual(P.format_value(42.5, "°C"), "42.5 °C")
        self.assertEqual(P.format_value(1240.0, "rpm"), "1,240 rpm")
        self.assertEqual(P.format_value(68.2, "%"), "68.2 %")
        self.assertEqual(P.format_value(7, "pcs"), "7 pcs")

    def test_missing_is_not_zero(self):
        self.assertEqual(P.format_value(None, "rpm"), "—")
        self.assertEqual(P.format_value(0.0, "rpm"), "0 rpm")
        self.assertNotEqual(P.format_value(None, "rpm"), P.format_value(0, "rpm"))

    def test_unknown_unit_not_invented(self):
        self.assertEqual(P.format_value(5.5, None), "5.5")

    def test_no_false_precision_and_large_numbers(self):
        self.assertEqual(P.format_value(0.1 + 0.2, None), "0.3")
        self.assertEqual(P.format_value(1e18, None), "1e+18")
        self.assertEqual(P.format_value(123456789.125, "m"), "123,456,789.125 m")

    def test_bool_and_text(self):
        self.assertEqual(P.format_value(True, None), "true")
        self.assertEqual(P.format_value("PATTERN-A", "ignored"), "PATTERN-A")

    def test_age(self):
        self.assertEqual(P.format_age(None), "never")
        self.assertEqual(P.format_age(0.5), "just now")
        self.assertEqual(P.format_age(12), "12 s ago")
        self.assertEqual(P.format_age(185), "3 min 5 s ago")
        self.assertEqual(P.format_age(7500), "2 h 5 min ago")
        self.assertEqual(P.format_age(200000), "2 d 7 h ago")

    def test_timestamp_uses_given_timezone_and_none(self):
        self.assertEqual(
            P.format_timestamp(T0, timezone(timedelta(hours=5, minutes=30))), "2026-01-01 17:30:00"
        )
        self.assertEqual(P.format_timestamp(None), "—")


class SafetyTests(unittest.TestCase):
    def test_device_text_is_escaped_not_markup(self):
        evil = "<script>alert(1)</script><img src=x onerror=y>\"'"
        out = P.esc(evil)
        self.assertNotIn("<", out)
        self.assertIn("&lt;script&gt;", out)
        badge = P.badge("tia-ok", "x", evil, evil)
        self.assertNotIn("<script", badge)
        self.assertNotIn("<img", badge)

    def test_control_characters_and_length(self):
        self.assertEqual(P.safe_text("a\x00b\x1bc"), "abc")
        self.assertEqual(len(P.safe_text("x" * 5000)), P.MAX_TEXT)

    def test_csv_formula_injection_neutralised_and_source_preserved(self):
        r = MeasurementResult(
            "=cmd|' /C calc'!A0", "speed", "=1+1", None, T0, SourceKind.SIMULATOR, Quality.VALID
        )
        text = P.to_csv([r])
        row = list(csv.reader(io.StringIO(text)))[1]
        self.assertTrue(row[0].startswith("'="))
        self.assertTrue(row[2].startswith("'="))
        self.assertEqual(row[6], "SIMULATOR")
        self.assertIn("source_kind", text.splitlines()[0])


class LabelTests(unittest.TestCase):
    def test_every_state_has_text_label_and_symbol(self):
        for state in S:
            html = P.comm_badge(state)
            self.assertIn(state.value, html)
            self.assertIn(COMM_STYLES[state].symbol, html)
            self.assertIn("aria-label", html)
        for q in Quality:
            self.assertIn(q.value, P.quality_badge(q))
            self.assertIn(QUALITY_STYLES[q].symbol, P.quality_badge(q))

    def test_status_is_not_conveyed_by_colour_alone(self):
        warn = [s for s in S if COMM_STYLES[s].css_class == "tia-warn"]
        self.assertEqual(len({COMM_STYLES[s].symbol for s in warn}), len(warn))  # distinct cues
        self.assertNotEqual(COMM_STYLES[S.CONNECTED].symbol, COMM_STYLES[S.ERROR].symbol)

    def test_simulated_is_always_labelled(self):
        self.assertIn("SIMULATED", P.source_badge(SourceKind.SIMULATOR))
        self.assertEqual(P.source_label(SourceKind.SIMULATOR), "SIMULATED")
        self.assertNotIn("live", P.source_badge(SourceKind.SIMULATOR).lower())


class SnapshotPresentationTests(unittest.TestCase):
    def test_failure_description_names_operation_device_and_retry(self):
        from tests_support import run_steps

        snap = run_steps([{"kind": "read_timeout", "start": 2, "duration": 99}], 3)
        text = P.describe_failure(snap)
        self.assertIn("Read timeout", text)
        self.assertIn("dev-1", text)
        self.assertIn("Retrying", text)

    def test_no_failure_text_when_connected(self):
        from tests_support import run_steps

        self.assertIsNone(P.describe_failure(run_steps([], 2)))

    def test_rows_show_quality_age_and_source(self):
        from tests_support import run_steps

        snap = run_steps(
            [{"kind": "read_timeout", "start": 2, "duration": 99}], 4, advance_after=10
        )
        row = P.measurement_rows(snap)[0]
        self.assertIn("STALE", row["Quality"])
        self.assertEqual(row["Source"], "SIMULATED")
        self.assertNotEqual(row["Last accepted value"], "—")  # old value kept, but labelled stale
        self.assertTrue(P.needs_attention(snap))
        drow = P.device_table_rows([snap])[0]
        self.assertIn("SIMULATED", drow["Source"])
        self.assertNotEqual(drow["Latest error"], "—")

    def test_comm_state_and_machine_state_are_separate_columns(self):
        from tests_support import run_steps

        drow = P.device_table_rows([run_steps([], 1)])[0]
        self.assertIn("CONNECTED", drow["Communication"])
        self.assertEqual(
            drow["Machine state"], "Not reported"
        )  # connectivity does not imply a state


class HistoryTests(unittest.TestCase):
    def results(self, offsets):
        return [
            MeasurementResult(
                "d",
                "k",
                float(i),
                "u",
                T0 + timedelta(seconds=o),
                SourceKind.SIMULATOR,
                Quality.VALID,
            )
            for i, o in enumerate(offsets)
        ]

    def test_gaps_break_segments_instead_of_interpolating(self):
        pts, gaps = P.segment_by_gaps(self.results([0, 1, 2, 30, 31]), max_gap_s=3)
        self.assertEqual([p.segment for p in pts], [0, 0, 0, 1, 1])
        self.assertEqual(len(gaps), 1)
        self.assertEqual((gaps[0][1] - gaps[0][0]).total_seconds(), 28)

    def test_no_gap_when_regular(self):
        pts, gaps = P.segment_by_gaps(self.results(range(10)), max_gap_s=3)
        self.assertEqual(gaps, [])
        self.assertEqual({p.segment for p in pts}, {0})

    def test_non_numeric_skipped(self):
        r = [
            MeasurementResult("d", "k", "text", None, T0, SourceKind.SIMULATOR, Quality.VALID),
            MeasurementResult("d", "k", True, None, T0, SourceKind.SIMULATOR, Quality.VALID),
        ]
        self.assertEqual(P.segment_by_gaps(r, 3)[0], [])

    def test_empty(self):
        self.assertEqual(P.segment_by_gaps([], 3), ([], []))


class ThemeTests(unittest.TestCase):
    def test_text_contrast_meets_wcag_aa(self):
        for name, (fg, bg) in TEXT_PAIRS.items():
            self.assertGreaterEqual(contrast_ratio(fg, bg), 4.5, f"{name}: {fg} on {bg}")

    def test_non_text_contrast_meets_3_to_1(self):
        for name, (fg, bg) in NON_TEXT_PAIRS.items():
            self.assertGreaterEqual(contrast_ratio(fg, bg), 3.0, name)

    def test_contrast_function_known_values(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(contrast_ratio("#777777", "#777777"), 1.0, places=3)

    def test_specified_palette_is_used(self):
        self.assertEqual(
            (TOKENS.background, TOKENS.accent, TOKENS.danger), ("#F4F6F8", "#245EA8", "#B42318")
        )

    def test_css_is_offline_and_tokenised(self):
        css = build_css()
        self.assertNotRegex(css, r"https?://")
        self.assertNotIn("@import", css)
        self.assertNotIn("url(", css)
        self.assertIn("Segoe UI", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        for token in ("--color-success", "--color-warning", "--color-danger"):
            self.assertIn(token, css)
        self.assertNotRegex(css.split("}", 1)[1], r"#[0-9A-Fa-f]{6}")  # colours only via tokens


if __name__ == "__main__":
    unittest.main()


class BannerTests(unittest.TestCase):
    def snap(self, **kw):
        from types import SimpleNamespace

        return SimpleNamespace(
            **{
                "any_simulated": False,
                "using_example_config": False,
                "persistence": None,
                "event_persist_failures": 0,
                **kw,
            }
        )

    def test_simulation_banner_present_for_simulated_devices(self):
        b = P.banner_specs(self.snap(any_simulated=True))
        self.assertEqual(b[0].kind, "sim")
        self.assertIn("synthetic", b[0].text)

    def test_no_banner_when_nothing_to_report(self):
        self.assertEqual(P.banner_specs(self.snap()), [])

    def test_restricted_mode_banner_says_history_not_saved(self):
        from textile_automation.services.persistence import PersistenceMode, PersistenceStatus

        st = PersistenceStatus(PersistenceMode.RESTRICTED, "disk full", None, None, 4, 0, 1)
        text = " ".join(x.text for x in P.banner_specs(self.snap(persistence=st)))
        self.assertIn("NOT being saved", text)
        self.assertIn("disk full", text)
        self.assertIn("4", text)

    def test_example_config_banner(self):
        self.assertIn(
            "example configuration", P.banner_specs(self.snap(using_example_config=True))[0].text
        )
