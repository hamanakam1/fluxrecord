"""Ticket file storage: the on-disk projection described in SPEC.md."""

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.files import export, render_ticket, scaffold


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / ".fluxrecord"
        self.ledger = Ledger(self.root / "ledger.sqlite")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def seed(self):
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

    def test_scaffold_creates_the_documented_layout(self):
        layout = scaffold(self.project)
        self.assertTrue(Path(layout["tickets"]).is_dir())
        self.assertTrue(Path(layout["evidence"]).is_dir())
        self.assertTrue(Path(layout["index"]).is_file())
        self.assertTrue(layout["created"])

    def test_scaffold_is_idempotent(self):
        scaffold(self.project)
        again = scaffold(self.project)
        self.assertFalse(again["created"])

    def test_export_writes_a_ticket_file(self):
        self.seed()
        result = export(self.ledger, self.project)
        self.assertEqual(result["tasks"], 1)
        ticket = (self.project / "tickets" / "M-397001.md").read_text()
        self.assertTrue(ticket.startswith("---\n"))
        self.assertIn('id: "M-397001"', ticket)
        self.assertIn('title: "JWT auth"', ticket)
        self.assertIn("priority: 1", ticket)
        self.assertIn('scope: ["repo/api", "repo/auth"]', ticket)
        self.assertIn('tags: ["security"]', ticket)
        self.assertIn("## Acceptance Criteria", ticket)
        self.assertIn("- [ ] Tokens issued + refresh + revoke work", ticket)
        self.assertIn("created_at:", ticket)

    def test_export_writes_evidence_files(self):
        self.seed()
        export(self.ledger, self.project)
        folder = self.project / "evidence" / "M-397001"
        checkpoint = json.loads((folder / "checkpoint-1.json").read_text())
        self.assertEqual(checkpoint["sequence"], 1)
        self.assertEqual(checkpoint["summary"], "Implemented token issuance")
        self.assertEqual(checkpoint["tokens"], 1200)
        self.assertTrue(checkpoint["evidence_hash"].startswith("sha256:"))
        self.assertTrue((folder / "finish.json").is_file())

    def test_export_writes_the_index(self):
        self.seed()
        export(self.ledger, self.project)
        index = json.loads((self.project / "index.json").read_text())
        self.assertEqual(index["M-397001"]["status"], "review")
        self.assertEqual(index["M-397001"]["title"], "JWT auth")
        self.assertEqual(index["M-397001"]["priority"], 1)

    def test_export_of_an_empty_ledger_is_still_valid(self):
        result = export(self.ledger, self.project)
        self.assertEqual(result["tasks"], 0)
        self.assertEqual(json.loads((self.project / "index.json").read_text()), {})

    def test_the_rendered_ticket_has_every_section_of_the_template(self):
        template = (Path(__file__).resolve().parent.parent / "templates" / "ticket.md").read_text()
        headings = [line for line in template.splitlines() if line.startswith("## ")]
        self.assertTrue(headings)
        self.seed()
        export(self.ledger, self.project)
        rendered = (self.project / "tickets" / "M-397001.md").read_text()
        for heading in headings:
            self.assertIn(heading, rendered, heading)

    def test_a_ticket_id_cannot_walk_out_of_the_project(self):
        with self.assertRaises(ValueError):
            self.ledger.add_task("../../escape", "Objective", "Acceptance", scope=["repo/x"])

    def test_a_ticket_id_cannot_be_a_path(self):
        for bad in ("../escape", "a/b", "a\\b", ".", "..", "with space"):
            with self.assertRaises(ValueError):
                self.ledger.add_task(bad, "Objective", "Acceptance", scope=["repo/x"])

    def test_export_refuses_to_write_outside_the_project(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        self.ledger.db.execute("UPDATE tasks SET id='../../escape' WHERE id='M-1'")
        with self.assertRaises(ValueError):
            export(self.ledger, self.project)
        self.assertFalse(list(self.project.parent.glob("escape.md")))

    def test_a_ticket_without_evidence_still_renders(self):
        rendered = render_ticket(
            {
                "id": "M-1",
                "objective": "Do a thing",
                "acceptance": "It is done",
                "status": "ready",
                "kind": "code",
                "scope": [],
                "token_budget": 100,
                "tags": [],
                "depends_on": [],
                "blocks": [],
                "owner": "",
                "seed_key": "",
                "created": 0,
                "updated_at": 0,
            },
            {"events": [], "checkpoints": [], "failures": []},
        )
        self.assertIn("## Failures", rendered)
        self.assertIn("- none", rendered)


if __name__ == "__main__":
    unittest.main()
