from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from bot.services.economy_write_gate import (
    EconomyGatePersistenceError,
    EconomyGateTransitionError,
    EconomyGateState,
    EconomyWriteGate,
    EconomyWriteRejected,
)


class EconomyWriteGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "economy_gate.json"

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self):
        return EconomyWriteGate.bootstrap_open(self.path)

    def test_open_drain_lock_open_and_persistent_audit(self):
        gate = self.gate()
        self.assertEqual(gate.state, EconomyGateState.OPEN)
        gate.begin_drain(reason="cutover", actor="operator")
        self.assertEqual(gate.active_writes, 0)
        gate.lock(reason="cutover", actor="operator")
        self.assertEqual(EconomyWriteGate(self.path).state, EconomyGateState.LOCKED)
        reopened = EconomyWriteGate(self.path)
        self.assertEqual(reopened.open(reason="abort" ).state, EconomyGateState.OPEN)
        events = json.loads(self.path.read_text(encoding="utf-8"))["events"]
        self.assertEqual([event["new_state"] for event in events], ["OPEN", "DRAINING", "LOCKED", "OPEN"])

    def test_missing_state_requires_explicit_bootstrap(self):
        with self.assertRaises(EconomyGatePersistenceError):
            EconomyWriteGate(self.path)
        gate = EconomyWriteGate.bootstrap_open(self.path)
        self.assertEqual(gate.state, EconomyGateState.OPEN)
        marker = Path(str(self.path) + ".initialized")
        self.assertTrue(marker.exists())
        before = self.path.read_bytes()
        self.assertEqual(EconomyWriteGate.bootstrap_open(self.path).state, EconomyGateState.OPEN)
        self.assertEqual(self.path.read_bytes(), before)

    def test_bootstrap_never_overwrites_draining_locked_or_corrupt_state(self):
        gate = self.gate(); gate.begin_drain(reason="test")
        self.assertEqual(EconomyWriteGate.bootstrap_open(self.path).state, EconomyGateState.DRAINING)
        gate.lock(reason="test")
        self.assertEqual(EconomyWriteGate.bootstrap_open(self.path).state, EconomyGateState.LOCKED)
        self.path.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(EconomyGatePersistenceError): EconomyWriteGate.bootstrap_open(self.path)

    def test_missing_state_after_initialization_fails_closed(self):
        gate = self.gate()
        self.path.unlink()
        with self.assertRaises(EconomyGatePersistenceError): EconomyWriteGate.bootstrap_open(self.path)
        self.assertEqual(gate.state, EconomyGateState.OPEN)

    def test_active_write_drains_then_locks(self):
        gate = self.gate()
        entered = threading.Event()
        release = threading.Event()

        def writer():
            with gate.write():
                entered.set()
                release.wait(2)

        thread = threading.Thread(target=writer)
        thread.start(); self.assertTrue(entered.wait(2))
        gate.begin_drain(reason="test")
        with self.assertRaises(EconomyWriteRejected):
            with gate.write():
                pass
        self.assertEqual(gate.wait_until_drained(0.01).active_writes, 1)
        release.set(); thread.join(2)
        self.assertEqual(gate.drain_and_lock(2).state, EconomyGateState.LOCKED)

    def test_timeout_never_forces_locked(self):
        gate = self.gate(); entered = threading.Event(); release = threading.Event()

        def writer():
            with gate.write():
                entered.set(); release.wait(2)

        thread = threading.Thread(target=writer); thread.start(); self.assertTrue(entered.wait(2))
        result = gate.drain_and_lock(0.01, reason="timeout")
        self.assertEqual(result.state, EconomyGateState.DRAINING)
        self.assertEqual(result.active_writes, 1)
        with self.assertRaises(EconomyGateTransitionError): gate.lock()
        release.set(); thread.join(2)

    def test_exception_releases_lease(self):
        gate = self.gate()
        with self.assertRaises(RuntimeError):
            with gate.write():
                raise RuntimeError("database error")
        self.assertEqual(gate.active_writes, 0)

    def test_nested_write_is_reentrant_but_not_double_counted(self):
        gate = self.gate()
        with gate.write():
            self.assertEqual(gate.active_writes, 1)
            with gate.write(): self.assertEqual(gate.active_writes, 1)
        self.assertEqual(gate.active_writes, 0)

    def test_race_admission_is_atomic(self):
        gate = self.gate(); entered = threading.Event(); release = threading.Event(); errors = []

        def admitted_writer():
            try:
                with gate.write():
                    entered.set(); release.wait(2)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=admitted_writer); thread.start(); self.assertTrue(entered.wait(2))
        gate.begin_drain(reason="race")
        rejected = []

        def late_writer():
            try:
                with gate.write(): pass
            except EconomyWriteRejected: rejected.append(True)

        late = [threading.Thread(target=late_writer) for _ in range(25)]
        for item in late: item.start()
        for item in late: item.join(2)
        self.assertEqual(len(rejected), 25); self.assertFalse(errors)
        release.set(); thread.join(2); gate.drain_and_lock(2)

    def test_restart_preserves_draining_and_locked_and_corruption_fails_closed(self):
        gate = self.gate(); gate.begin_drain(reason="restart")
        self.assertEqual(EconomyWriteGate(self.path).state, EconomyGateState.DRAINING)
        gate.lock(reason="restart")
        self.assertEqual(EconomyWriteGate(self.path).state, EconomyGateState.LOCKED)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(EconomyGatePersistenceError): EconomyWriteGate(self.path)

    def test_locked_rejects_and_open_restores_writes(self):
        gate = self.gate(); gate.drain_and_lock()
        with self.assertRaises(EconomyWriteRejected):
            with gate.write(): pass
        gate.open(reason="done")
        with gate.write(): self.assertEqual(gate.active_writes, 1)

    def test_invalid_transitions_are_rejected(self):
        gate = self.gate()
        with self.assertRaises(EconomyGateTransitionError): gate.lock()
        with self.assertRaises(EconomyGateTransitionError): gate.open()
        gate.begin_drain()
        with self.assertRaises(EconomyGateTransitionError): gate.begin_drain()

    def test_persistence_failure_does_not_change_in_memory_state(self):
        gate = self.gate()
        original = gate.snapshot()
        original_persist = gate._persist_event
        gate._persist_event = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
        with self.assertRaises(OSError): gate.begin_drain(reason="failure")
        self.assertEqual(gate.snapshot(), original)
        gate._persist_event = original_persist
        self.assertEqual(EconomyWriteGate(self.path).state, EconomyGateState.OPEN)


class EconomyWriteGateAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_releases_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = EconomyWriteGate.bootstrap_open(Path(directory) / "gate.json")
            started = asyncio.Event()

            async def writer():
                async with gate.write_async():
                    started.set(); await asyncio.sleep(60)

            task = asyncio.create_task(writer()); await started.wait(); self.assertEqual(gate.active_writes, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(gate.active_writes, 0)


if __name__ == "__main__":
    unittest.main()
