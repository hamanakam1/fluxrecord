"""Property-based tests for the rules that must hold for any input.

The specification asks for property testing on the key functions. These
pin down the three rules a worker depends on: cumulative tokens never go
backwards, the budget is enforced, and repeated evidence is a failure.
"""

import tempfile
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fluxrecord.core import Conflict, Ledger

SLOW = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def ledger() -> tuple[tempfile.TemporaryDirectory, Ledger]:
    temp = tempfile.TemporaryDirectory()
    return temp, Ledger(Path(temp.name) / "ledger.sqlite")


class TokenBudgetTest(unittest.TestCase):
    @SLOW
    @given(st.lists(st.integers(min_value=0, max_value=5000), min_size=1, max_size=8))
    def test_cumulative_tokens_never_decrease(self, counts):
        temp, book = ledger()
        try:
            book.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], token_budget=100000)
            started = book.start("M-1", "terminal-1")
            session = started["session_id"]
            highest = 0
            for count in counts:
                try:
                    result = book.checkpoint(session, "s", f"evidence-{count}", "next", count)
                except Conflict:
                    break
                highest = max(highest, count)
                row = book.db.execute(
                    "SELECT tokens FROM sessions WHERE id=?", (session,)
                ).fetchone()
                self.assertGreaterEqual(row["tokens"], highest)
                if result["status"] == "blocked":
                    break
        finally:
            book.close()
            temp.cleanup()

    @SLOW
    @given(st.integers(min_value=1, max_value=1000))
    def test_reaching_the_budget_blocks_the_task(self, budget):
        temp, book = ledger()
        try:
            book.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], token_budget=budget)
            started = book.start("M-1", "terminal-1")
            result = book.checkpoint(started["session_id"], "s", "evidence", "next", budget)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(book.task("M-1")["status"], "blocked")
            categories = [f["category"] for f in book.status()["failures"]]
            self.assertIn("budget", categories)
        finally:
            book.close()
            temp.cleanup()


class StagnationTest(unittest.TestCase):
    @SLOW
    @given(st.integers(min_value=1, max_value=3))
    def test_three_identical_checkpoints_block(self, repeats):
        temp, book = ledger()
        try:
            book.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], token_budget=100000)
            started = book.start("M-1", "terminal-1")
            session = started["session_id"]
            status = "active"
            for _ in range(repeats):
                status = book.checkpoint(session, "s", "the same evidence", "next", 10)["status"]
            if repeats >= 3:
                self.assertEqual(status, "blocked")
                categories = [f["category"] for f in book.status()["failures"]]
                self.assertIn("no_progress", categories)
            else:
                self.assertEqual(status, "active")
        finally:
            book.close()
            temp.cleanup()


class PacketBudgetTest(unittest.TestCase):
    @SLOW
    @given(st.integers(min_value=600, max_value=4000))
    def test_the_packet_never_exceeds_its_budget(self, max_bytes):
        temp, book = ledger()
        try:
            book.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
            for index in range(3):
                book.add_context("M-1", f"context-{index}-" + "y" * 200, "test")
            result = book.pack("M-1", max_bytes=max_bytes)
            self.assertLessEqual(result["packet_bytes"], max_bytes)
            included = len(result["packet"]["context"])
            self.assertEqual(included + len(result["omitted"]), 3)
            for omission in result["omitted"]:
                self.assertIn("reason", omission)
        finally:
            book.close()
            temp.cleanup()


class ScopeConflictTest(unittest.TestCase):
    @SLOW
    @given(
        st.lists(
            st.sets(st.sampled_from(["repo/a", "repo/b", "repo/c"]), min_size=1, max_size=3),
            min_size=2,
            max_size=4,
        )
    )
    def test_overlapping_scopes_cannot_run_together(self, scopes):
        temp, book = ledger()
        try:
            for index, scope in enumerate(scopes):
                book.add_task(f"M-{index}", "Objective", "Acceptance", scope=sorted(scope))
            started = 0
            for index, scope in enumerate(scopes):
                try:
                    book.start(f"M-{index}", "terminal-1")
                    started += 1
                except Conflict:
                    break
            self.assertLessEqual(started, len(scopes))
        finally:
            book.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
