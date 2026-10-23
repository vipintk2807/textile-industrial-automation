import math
import unittest
from datetime import datetime

from helpers import meas
from textile_automation.clock import ManualClock
from textile_automation.domain.errors import ErrorCategory as C
from textile_automation.domain.models import DataType, MeasurementResult
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import SourceKind
from textile_automation.services.validation import missing_result, validate_result


def res(value, unit="rpm", q=Quality.UNKNOWN, ts=None, clock=ManualClock()):
    return MeasurementResult(
        "d", "speed", value, unit, clock.now(), SourceKind.SIMULATOR, q, source_timestamp=ts
    )


class ValidationTests(unittest.TestCase):
    def check(self, spec, value, quality, category=None, **kw):
        out = validate_result(spec, res(value, spec.unit, **kw))
        self.assertEqual(out.quality, quality, out)
        if category:
            self.assertEqual(out.error.category, category)
        if quality is not Quality.VALID:
            self.assertIsNone(out.value)  # failure never becomes a plausible value
        return out

    def test_valid_float_and_int_widening(self):
        self.assertEqual(self.check(meas(), 12, Quality.VALID).value, 12.0)
        self.assertIsInstance(validate_result(meas(), res(12)).value, float)

    def test_bool_is_not_a_number(self):
        self.check(meas(), True, Quality.INVALID, C.TYPE_MISMATCH)

    def test_text_not_converted(self):
        self.check(meas(), "12", Quality.INVALID, C.TYPE_MISMATCH)

    def test_non_finite(self):
        for v in (math.nan, math.inf, -math.inf):
            self.check(meas(), v, Quality.INVALID, C.NON_FINITE_VALUE)

    def test_range_boundaries_inclusive(self):
        spec = meas(min_value=0, max_value=10)
        self.check(spec, 0.0, Quality.VALID)
        self.check(spec, 10.0, Quality.VALID)
        self.check(spec, -0.001, Quality.INVALID, C.OUT_OF_RANGE)
        self.check(spec, 10.001, Quality.INVALID, C.OUT_OF_RANGE)

    def test_no_invented_limits(self):
        self.check(meas(), 1e12, Quality.VALID)

    def test_zero_is_valid(self):
        self.assertEqual(self.check(meas(), 0.0, Quality.VALID).value, 0.0)

    def test_none_is_missing_not_zero(self):
        out = validate_result(meas(), res(None))
        self.assertEqual(out.quality, Quality.MISSING)
        self.assertIsNone(out.value)

    def test_int_type_rules(self):
        spec = meas(data_type=DataType.INT, unit="pcs")
        self.check(spec, 5, Quality.VALID)
        self.check(spec, 5.5, Quality.INVALID, C.TYPE_MISMATCH)
        self.check(spec, 2**60, Quality.INVALID, C.OUT_OF_RANGE)

    def test_bool_and_str(self):
        self.check(meas(data_type=DataType.BOOL, unit=None), True, Quality.VALID)
        self.check(meas(data_type=DataType.BOOL, unit=None), 1, Quality.INVALID, C.TYPE_MISMATCH)
        s = meas(data_type=DataType.STR, unit=None)
        self.check(s, "ok", Quality.VALID)
        self.check(s, "x" * 300, Quality.INVALID, C.OUT_OF_RANGE)
        self.check(s, 5, Quality.INVALID, C.TYPE_MISMATCH)

    def test_unit_mismatch_rejected_not_converted(self):
        out = validate_result(meas(unit="rpm"), res(5.0, unit="m/s"))
        self.assertEqual(out.error.category, C.UNIT_MISMATCH)

    def test_unknown_unit_stays_unknown(self):
        spec = meas(unit=None)
        self.assertIsNone(validate_result(spec, res(5.0, unit=None)).unit)
        self.assertEqual(
            validate_result(spec, res(5.0, unit="rpm")).error.category, C.UNIT_MISMATCH
        )

    def test_naive_source_timestamp_rejected(self):
        out = validate_result(meas(), res(5.0, ts=datetime(2026, 1, 1)))
        self.assertEqual(out.error.category, C.INVALID_TIMESTAMP)

    def test_missing_source_timestamp_stays_none(self):
        self.assertIsNone(validate_result(meas(), res(5.0, ts=None)).source_timestamp)

    def test_aware_source_timestamp_preserved(self):
        ts = ManualClock().now()
        self.assertEqual(validate_result(meas(), res(5.0, ts=ts)).source_timestamp, ts)

    def test_adapter_invalid_is_never_upgraded(self):
        r = res(5.0, q=Quality.INVALID)
        out = validate_result(meas(), r)
        self.assertEqual(out.quality, Quality.INVALID)
        self.assertIsNone(out.value)

    def test_adapter_claiming_valid_is_still_validated(self):
        self.check(meas(), math.nan, Quality.INVALID, C.NON_FINITE_VALUE, q=Quality.VALID)

    def test_raw_value_kept_for_diagnostics(self):
        out = validate_result(meas(), res("abc"))
        self.assertEqual(out.raw_value, "'abc'")

    def test_missing_result_builder(self):
        out = missing_result(meas(), res(None))
        self.assertEqual((out.quality, out.error.category), (Quality.MISSING, C.MISSING_VALUE))


if __name__ == "__main__":
    unittest.main()
