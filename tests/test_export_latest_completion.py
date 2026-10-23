"""The finish projection describes the latest completed attempt, retaining history."""

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.files import export


class LatestCompletionExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task("T-1", "Synthetic objective", "Synthetic acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def finish_attempt(self, number):
        session = self.ledger.start("T-1", f"worker-{number}")["session_id"]
        self.ledger.checkpoint(session, f"Attempt {number}", f"Checkpoint {number}", "Review", 1)
        self.ledger.finish(session, f"Completion {number}")
        return session

    def retry_and_finish(self):
        self.ledger.decide("T-1", "retry", "Synthetic revision requested")
        return self.finish_attempt(2)

    def read_finish(self):
        return json.loads((self.project / "evidence" / "T-1" / "finish.json").read_text())

    def test_retry_replaces_finish_with_the_latest_attempt_and_preserves_history(self):
        first_session = self.finish_attempt(1)
        export(self.ledger, self.project)
        self.assertEqual(self.read_finish(), {"session": first_session, "evidence": "Completion 1"})
        second_session = self.retry_and_finish()
        self.ledger.decide("T-1", "accept", "Synthetic verification complete")
        before = self.ledger.evidence("T-1")

        export(self.ledger, self.project)

        self.assertEqual(
            self.read_finish(), {"session": second_session, "evidence": "Completion 2"}
        )
        self.assertEqual(self.ledger.evidence("T-1"), before)
        finishes = [event for event in before["events"] if event["type"] == "session.finished"]
        self.assertEqual(len(finishes), 2)
        folder = self.project / "evidence" / "T-1"
        for number, session in ((1, first_session), (2, second_session)):
            checkpoint = json.loads((folder / f"checkpoint-{number}.json").read_text())
            self.assertEqual(checkpoint["session_id"], session)
        ticket = (self.project / "tickets" / "T-1.md").read_text()
        self.assertIn("Completion 1", ticket)
        self.assertIn("Completion 2", ticket)

    def test_empty_latest_data_does_not_fall_back_to_an_older_completion(self):
        self.finish_attempt(1)
        second_session = self.retry_and_finish()
        sequence = self.ledger.db.execute(
            "SELECT MAX(seq) FROM events WHERE type='session.finished'"
        ).fetchone()[0]
        for payload in ({}, {"session": second_session, "evidence": ""}):
            with self.subTest(payload=payload):
                # Imported or historical event data may contain empty values.
                self.ledger.db.execute(
                    "UPDATE events SET data=? WHERE seq=?", (json.dumps(payload), sequence)
                )
                export(self.ledger, self.project)
                self.assertEqual(self.read_finish(), payload)

    def test_event_sequence_determines_the_latest_completion_not_wall_clock(self):
        self.finish_attempt(1)
        second_session = self.retry_and_finish()
        self.ledger.db.execute(
            "UPDATE events SET created=1 WHERE seq=("
            "SELECT MAX(seq) FROM events WHERE type='session.finished')"
        )

        export(self.ledger, self.project)

        self.assertEqual(
            self.read_finish(), {"session": second_session, "evidence": "Completion 2"}
        )


if __name__ == "__main__":
    unittest.main()
