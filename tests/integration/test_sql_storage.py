"""SQLite persistence tests using temporary databases. Require sqlalchemy + alembic."""

import sqlite3
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from helpers import device, meas, requires_sqlalchemy
from textile_automation.domain.errors import StorageError
from textile_automation.domain.models import (
    DataType,
    Event,
    EventCategory as EC,
    MeasurementResult,
    Severity,
)
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import SourceKind

T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)


def result(key="speed", value=1.5, at=T0, quality=Quality.VALID, device_id="dev-1", src=None):
    return MeasurementResult(
        device_id, key, value, "rpm", at, SourceKind.SIMULATOR, quality, source_timestamp=src
    )


@requires_sqlalchemy
class SqlStorageTests(unittest.TestCase):
    def setUp(self):
        from textile_automation.storage.repositories import SqlStorage

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "t.db"
        self.SqlStorage = SqlStorage
        self.db = SqlStorage.open(self.path)
        spec = device(
            "dev-1",
            measurements=[
                meas("speed"),
                meas("count", data_type=DataType.INT, unit="pcs"),
                meas("on", data_type=DataType.BOOL, unit=None),
                meas("mode", data_type=DataType.STR, unit=None),
            ],
        )
        self.db.upsert_devices([spec], {"dev-1": "SIMULATOR"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_schema_matches_models(self):
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.engine import URL
        from textile_automation.storage.models import Base

        engine = create_engine(URL.create("sqlite", database=str(self.path)))
        insp = inspect(engine)
        for table in Base.metadata.sorted_tables:
            self.assertIn(table.name, insp.get_table_names())
            self.assertEqual(
                {c["name"] for c in insp.get_columns(table.name)}, {c.name for c in table.columns}
            )
            self.assertEqual(
                {i["name"] for i in insp.get_indexes(table.name)},
                {i.name for i in table.indexes},
                table.name,
            )
        engine.dispose()

    def test_roundtrip_preserves_metadata_types_and_timezone(self):
        src = T0 - timedelta(seconds=1)
        self.db.save_measurements(
            [
                result(value=1.5, src=src),
                result("count", 7),
                result("on", True),
                result("mode", "A"),
            ]
        )
        got = {
            r.measurement_key: r
            for r in self.db.query_measurements("dev-1", "speed", None, None, 10)
        }
        r = got["speed"]
        self.assertEqual(
            (r.value, r.unit, r.source_kind, r.quality),
            (1.5, "rpm", SourceKind.SIMULATOR, Quality.VALID),
        )
        self.assertEqual((r.received_at, r.source_timestamp), (T0, src))
        self.assertIsNotNone(r.received_at.tzinfo)
        self.assertEqual(self.db.latest_measurement("dev-1", "count").value, 7)
        self.assertIsInstance(self.db.latest_measurement("dev-1", "count").value, int)
        self.assertIs(self.db.latest_measurement("dev-1", "on").value, True)
        self.assertEqual(self.db.latest_measurement("dev-1", "mode").value, "A")

    def test_zero_and_false_are_stored_as_values(self):
        self.db.save_measurements([result(value=0.0), result("on", False)])
        self.assertEqual(self.db.latest_measurement("dev-1", "speed").value, 0.0)
        self.assertIs(self.db.latest_measurement("dev-1", "on").value, False)

    def test_non_valid_results_are_refused(self):
        for q in (Quality.INVALID, Quality.MISSING, Quality.STALE, Quality.UNKNOWN):
            with self.assertRaises(StorageError):
                self.db.save_measurements([result(quality=q)])
        self.assertEqual(self.db.query_measurements("dev-1", "speed", None, None, 10), [])

    def test_batch_is_atomic_on_unsupported_value(self):
        with self.assertRaises(StorageError):
            self.db.save_measurements([result(value=1.0), result("count", value=[1, 2])])
        self.assertEqual(self.db.query_measurements("dev-1", "speed", None, None, 10), [])

    def test_failed_transaction_rolls_back(self):
        with self.assertRaises(StorageError):  # foreign key: unknown device
            self.db.save_measurements([result(value=1.0), result(device_id="ghost", value=2.0)])
        self.assertEqual(self.db.query_measurements("dev-1", "speed", None, None, 10), [])

    def test_time_filter_limit_and_order(self):
        self.db.save_measurements(
            [result(value=float(i), at=T0 + timedelta(seconds=i)) for i in range(10)]
        )
        rows = self.db.query_measurements(
            "dev-1", "speed", T0 + timedelta(seconds=2), T0 + timedelta(seconds=8), 100
        )
        self.assertEqual([r.value for r in rows], [float(i) for i in range(2, 9)])
        last3 = self.db.query_measurements("dev-1", "speed", None, None, 3)
        self.assertEqual([r.value for r in last3], [7.0, 8.0, 9.0])

    def test_events_roundtrip_and_filters(self):
        self.db.save_event(
            Event(T0, EC.CONNECTION_FAILURE, Severity.WARNING, "a", "dev-1", {"n": 1})
        )
        self.db.save_event(
            Event(T0 + timedelta(seconds=1), EC.RECOVERY, Severity.INFO, "b", "dev-1")
        )
        self.db.save_event(Event(T0 + timedelta(seconds=2), EC.STARTUP, Severity.INFO, "c", None))
        self.assertEqual([e.message for e in self.db.query_events()], ["c", "b", "a"])
        self.assertEqual([e.message for e in self.db.query_events(device_id="dev-1")], ["b", "a"])
        self.assertEqual([e.message for e in self.db.query_events(categories=["RECOVERY"])], ["b"])
        self.assertEqual([e.message for e in self.db.query_events(severities=["WARNING"])], ["a"])
        self.assertEqual(
            [e.message for e in self.db.query_events(start=T0 + timedelta(seconds=1))], ["c", "b"]
        )
        self.assertEqual(self.db.query_events(device_id="dev-1")[1].details, {"n": 1})

    def test_history_survives_restart(self):
        self.db.save_measurements([result(value=3.25)])
        self.db.close()
        again = self.SqlStorage.open(self.path)
        try:
            self.assertEqual(again.latest_measurement("dev-1", "speed").value, 3.25)
        finally:
            again.close()
        self.db = self.SqlStorage.open(self.path)

    def test_migration_is_idempotent(self):
        self.db.close()
        self.db = self.SqlStorage.open(self.path)
        self.db.health_check()

    def test_secrets_are_redacted_in_stored_device_configuration(self):
        spec = device("dev-2", measurements=[meas()], settings={"seed": 1, "password": "hunter2"})
        self.db.upsert_devices([spec], {"dev-2": "SIMULATOR"})
        con = sqlite3.connect(str(self.path))
        try:
            text = con.execute(
                "SELECT configuration FROM devices WHERE device_id='dev-2'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertNotIn("hunter2", text)

    def test_purge_removes_only_old_rows(self):
        self.db.save_measurements([result(at=T0), result(at=T0 + timedelta(days=2))])
        removed, _ = self.db.purge_before(T0 + timedelta(days=1))
        self.assertEqual(removed, 1)
        self.assertEqual(len(self.db.query_measurements("dev-1", "speed", None, None, 10)), 1)

    def test_concurrent_writers_do_not_lose_rows(self):
        def work(n):
            for i in range(25):
                self.db.save_measurements(
                    [result(value=float(n * 100 + i), at=T0 + timedelta(milliseconds=n * 1000 + i))]
                )

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(len(self.db.query_measurements("dev-1", "speed", None, None, 1000)), 100)

    def test_backup_is_a_readable_copy(self):
        from textile_automation.storage.database import backup_database

        self.db.save_measurements([result(value=9.0)])
        dest = backup_database(self.path, Path(self.tmp.name) / "bk" / "copy.db")
        again = self.SqlStorage.open(dest)
        try:
            self.assertEqual(again.latest_measurement("dev-1", "speed").value, 9.0)
        finally:
            again.close()

    def test_backup_of_missing_database_fails_clearly(self):
        from textile_automation.storage.database import backup_database

        with self.assertRaises(StorageError):
            backup_database(Path(self.tmp.name) / "none.db", Path(self.tmp.name) / "x.db")


@requires_sqlalchemy
class SqlStorageFailureTests(unittest.TestCase):
    def test_unusable_location_gives_actionable_error(self):
        from textile_automation.storage.repositories import SqlStorage

        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "afile"
            blocker.write_text("x")
            with self.assertRaises(StorageError) as cm:
                SqlStorage.open(blocker / "db.sqlite")
            self.assertTrue(str(cm.exception))

    def test_newer_schema_revision_is_refused(self):
        from textile_automation.storage.repositories import SqlStorage

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.db"
            SqlStorage.open(path).close()
            con = sqlite3.connect(str(path))
            con.execute("UPDATE alembic_version SET version_num='zzzz'")
            con.commit()
            con.close()
            with self.assertRaises(StorageError) as cm:
                SqlStorage.open(path)
            self.assertIn("newer", str(cm.exception))

    def test_runtime_end_to_end_on_real_database(self):
        from helpers import wait_until
        from textile_automation.domain.models import AppConfig, RetryPolicy
        from textile_automation.runtime import AppRuntime
        from textile_automation.storage.repositories import SqlStorage

        spec = device(
            "sim-1",
            poll=0.05,
            retry=RetryPolicy(0.05, 0.1, 2.0, 3),
            measurements=[meas("speed", mapping={"base": 10.0})],
            settings={"faults": [{"kind": "read_timeout", "start": 3, "duration": 1}]},
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "e2e.db"
            rt = AppRuntime(
                AppConfig(devices=(spec,), event_dedup_window_s=0.001),
                SqlStorage.open(path),
                guard_grace_s=0.1,
            )
            rt.start()
            self.assertTrue(
                wait_until(lambda: any(e.category == EC.RECOVERY for e in rt.events.recent()), 10)
            )
            stored = rt.history("sim-1", "speed", None, None)
            self.assertGreaterEqual(len(stored), 3)
            self.assertTrue(all(r.source_kind is SourceKind.SIMULATOR for r in stored))
            self.assertTrue(rt.stored_events(device_id="sim-1"))
            self.assertTrue(rt.shutdown().stopped_cleanly)
            again = SqlStorage.open(path)
            try:
                self.assertGreaterEqual(
                    len(again.query_measurements("sim-1", "speed", None, None, 100)), 3
                )
            finally:
                again.close()


if __name__ == "__main__":
    unittest.main()
