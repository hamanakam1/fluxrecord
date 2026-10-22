"""The event chain: a signed ledger makes a silent edit detectable."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def signed(self, key="a-chain-key"):
        ledger = Ledger(self.path, chain_key=key)
        ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        ledger.start("M-1", "terminal-1")
        return ledger

    def test_an_unsigned_ledger_says_it_cannot_be_checked(self):
        with Ledger(self.path) as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
            report = ledger.verify_chain()
            self.assertFalse(report["signed"])
            self.assertTrue(report["ok"])

    def test_a_signed_ledger_verifies(self):
        with self.signed() as ledger:
            report = ledger.verify_chain()
            self.assertTrue(report["signed"])
            self.assertTrue(report["ok"])
            self.assertIsNone(report["broken_at"])
            self.assertGreater(report["events"], 1)

    def test_an_edit_is_detected(self):
        with self.signed() as ledger:
            ledger.db.execute("UPDATE events SET data='{\"tampered\": true}' WHERE seq=1")
            report = ledger.verify_chain()
            self.assertFalse(report["ok"])
            self.assertEqual(report["broken_at"], 1)
            self.assertEqual(report["reason"], "changed after it was written")

    def test_an_edit_to_the_type_is_detected(self):
        with self.signed() as ledger:
            ledger.db.execute("UPDATE events SET type='task.approved' WHERE seq=1")
            report = ledger.verify_chain()
            self.assertFalse(report["ok"])

    def test_a_deletion_in_the_middle_is_detected(self):
        with self.signed() as ledger:
            ledger.db.execute(
                "INSERT INTO events(type,task_id,data,created,digest) VALUES"
                " ('task.created','M-1','{}',0,'')"
            )
            ledger.db.execute("DELETE FROM events WHERE seq=2")
            report = ledger.verify_chain()
            self.assertFalse(report["ok"])

    def test_an_event_written_before_signing_is_named_as_such(self):
        with Ledger(self.path) as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        with Ledger(self.path, chain_key="a-chain-key") as ledger:
            report = ledger.verify_chain()
            self.assertFalse(report["ok"])
            self.assertEqual(report["reason"], "written before the ledger was signed")

    def test_another_key_does_not_verify(self):
        with self.signed(key="key-one") as ledger:
            pass
        with Ledger(self.path, chain_key="key-two") as ledger:
            report = ledger.verify_chain()
            self.assertFalse(report["ok"])

    def test_an_empty_ledger_verifies(self):
        with Ledger(self.path, chain_key="a-chain-key") as ledger:
            report = ledger.verify_chain()
            self.assertTrue(report["ok"])
            self.assertEqual(report["events"], 0)


if __name__ == "__main__":
    unittest.main()
