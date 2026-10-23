"""State vocabularies: communication state, data source kind, machine operating state.

Communication state, machine operating state and measurement quality are three separate
concepts. Nothing in this module derives one from another.
"""

from __future__ import annotations

from enum import Enum, StrEnum


class CommunicationState(StrEnum):
    """State of the monitoring link to a device (never the state of the machine itself)."""

    DISABLED = "DISABLED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RECOVERING = "RECOVERING"
    ERROR = "ERROR"


class SourceKind(StrEnum):
    """Where a measurement came from. Persisted with every record."""

    SIMULATOR = "SIMULATOR"
    PHYSICAL = "PHYSICAL"


class OperatingState(StrEnum):
    """Machine operating state, only when a source provides a documented mapping."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    IDLE = "IDLE"
    FAULTED = "FAULTED"


class CommEvent(Enum):
    """Evidence-bearing events that may change the communication state."""

    BEGIN_CONNECT = "begin_connect"  # first connection attempt of a session
    BEGIN_RECOVERY = "begin_recovery"  # reconnect after the link had worked
    CONNECT_OK = "connect_ok"  # link established; data not yet verified
    CONNECT_FAIL = "connect_fail"
    READ_FULL = "read_full"  # every configured measurement accepted
    READ_PARTIAL = "read_partial"  # some accepted, some invalid/missing
    READ_NONE = "read_none"  # response arrived but nothing usable
    READ_FAIL = "read_fail"  # read failed or timed out
    GIVE_UP = "give_up"  # consecutive-failure limit reached
    FATAL = "fatal"  # non-retryable error (configuration, mapping, defect)
    OPERATOR_RETRY = "operator_retry"
    STOP = "stop"


class InvalidTransition(RuntimeError):
    """Raised when code requests a transition the state model does not define (a defect)."""


_S = CommunicationState
_E = CommEvent
_LIVE = (_S.CONNECTING, _S.RECOVERING, _S.CONNECTED, _S.DEGRADED)
_ACTIVE = (_S.DISCONNECTED, *_LIVE, _S.ERROR)

_TRANSITIONS: dict[tuple[CommunicationState, CommEvent], CommunicationState] = {
    (_S.DISCONNECTED, _E.BEGIN_CONNECT): _S.CONNECTING,
    (_S.DEGRADED, _E.BEGIN_CONNECT): _S.CONNECTING,
    (_S.DISCONNECTED, _E.BEGIN_RECOVERY): _S.RECOVERING,
    (_S.DEGRADED, _E.BEGIN_RECOVERY): _S.RECOVERING,
    (_S.CONNECTED, _E.BEGIN_RECOVERY): _S.RECOVERING,
    (_S.CONNECTING, _E.CONNECT_OK): _S.CONNECTING,
    (_S.RECOVERING, _E.CONNECT_OK): _S.RECOVERING,
    # A device that never connected waits for its next attempt as DISCONNECTED; a device that
    # was working before stays RECOVERING while attempts continue.
    (_S.CONNECTING, _E.CONNECT_FAIL): _S.DISCONNECTED,
    (_S.RECOVERING, _E.CONNECT_FAIL): _S.RECOVERING,
    (_S.ERROR, _E.OPERATOR_RETRY): _S.DISCONNECTED,
}
for _state in _LIVE:
    _TRANSITIONS[(_state, _E.READ_FULL)] = _S.CONNECTED
    _TRANSITIONS[(_state, _E.READ_PARTIAL)] = _S.DEGRADED
    _TRANSITIONS[(_state, _E.READ_NONE)] = _S.DEGRADED
    _TRANSITIONS[(_state, _E.READ_FAIL)] = _S.DEGRADED
for _state in (_S.DISCONNECTED, *_LIVE):
    _TRANSITIONS[(_state, _E.GIVE_UP)] = _S.ERROR
    _TRANSITIONS[(_state, _E.FATAL)] = _S.ERROR
for _state in _ACTIVE:
    _TRANSITIONS[(_state, _E.STOP)] = _S.DISCONNECTED


def next_comm_state(current: CommunicationState, event: CommEvent) -> CommunicationState:
    """Return the state after `event`, or raise InvalidTransition for an undefined pair.

    DISABLED has no outgoing transitions in this release: a disabled device is never polled.
    """
    try:
        return _TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(f"No transition from {current} on {event.name}") from None
