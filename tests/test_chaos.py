"""Chaos: a worker that dies, and what the ledger does about it.

The rules that matter under failure are the ones a worker cannot check:
a lease that runs out, a transaction that is cut in half, a process that
is killed between two writes. This exercises them by killing things on
purpose.
"""

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fluxrecord.core import Conflict, Ledger


class DeadWorkerTest(unittest.TestCase):
    """A worker that stops without saying goodbye."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        # M-2 wants the same resource as M-1: that is the contention.
        self.ledger.add_task("M-2", "Other", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_dead_worker_holds_its_scope_until_it_is_released(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 1, started["session_id"]),
        )
        self.ledger.reconcile()
        self.assertEqual(self.ledger.task("M-1")["status"], "blocked")
        # The scope stays reserved: another worker cannot start M-2 yet.
        with self.assertRaises(Conflict):
            self.ledger.start("M-2", "terminal-2")

    def test_releasing_the_dead_worker_lets_the_next_one_in(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 1, started["session_id"]),
        )
        self.ledger.reconcile()
        self.ledger.decide("M-1", "retry", "the old worker is gone")
        self.ledger.start("M-2", "terminal-2")

    def test_the_death_is_recorded_as_a_failure(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 1, started["session_id"]),
        )
        self.ledger.reconcile()
        categories = [failure["category"] for failure in self.ledger.status()["failures"]]
        self.assertIn("lease_expired", categories)

    def test_the_work_before_the_death_is_still_there(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        self.ledger.checkpoint(started["session_id"], "a summary", "an evidence", "next", 100)
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 1, started["session_id"]),
        )
        self.ledger.reconcile()
        evidence = self.ledger.evidence("M-1")
        self.assertEqual(len(evidence["checkpoints"]), 1)
        self.assertEqual(evidence["checkpoints"][0]["summary"], "a summary")

    def test_a_dead_worker_can_be_adopted_instead_of_waited_for(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        adopted = self.ledger.adopt(started["session_id"], "terminal-2", "the old worker is gone")
        self.assertEqual(adopted["owner"], "terminal-2")
        self.assertEqual(self.ledger.sessions()[0]["owner"], "terminal-2")

    def test_the_adoption_is_in_the_event_log(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        self.ledger.adopt(started["session_id"], "terminal-2", "gone")
        kinds = [event["type"] for event in self.ledger.events()]
        self.assertIn("session.adopted", kinds)


class CutTransactionTest(unittest.TestCase):
    """A transaction that is cut in half."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_failed_transaction_leaves_nothing_behind(self):
        before = len(self.ledger.status()["tasks"])
        with self.assertRaises(RuntimeError):
            with self.ledger.transaction():
                self.ledger.db.execute("UPDATE tasks SET status='in_progress' WHERE id='M-1'")
                raise RuntimeError("the worker died mid-transaction")
        self.assertEqual(self.ledger.task("M-1")["status"], "ready")
        self.assertEqual(len(self.ledger.status()["tasks"]), before)

    def test_a_committed_transaction_is_there(self):
        with self.ledger.transaction():
            self.ledger.db.execute("UPDATE tasks SET owner='terminal-1' WHERE id='M-1'")
        self.assertEqual(self.ledger.task("M-1")["owner"], "terminal-1")


class KilledProcessTest(unittest.TestCase):
    """A process that is killed outright."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, body: str) -> subprocess.CompletedProcess[str]:
        script = self.root / "worker.py"
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
            "from fluxrecord.core import Ledger\n"
            f"ledger = Ledger({str(self.db)!r})\n" + body
        )
        return subprocess.run([sys.executable, str(script)], capture_output=True, text=True)

    def test_a_killed_worker_leaves_a_usable_ledger(self):
        result = self.worker(
            "ledger.add_task('M-1', 'Objective', 'Acceptance', scope=['repo/x'])\n"
            "import os, signal\n"
            "os.kill(os.getpid(), signal.SIGKILL)\n"
        )
        self.assertNotEqual(result.returncode, 0)
        with Ledger(self.db) as ledger:
            self.assertEqual(len(ledger.status()["tasks"]), 1)
            self.assertEqual(ledger.task("M-1")["status"], "ready")

    def test_a_killed_worker_leaves_a_chain_that_still_verifies(self):
        with Ledger(self.db, chain_key="a-chain-key") as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        result = self.worker("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        self.assertNotEqual(result.returncode, 0)
        with Ledger(self.db, chain_key="a-chain-key") as ledger:
            report = ledger.verify_chain()
            self.assertTrue(report["ok"], report)


class TwoLedgersTest(unittest.TestCase):
    """Two processes on one ledger."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        self.ledger.add_task("M-2", "Other", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_only_one_process_holds_the_scope(self):
        other = Ledger(self.path)
        try:
            self.ledger.start("M-1", "terminal-1")
            with self.assertRaises(Conflict):
                other.start("M-2", "terminal-2")
        finally:
            other.close()

    def test_the_second_process_sees_the_first_one_s_work(self):
        other = Ledger(self.path)
        try:
            self.ledger.start("M-1", "terminal-1")
            self.assertEqual(other.task("M-1")["status"], "in_progress")
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
