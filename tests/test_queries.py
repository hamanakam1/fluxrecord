"""Listing tickets with filters, and the context packet a worker receives."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class ListTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task(
            "M-1",
            "Fix search",
            "Regression passes",
            scope=["repo/search"],
            title="Search is broken",
            priority=1,
            tags=["search", "p1"],
            owner="terminal-1",
        )
        self.ledger.add_task(
            "M-2",
            "Add rate limiting",
            "429 after N requests",
            scope=["repo/api"],
            title="Rate limit the API",
            priority=3,
            tags=["api"],
        )

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_everything_is_listed_by_default(self):
        self.assertEqual(len(self.ledger.list_tasks()), 2)

    def test_filter_by_status(self):
        self.assertEqual([t["id"] for t in self.ledger.list_tasks(status="ready")], ["M-1", "M-2"])

    def test_filter_by_priority(self):
        found = self.ledger.list_tasks(priority=1)
        self.assertEqual([t["id"] for t in found], ["M-1"])

    def test_filter_by_tag(self):
        found = self.ledger.list_tasks(tag="api")
        self.assertEqual([t["id"] for t in found], ["M-2"])

    def test_filter_by_scope(self):
        found = self.ledger.list_tasks(scope="repo/search")
        self.assertEqual([t["id"] for t in found], ["M-1"])

    def test_filter_by_owner(self):
        found = self.ledger.list_tasks(owner="terminal-1")
        self.assertEqual([t["id"] for t in found], ["M-1"])

    def test_filters_combine(self):
        self.assertEqual(self.ledger.list_tasks(tag="search", priority=3), [])

    def test_the_limit_is_respected(self):
        self.assertEqual(len(self.ledger.list_tasks(limit=1)), 1)


class PacketTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task(
            "M-1",
            "Fix search",
            "Regression passes",
            scope=["repo/search"],
            title="Search is broken",
            priority=1,
            tags=["search"],
        )

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_the_packet_carries_the_whole_ticket(self):
        packet = self.ledger.pack("M-1")["packet"]
        self.assertEqual(packet["title"], "Search is broken")
        self.assertEqual(packet["priority"], 1)
        self.assertEqual(packet["tags"], ["search"])
        self.assertEqual(packet["kind"], "code")
        self.assertEqual(packet["scope"], ["repo/search"])

    def test_the_packet_carries_failures(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.fail(started["session_id"], "tool", "Compiler missing")
        packet = self.ledger.pack("M-1")["packet"]
        self.assertEqual(len(packet["failures"]), 1)
        self.assertEqual(packet["failures"][0]["category"], "tool")
        self.assertEqual(packet["failures"][0]["summary"], "Compiler missing")

    def test_the_packet_stays_inside_its_byte_budget(self):
        self.ledger.add_context("M-1", "x" * 500, "test")
        result = self.ledger.pack("M-1", max_bytes=600)
        self.assertLessEqual(result["packet_bytes"], 600)
        self.assertTrue(result["omitted"])


if __name__ == "__main__":
    unittest.main()
