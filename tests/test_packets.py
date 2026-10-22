"""Content addressing: a packet's hash is the address of its content."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class PacketAddressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_packet_has_a_hash(self):
        packed = self.ledger.pack("M-1")
        self.assertTrue(packed["packet_hash"].startswith("sha256:"))

    def test_an_unchanged_packet_has_the_same_hash(self):
        first = self.ledger.pack("M-1")
        second = self.ledger.pack("M-1")
        self.assertEqual(first["packet_hash"], second["packet_hash"])

    def test_a_changed_packet_has_a_different_hash(self):
        first = self.ledger.pack("M-1")
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        second = self.ledger.pack("M-1")
        self.assertNotEqual(first["packet_hash"], second["packet_hash"])

    def test_attached_context_changes_the_hash(self):
        first = self.ledger.pack("M-1")
        self.ledger.add_context("M-1", "some context", "note")
        second = self.ledger.pack("M-1")
        self.assertNotEqual(first["packet_hash"], second["packet_hash"])


class DeltaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_nothing_changed_means_no_delta(self):
        first = self.ledger.pack("M-1")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertFalse(again["delta"]["changed"])
        self.assertEqual(again["delta"]["delta"]["checkpoints"], [])

    def test_a_checkpoint_shows_up_in_the_delta(self):
        first = self.ledger.pack("M-1")
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "a summary", "an evidence", "next", 10)
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertTrue(again["delta"]["changed"])
        self.assertEqual(len(again["delta"]["delta"]["checkpoints"]), 1)
        self.assertEqual(again["delta"]["delta"]["checkpoints"][0]["summary"], "a summary")

    def test_a_failure_shows_up_in_the_delta(self):
        first = self.ledger.pack("M-1")
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.fail(started["session_id"], "tool", "the tool would not run")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertTrue(again["delta"]["changed"])
        self.assertEqual(again["delta"]["delta"]["failures"][0]["category"], "tool")

    def test_new_context_shows_up_in_the_delta(self):
        first = self.ledger.pack("M-1")
        self.ledger.add_context("M-1", "some context", "note")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertTrue(again["delta"]["changed"])
        self.assertEqual(len(again["delta"]["delta"]["context"]), 1)

    def test_a_delta_is_smaller_than_the_packet(self):
        self.ledger.add_context("M-1", "x" * 500, "note")
        first = self.ledger.pack("M-1")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertLess(again["delta"]["delta_bytes"], again["packet_bytes"])

    def test_a_status_change_shows_up_in_the_delta(self):
        first = self.ledger.pack("M-1")
        self.ledger.start("M-1", "terminal-1")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertTrue(again["delta"]["changed"])
        self.assertTrue(again["delta"]["status_changed"])
        self.assertEqual(again["delta"]["delta"]["status"], "in_progress")

    def test_a_moved_ticket_without_new_evidence_is_still_a_change(self):
        first = self.ledger.pack("M-1")
        self.ledger.start("M-1", "terminal-1")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertTrue(again["delta"]["changed"])
        self.assertEqual(again["delta"]["delta"]["checkpoints"], [])

    def test_an_unknown_hash_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.pack("M-1", since="sha256:" + "0" * 64)

    def test_a_hash_from_another_ticket_is_refused(self):
        self.ledger.add_task("M-2", "Other", "Acceptance", scope=["repo/y"])
        other = self.ledger.pack("M-2")
        with self.assertRaises(ValueError):
            self.ledger.pack("M-1", since=other["packet_hash"])

    def test_the_delta_says_what_it_is(self):
        first = self.ledger.pack("M-1")
        again = self.ledger.pack("M-1", since=first["packet_hash"])
        self.assertEqual(again["delta"]["since"], first["packet_hash"])
        self.assertEqual(again["delta"]["packet_hash"], first["packet_hash"])


if __name__ == "__main__":
    unittest.main()
