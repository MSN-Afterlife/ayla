from __future__ import annotations

import asyncio
import contextvars
import json
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator


class EconomyGateState(StrEnum):
    OPEN = "OPEN"
    DRAINING = "DRAINING"
    LOCKED = "LOCKED"


class EconomyGateError(RuntimeError):
    pass


class EconomyWriteRejected(EconomyGateError, ValueError):
    """A new mutation was attempted while the gate was not OPEN."""


class EconomyGatePersistenceError(EconomyGateError):
    """The operational state could not be read or safely persisted."""


class EconomyGateTransitionError(EconomyGateError):
    pass


@dataclass(frozen=True)
class EconomyGateSnapshot:
    state: EconomyGateState
    active_writes: int
    entered_at: str | None
    reason: str | None
    event_count: int


class EconomyWriteGate:
    """Process-local writer admission backed by a durable operational state.

    The current deployment has one economic writer process. State transitions
    and active leases are synchronized in this process; the JSON file makes
    OPEN/DRAINING/LOCKED survive restart. Multi-process deployments require a
    distributed lock/coordinator before they can share this gate.
    """

    _depth: contextvars.ContextVar[int] = contextvars.ContextVar("economy_write_gate_depth", default=0)

    def __init__(self, state_path: str | Path, *, bootstrap_open: bool = False) -> None:
        requested_path = Path(state_path).expanduser()
        if requested_path.is_symlink():
            raise EconomyGatePersistenceError("gate state symlinks are not accepted")
        self.state_path = requested_path.resolve()
        self.bootstrap_marker_path = self.state_path.with_name(self.state_path.name + ".initialized")
        self._condition = threading.Condition(threading.RLock())
        self._state: EconomyGateState
        self._entered_at: str | None
        self._reason: str | None
        self._events: list[dict[str, Any]]
        self._active_writes = 0
        if self.state_path.exists():
            self._load()
        elif bootstrap_open:
            if self.bootstrap_marker_path.exists():
                raise EconomyGatePersistenceError("economic gate state is missing after bootstrap")
            self._state = EconomyGateState.OPEN
            self._entered_at = _utc_now()
            self._reason = "explicit bootstrap"
            self._events = []
            self._persist_event(None, EconomyGateState.OPEN, "explicit bootstrap", "BOOTSTRAP")
            try:
                self.bootstrap_marker_path.write_text("AYLA_ECONOMY_WRITE_GATE_INITIALIZED_V1\n", encoding="utf-8")
                try:
                    os.chmod(self.bootstrap_marker_path, 0o600)
                except OSError:
                    pass
            except OSError as exc:
                raise EconomyGatePersistenceError("cannot persist economic gate bootstrap marker") from exc
        else:
            raise EconomyGatePersistenceError(f"economic gate state is missing: {self.state_path}")

    @classmethod
    def bootstrap_open(cls, state_path: str | Path) -> "EconomyWriteGate":
        return cls(state_path, bootstrap_open=True)

    @property
    def state(self) -> EconomyGateState:
        with self._condition:
            return self._state

    @property
    def active_writes(self) -> int:
        with self._condition:
            return self._active_writes

    def snapshot(self) -> EconomyGateSnapshot:
        with self._condition:
            return EconomyGateSnapshot(self._state, self._active_writes, self._entered_at, self._reason, len(self._events))

    def begin_drain(self, *, reason: str = "maintenance", actor: str | None = None) -> EconomyGateSnapshot:
        with self._condition:
            if self._state != EconomyGateState.OPEN:
                raise EconomyGateTransitionError(f"cannot begin drain from {self._state.value}")
            self._transition(EconomyGateState.DRAINING, reason, actor)
            self._condition.notify_all()
            return self.snapshot()

    def wait_until_drained(self, timeout: float = 30.0) -> EconomyGateSnapshot:
        if timeout < 0:
            raise ValueError("timeout must be nonnegative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active_writes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.snapshot()
                self._condition.wait(remaining)
            return self.snapshot()

    async def wait_until_drained_async(self, timeout: float = 30.0) -> EconomyGateSnapshot:
        return await asyncio.to_thread(self.wait_until_drained, timeout)

    def lock(self, *, reason: str = "maintenance", actor: str | None = None) -> EconomyGateSnapshot:
        with self._condition:
            if self._state != EconomyGateState.DRAINING:
                raise EconomyGateTransitionError(f"cannot lock from {self._state.value}")
            if self._active_writes:
                raise EconomyGateTransitionError("cannot lock while active writes remain")
            self._transition(EconomyGateState.LOCKED, reason, actor)
            return self.snapshot()

    def drain_and_lock(self, timeout: float = 30.0, *, reason: str = "maintenance", actor: str | None = None) -> EconomyGateSnapshot:
        if self.state == EconomyGateState.OPEN:
            self.begin_drain(reason=reason, actor=actor)
        snapshot = self.wait_until_drained(timeout)
        if snapshot.active_writes:
            return snapshot
        return self.lock(reason=reason, actor=actor)

    def open(self, *, reason: str = "maintenance complete", actor: str | None = None) -> EconomyGateSnapshot:
        with self._condition:
            if self._state != EconomyGateState.LOCKED:
                raise EconomyGateTransitionError(f"cannot open from {self._state.value}")
            if self._active_writes:
                raise EconomyGateTransitionError("cannot open while active writes remain")
            self._transition(EconomyGateState.OPEN, reason, actor)
            return self.snapshot()

    @contextmanager
    def write(self) -> Iterator[None]:
        depth = self._depth.get()
        if depth:
            token = self._depth.set(depth + 1)
            try:
                yield
            finally:
                self._depth.reset(token)
            return
        with self._condition:
            if self._state != EconomyGateState.OPEN:
                raise EconomyWriteRejected("A economia da Ayla está temporariamente em manutenção. Tente novamente em alguns minutos.")
            self._active_writes += 1
            self._condition.notify_all()
        token = self._depth.set(1)
        try:
            yield
        finally:
            self._depth.reset(token)
            with self._condition:
                self._active_writes -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def write_async(self):
        with self.write():
            yield

    def _load(self) -> None:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            state = EconomyGateState(payload["state"])
            if not isinstance(payload.get("events", []), list):
                raise ValueError("events must be a list")
            self._state = state
            self._entered_at = payload.get("entered_at")
            self._reason = payload.get("reason")
            self._events = payload.get("events", [])[-256:]
        except EconomyGatePersistenceError:
            raise
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise EconomyGatePersistenceError(f"invalid economic gate state: {self.state_path}") from exc

    def _transition(self, new_state: EconomyGateState, reason: str, actor: str | None) -> None:
        previous = self._state
        previous_entered_at = self._entered_at
        previous_reason = self._reason
        previous_events = list(self._events)
        self._state = new_state
        self._entered_at = _utc_now()
        self._reason = _safe_text(reason)
        try:
            self._persist_event(previous, new_state, self._reason, actor)
        except Exception:
            self._state = previous
            self._entered_at = previous_entered_at
            self._reason = previous_reason
            self._events = previous_events
            raise

    def _persist_event(self, previous: EconomyGateState | None, new_state: EconomyGateState, reason: str, actor: str | None) -> None:
        self._events.append({"timestamp": _utc_now(), "event_type": "STATE_TRANSITION", "previous_state": previous.value if previous else None, "new_state": new_state.value, "reason": _safe_text(reason), "actor": _safe_text(actor) if actor else None})
        payload = {"version": 1, "state": self._state.value, "entered_at": self._entered_at, "reason": self._reason, "events": self._events[-256:]}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.state_path)
            try:
                os.chmod(self.state_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            raise EconomyGatePersistenceError(f"cannot persist economic gate state: {self.state_path}") from exc


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).replace("\r", " ").replace("\n", " ")[:256]
