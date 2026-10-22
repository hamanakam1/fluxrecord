"""The failure taxonomy from SPEC.md, and the exactness of the byte budget."""

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import FAILURE_CATEGORIES, Conflict, Ledger


class FailureCategoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        self.started = self.ledger.start("M-1", "terminal-1")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_every_documented_category_is_accepted(self):
        for category in FAILURE_CATEGORIES:
            self.ledger.add_task(
                f"M-{category}", "Objective", "Acceptance", scope=[f"repo/{category}"]
            )
            started = self.ledger.start(f"M-{category}", "terminal-1")
            result = self.ledger.fail(started["session_id"], category, "something went wrong")
            self.assertEqual(result["status"], "blocked")

    def test_a_category_outside_the_taxonomy_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.fail(self.started["session_id"], "whatever", "something went wrong")

    def test_the_taxonomy_is_the_one_in_the_spec(self):
        self.assertEqual(
            FAILURE_CATEGORIES,
            ("lease_expired", "budget", "tool", "dependency", "no_progress"),
        )


class ByteBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_the_reported_byte_count_is_the_real_one(self):
        for index in range(20):
            self.ledger.add_context("M-1", f"body-{index}-" + "z" * 200, "source")
        for limit in (1200, 2000, 5000, 20000):
            result = self.ledger.pack("M-1", limit)
            actual = len(
                json.dumps(result["packet"], ensure_ascii=False, separators=(",", ":")).encode()
            )
            self.assertEqual(actual, result["packet_bytes"])
            self.assertLessEqual(actual, limit)

    def test_every_context_is_either_included_or_omitted_with_a_reason(self):
        for index in range(10):
            self.ledger.add_context("M-1", f"body-{index}-" + "z" * 200, "source")
        result = self.ledger.pack("M-1", 2000)
        included = len(result["packet"]["context"])
        self.assertEqual(included + len(result["omitted"]), 10)
        for omission in result["omitted"]:
            self.assertIn(omission["reason"], ("byte_budget", "expired"))

    def test_a_budget_too_small_for_the_mandatory_fields_is_refused(self):
        with self.assertRaises(Conflict):
            self.ledger.pack("M-1", max_bytes=10)


if __name__ == "__main__":
    unittest.main()
