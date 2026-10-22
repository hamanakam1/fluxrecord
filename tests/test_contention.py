"""Two workers contending for the same scope.

The scope reservation is the mechanism that lets two agents work in the
same repository without overwriting each other. This checks it at the
level that matters: two separate processes, one ledger, one winner.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = """
import json, sys
sys.path.insert(0, {repo!r})
from fluxrecord.core import Conflict, Ledger

ledger = Ledger({db!r})
try:
    started = ledger.start(sys.argv[2], sys.argv[1], 900)
    print(json.dumps({{"won": True, "session": started["session_id"]}}))
except Conflict as exc:
    print(json.dumps({{"won": False, "error": str(exc)}}))
finally:
    ledger.close()
"""


class ContentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"
        self.repo = Path(__file__).resolve().parent.parent

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        from fluxrecord.core import Ledger

        with Ledger(self.db) as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/shared"])
            ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/shared"])

    def run_worker(self, owner, task="M-2"):
        script = self.root / "worker.py"
        script.write_text(WORKER.format(repo=str(self.repo), db=str(self.db)))
        result = subprocess.run(
            [sys.executable, str(script), owner, task],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_exactly_one_worker_wins(self):
        self.prepare()
        # Two different tickets, one shared resource.
        first = self.run_worker("terminal-1", task="M-1")
        second = self.run_worker("terminal-2", task="M-2")
        winners = [first, second]
        self.assertEqual(sum(1 for w in winners if w["won"]), 1)

    def test_the_same_ticket_cannot_be_started_twice(self):
        self.prepare()
        first = self.run_worker("terminal-1", task="M-1")
        second = self.run_worker("terminal-2", task="M-1")
        self.assertTrue(first["won"])
        self.assertFalse(second["won"])
        self.assertIn("M-1", second["error"])

    def test_the_loser_is_told_who_holds_the_reservation(self):
        self.prepare()
        self.run_worker("terminal-1", task="M-1")
        loser = self.run_worker("terminal-2", task="M-2")
        self.assertFalse(loser["won"])
        self.assertIn("repo/shared", loser["error"])
        self.assertIn("M-1", loser["error"])

    def test_the_loser_is_told_until_when_the_reservation_lasts(self):
        self.prepare()
        self.run_worker("terminal-1", task="M-1")
        loser = self.run_worker("terminal-2", task="M-2")
        self.assertIn("until", loser["error"])
        self.assertIn("terminal-1", loser["error"])

    def test_a_different_scope_is_not_contended(self):
        self.prepare()
        from fluxrecord.core import Ledger

        with Ledger(self.db) as ledger:
            ledger.add_task("M-3", "Other", "Acceptance", scope=["repo/other"])
        script = self.root / "worker3.py"
        script.write_text(
            "import json, sys\n"
            f"sys.path.insert(0, {str(self.repo)!r})\n"
            "from fluxrecord.core import Ledger\n"
            "ledger = Ledger(" + repr(str(self.db)) + ")\n"
            "started = ledger.start('M-3', 'terminal-9', 900)\n"
            "print(json.dumps({'won': True}))\n"
            "ledger.close()\n"
        )
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
