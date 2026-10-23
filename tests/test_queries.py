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

    def test_a_compact_list_is_a_row_per_ticket(self):
        rows = self.ledger.list_tasks(compact=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "M-1")
        self.assertEqual(rows[0]["status"], "ready")
        self.assertIn("checkpoints", rows[0])
        self.assertNotIn("seed_key", rows[0])

    def test_a_compact_row_counts_what_is_attached(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.add_context("M-1", "context", "note")
        row = self.ledger.list_tasks(compact=True)[0]
        self.assertEqual(row["checkpoints"], 1)
        self.assertEqual(row["contexts"], 1)
        self.assertEqual(row["owner"], "terminal-1")


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_declared_profiles_are_visible(self):
        self.ledger.profile("local-coder", ["code"], 16000, "zero_incremental")
        profiles = self.ledger.status()["profiles"]
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["id"], "local-coder")
        self.assertEqual(profiles[0]["kinds"], ["code"])
        self.assertEqual(profiles[0]["cost"], "zero_incremental")

    def test_a_profile_is_a_declaration_not_a_verification(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        self.ledger.profile("local-coder", ["code"], 16000, "zero_incremental")
        route = self.ledger.route("M-1")
        self.assertFalse(route["execution_authorized"])
        self.assertEqual(route["options"][0]["provenance"], "operator declaration")


class ToolRequirementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_profile_can_declare_the_tools_it_needs(self):
        self.ledger.profile("coder", ["code"], 16000, "zero_incremental", requires=["git", "gh"])
        profile = self.ledger.status()["profiles"][0]
        self.assertEqual(sorted(profile["requires"]), ["gh", "git"])

    def test_a_missing_tool_makes_a_profile_ineligible(self):
        self.ledger.profile("coder", ["code"], 16000, "zero_incremental", requires=["nosuchtool"])
        route = self.ledger.route("M-1", available_tools={"git", "gh"})
        option = route["options"][0]
        self.assertFalse(option["eligible"])
        self.assertEqual(option["missing_tools"], ["nosuchtool"])
        self.assertIn("nosuchtool", option["reasons"][0])

    def test_a_profile_is_eligible_when_its_tools_are_there(self):
        self.ledger.profile("coder", ["code"], 16000, "zero_incremental", requires=["git"])
        route = self.ledger.route("M-1", available_tools={"git", "gh"})
        option = route["options"][0]
        self.assertTrue(option["eligible"])
        self.assertEqual(option["missing_tools"], [])

    def test_tool_availability_is_not_assumed(self):
        self.ledger.profile("coder", ["code"], 16000, "zero_incremental", requires=["git"])
        route = self.ledger.route("M-1")
        option = route["options"][0]
        self.assertTrue(option["eligible"])
        self.assertEqual(option["missing_tools"], [])


class ShowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Fix search", "Regression passes", scope=["repo/search"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_show_gathers_everything_about_a_ticket(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.fail(started["session_id"], "tool", "Compiler missing")
        view = self.ledger.show("M-1")
        self.assertEqual(view["ticket"]["id"], "M-1")
        self.assertEqual(len(view["sessions"]), 1)
        self.assertEqual(len(view["checkpoints"]), 1)
        self.assertEqual(len(view["failures"]), 1)
        self.assertTrue(view["events"])

    def test_show_refuses_an_unknown_ticket(self):
        with self.assertRaises(ValueError):
            self.ledger.show("M-999")

    def test_context_metadata_reports_utf8_bytes_without_returning_bodies(self):
        for body in ("ASCII", "界🚀", "a\x00b"):
            with self.subTest(body=body):
                attached = self.ledger.add_context("M-1", body, "synthetic reference")
                expected_bytes = len(body.encode("utf-8"))
                self.assertEqual(attached["bytes"], expected_bytes)
                for entries in (
                    self.ledger.context_of("M-1"),
                    self.ledger.show("M-1")["context"],
                ):
                    entry = next(row for row in entries if row["hash"] == attached["hash"])
                    self.assertEqual(entry["bytes"], expected_bytes)
                    self.assertNotIn("body", entry)


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
