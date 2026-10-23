"""The ticket file is a complete projection: it can be read back."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.files import export, read_ticket


class ReadBackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.project = self.root / "project"

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def build(self):
        self.ledger.add_task(
            "M-397001",
            "Implement JWT auth",
            "Tokens issued + refresh + revoke work",
            scope=["repo/api", "repo/auth"],
            title="JWT auth",
            priority=1,
            tags=["security"],
            seed_key="auth-v1",
        )
        started = self.ledger.start("M-397001", "terminal-1")
        self.ledger.checkpoint(
            started["session_id"], "Implemented token issuance", "Diff abc123", "Refresh", 1200
        )
        self.ledger.finish(started["session_id"], "All endpoints tested")
        export(self.ledger, self.project)

    def test_a_ticket_can_be_read_back_from_its_file(self):
        self.build()
        path = self.project / "tickets" / "M-397001.md"
        ticket = read_ticket(path)
        self.assertEqual(ticket["id"], "M-397001")
        self.assertEqual(ticket["title"], "JWT auth")
        self.assertEqual(ticket["status"], "review")
        self.assertEqual(ticket["priority"], 1)
        self.assertEqual(ticket["scope"], ["repo/api", "repo/auth"])
        self.assertEqual(ticket["tags"], ["security"])
        self.assertEqual(ticket["seed_key"], "auth-v1")
        self.assertEqual(ticket["description"], "Implement JWT auth")
        self.assertEqual(ticket["acceptance"], "Tokens issued + refresh + revoke work")

    def test_the_evidence_log_survives_the_round_trip(self):
        self.build()
        path = self.project / "tickets" / "M-397001.md"
        ticket = read_ticket(path)
        actions = [row["action"] for row in ticket["evidence_log"]]
        self.assertIn("task.created", actions)
        self.assertIn("session.finished", actions)
        for row in ticket["evidence_log"]:
            self.assertNotEqual(row["at"], "-----------")

    def test_a_value_with_a_comma_in_it_survives_the_round_trip(self):
        self.ledger.add_task(
            "M-397002",
            "Objective",
            "Acceptance",
            scope=["repo/a,b", "repo/c"],
            tags=["one,two"],
        )
        export(self.ledger, self.project)
        ticket = read_ticket(self.project / "tickets" / "M-397002.md")
        self.assertEqual(ticket["scope"], ["repo/a,b", "repo/c"])
        self.assertEqual(ticket["tags"], ["one,two"])

    def test_evidence_table_values_survive_pipes_newlines_and_literal_markup(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance")
        owner = "worker|synthetic\nsecond line"
        started = self.ledger.start("M-1", owner)
        summary = r"Checked a\b | c & <br> &#124;"
        evidence = "First | result\n## Description\r\nSecond <result> &amp; before\u2028after"
        self.ledger.checkpoint(started["session_id"], summary, evidence, "Review", 1)
        self.ledger.finish(started["session_id"], evidence)

        export(self.ledger, self.project)
        path = self.project / "tickets" / "M-1.md"
        ticket = read_ticket(path)
        rows = ticket["evidence_log"]

        self.assertEqual(ticket["description"], "Objective")
        self.assertEqual(
            next(row for row in rows if row["action"] == "session.started")["actor"], owner
        )
        self.assertEqual(
            next(row for row in rows if row["action"] == "session.finished")["evidence"], evidence
        )
        checkpoint = next(row for row in rows if row["action"] == "checkpoint")
        self.assertEqual(checkpoint["evidence"], f"{summary} — {evidence}")
        table = path.read_text().split("## Evidence Log\n", 1)[1].split("## Failures\n", 1)[0]
        self.assertNotIn("## Description", table.splitlines())
        self.assertTrue(
            all(line.count("|") == 5 for line in table.split("\n") if line.startswith("|"))
        )

    def test_legacy_evidence_tables_with_escaped_pipes_are_read(self):
        path = self.root / "legacy.md"
        path.write_text(
            '---\nid: "M-1"\n---\n\n## Evidence Log\n\n'
            "| Timestamp | Actor | Action | Evidence |\n"
            "|---|---|---|---|\n"
            r"| 2026-01-01 | worker | checkpoint | before \| after |" + "\n"
        )
        self.assertEqual(read_ticket(path)["evidence_log"][0]["evidence"], "before | after")

    def test_legacy_evidence_html_is_not_reinterpreted(self):
        path = self.root / "legacy-html.md"
        literal = "<br> &amp; &#124;"
        path.write_text(
            '---\nid: "M-1"\n---\n\n## Evidence Log\n\n'
            "| Timestamp | Actor | Action | Evidence |\n"
            "|---|---|---|---|\n"
            f"| 2026-01-01 | worker | checkpoint | {literal} |\n"
        )
        self.assertEqual(read_ticket(path)["evidence_log"][0]["evidence"], literal)

    def test_a_file_with_no_front_matter_is_refused(self):
        path = self.root / "not-a-ticket.md"
        path.write_text("# just a heading\n")
        with self.assertRaises(ValueError):
            read_ticket(path)

    def test_quoted_scalar_values_survive_export_and_read(self):
        for number, title in enumerate(
            ("A --- B", "café 東京", 'Say "hello"', r"repo\folder", "First\nSecond")
        ):
            with self.subTest(title=title):
                task_id = f"T-{number}"
                self.ledger.add_task(
                    task_id, "Objective", "Acceptance", scope=["repo/x"], title=title
                )
                export(self.ledger, self.project)
                ticket = read_ticket(self.project / "tickets" / f"{task_id}.md")
                self.assertEqual(ticket["title"], title)
                self.assertEqual(ticket["scope"], ["repo/x"])
                self.assertEqual(ticket["description"], "Objective")

    def test_front_matter_requires_whole_line_delimiters(self):
        for contents in ('---suffix\nid: "T-1"\n---\n', '---\ntitle: "A --- B"\n'):
            with self.subTest(contents=contents):
                path = self.root / "incomplete.md"
                path.write_text(contents)
                with self.assertRaises(ValueError):
                    read_ticket(path)

    def test_the_file_and_the_ledger_agree(self):
        self.build()
        path = self.project / "tickets" / "M-397001.md"
        ticket = read_ticket(path)
        ledger = self.ledger.task_view("M-397001")
        self.assertEqual(ticket["status"], ledger["status"])
        self.assertEqual(ticket["scope"], ledger["scope"])
        self.assertEqual(ticket["priority"], ledger["priority"])
        self.assertEqual(ticket["title"], ledger["title"])


if __name__ == "__main__":
    unittest.main()
