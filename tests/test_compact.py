"""Compaction: the event log does not grow without bound."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class CompactTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def build(self, count=6, key="a-chain-key"):
        ledger = Ledger(self.path, chain_key=key)
        for index in range(count):
            ledger.add_task(f"M-{index}", "Objective", "Acceptance", scope=[f"repo/{index}"])
        return ledger

    def test_a_short_log_is_left_alone(self):
        with self.build(3) as ledger:
            result = ledger.compact(keep=100)
            self.assertEqual(result["pruned"], 0)
            self.assertEqual(len(ledger.events()), 3)

    def test_the_oldest_events_are_pruned(self):
        with self.build(6) as ledger:
            result = ledger.compact(keep=2)
            self.assertEqual(result["pruned"], 4)
            self.assertEqual(len(ledger.events()), 2)

    def test_the_chain_still_verifies_after_a_compaction(self):
        with self.build(6) as ledger:
            self.assertTrue(ledger.verify_chain()["ok"])
            ledger.compact(keep=2)
            report = ledger.verify_chain()
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["events"], 2)

    def test_the_compaction_is_summarised(self):
        with self.build(6) as ledger:
            ledger.compact(keep=2)
            summary = ledger.compaction_summary()
            self.assertEqual(summary["events"], 4)
            self.assertIn("task.created", summary["kinds"])

    def test_the_events_that_were_pruned_are_still_counted(self):
        with self.build(6) as ledger:
            ledger.compact(keep=2)
            summary = ledger.compaction_summary()
            self.assertEqual(summary["from"] + summary["events"] - 1, summary["to"])

    def test_an_edit_after_a_compaction_is_still_detected(self):
        with self.build(6) as ledger:
            ledger.compact(keep=2)
            ledger.db.execute(
                "UPDATE events SET created=1.0 WHERE seq=(SELECT MAX(seq) FROM events)"
            )
            report = ledger.verify_chain()
            self.assertFalse(report["ok"])

    def test_a_compaction_needs_something_to_keep(self):
        with self.build(3) as ledger:
            with self.assertRaises(ValueError):
                ledger.compact(keep=0)


if __name__ == "__main__":
    unittest.main()
