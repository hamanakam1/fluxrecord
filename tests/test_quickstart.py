"""The Quick Start in README.md, executed verbatim.

If the documented interface drifts from the CLI, this fails.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class QuickStartTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def flux(self, *args):
        cmd = [sys.executable, "-m", "fluxrecord", "--db", str(self.path), *args]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_readme_quick_start_runs_end_to_end(self):
        added = self.flux(
            "task-add",
            "M-397001",
            "--objective",
            "Implement JWT auth",
            "--acceptance",
            "Tokens issued + refresh + revoke work",
            "--scope",
            "repo/api",
            "--scope",
            "repo/auth",
            "--budget",
            "8000",
        )
        self.assertEqual(added["id"], "M-397001")
        self.assertEqual(added["status"], "ready")
        self.assertEqual(added["token_budget"], 8000)

        started = self.flux("start", "M-397001", "--owner", "terminal-1", "--lease", "900")
        session = started["session_id"]

        checkpointed = self.flux(
            "checkpoint",
            session,
            "--summary",
            "Implemented token issuance",
            "--evidence",
            "Diff abc123; POST /api/auth/login works",
            "--next",
            "Refresh rotation",
            "--tokens",
            "1200",
        )
        self.assertEqual(checkpointed["tokens"], 1200)

        finished = self.flux("finish", session, "--evidence", "All endpoints tested; diff abc123")
        self.assertEqual(finished["status"], "review")

        decided = self.flux("decide", "M-397001", "accept", "--reason", "Reviewed diff and tests")
        self.assertEqual(decided["status"], "accepted")

        status = self.flux("status")
        self.assertEqual(status["metrics"]["accepted"], 1)

    def test_decide_rejects_an_unknown_decision(self):
        self.flux(
            "task-add",
            "M-397002",
            "--objective",
            "Something",
            "--acceptance",
            "Done",
            "--scope",
            "repo/x",
        )
        cmd = [
            sys.executable,
            "-m",
            "fluxrecord",
            "--db",
            str(self.path),
            "decide",
            "M-397002",
            "sideways",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_legacy_accept_command_still_works(self):
        self.flux(
            "task-add",
            "M-397003",
            "--objective",
            "Something",
            "--acceptance",
            "Done",
            "--scope",
            "repo/y",
        )
        started = self.flux("start", "M-397003", "--owner", "terminal-1")
        session = started["session_id"]
        self.flux(
            "checkpoint",
            session,
            "--summary",
            "s",
            "--evidence",
            "e",
            "--next",
            "n",
            "--tokens",
            "10",
        )
        self.flux("finish", session, "--evidence", "evidence")
        decided = self.flux("accept", "M-397003", "--reason", "looks right")
        self.assertEqual(decided["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
