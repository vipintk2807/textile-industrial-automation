"""Builds DeviceSnapshots by running the real worker deterministically (used by UI tests)."""

from helpers import FakeStorage, device
from textile_automation.adapters.simulator import SimulatorAdapter
from textile_automation.clock import ManualClock
from textile_automation.domain.models import RetryPolicy
from textile_automation.services.acquisition import DeviceWorker
from textile_automation.services.device_state import DeviceStateTracker
from textile_automation.services.events import EventService
from textile_automation.services.persistence import PersistenceGate
from textile_automation.services.pipeline import MeasurementPipeline


def run_steps(faults, steps, advance_after=0.0, **spec_kw):
    clock = ManualClock()
    spec = device(retry=RetryPolicy(1.0, 4.0, 2.0, 50), settings={"faults": faults}, **spec_kw)
    storage = FakeStorage()
    events = EventService(storage, clock)
    adapter = SimulatorAdapter(spec, clock, sleep=lambda s: False)
    adapter.open()
    tracker = DeviceStateTracker(spec, adapter.source_kind, clock)
    worker = DeviceWorker(
        spec,
        adapter,
        tracker,
        MeasurementPipeline(PersistenceGate(storage, events, clock), events),
        events,
        clock,
        guard_grace_s=0.05,
    )
    for _ in range(steps):
        delay = worker.step()
        clock.advance(delay or 0)
    clock.advance(advance_after)
    return tracker.snapshot()
