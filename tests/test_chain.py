"""The event chain: proof that the log was not rewritten.

A ledger is only worth trusting if you can tell when it has been changed
behind your back, and a chain that nobody tests is a chain that quietly
stops working. So the chain is tested here the way it is used: by editing
the ledger underneath it and checking that the ledger notices.

What the chain does and does not prove is stated plainly, because a
security claim that overstates itself is worse than none:

  - It proves the log has not been edited, to somebody who does not have
    the key. Without the key, an editor cannot compute a valid digest for
    a changed event.
  - It catches an edit in the middle, a deletion in the middle, and an
    insertion, because each event's digest covers the one before it.
  - It catches a truncation at the end, because the head of the chain is
    recorded beside the log and compared on every check. An editor who
    can write to the file can update that head too, so this catches
    accidents and casual edits, not a determined attacker.
  - It is not a defence against somebody who has the key. That is what
    the key is for.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.secrets import event_chain_key

KEY = "a-signing-key-that-is-long-enough-to-be-a-secret"


class ChainTest(unittest.TestCase):
    """The chain, exercised against a ledger somebody has tampered with."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path, chain_key=KEY)
        for index in range(1, 4):
            self.ledger.add_task(
                f"M-39700{index}",
                f"objective {index}",
                f"acceptance {index}",
                scope=[f"repo/{index}"],
            )
        # A session, so the log holds events that carry data as well as
        # events that do not.
        session = self.ledger.start("M-397001", "terminal-1", 900)["session_id"]
        self.ledger.checkpoint(session, "a summary", "an evidence", "a next step", 100)

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def tamper(self, statement: str) -> None:
        """Change the ledger behind the chain's back."""
        self.ledger.close()
        database = sqlite3.connect(self.path)
        try:
            database.execute(statement)
            database.commit()
        finally:
            database.close()
        self.ledger = Ledger(self.path, chain_key=KEY)

    # ----------------------------------------------------------------- good

    def test_a_signed_ledger_verifies(self):
        report = self.ledger.verify_chain()
        self.assertTrue(report["signed"])
        self.assertTrue(report["ok"])
        self.assertIsNone(report["broken_at"])
        self.assertGreater(report["events"], 0)

    def test_an_unsigned_ledger_says_it_cannot_be_checked(self):
        unsigned = Ledger(Path(self.temp.name) / "unsigned.sqlite")
        try:
            unsigned.add_task("M-1", "o", "a", scope=["s"])
            report = unsigned.verify_chain()
            self.assertFalse(report["signed"])
            self.assertIn("not signed", report["note"])
        finally:
            unsigned.close()

    def test_the_chain_is_deterministic(self):
        """The same events, the same digests, every time."""
        first = [event["digest"] for event in self.ledger.events()]
        second = [event["digest"] for event in self.ledger.events()]
        self.assertEqual(first, second)
        self.assertTrue(all(first), "every event of a signed ledger carries a digest")

    def test_an_empty_signed_ledger_verifies(self):
        empty = Ledger(Path(self.temp.name) / "empty.sqlite", chain_key=KEY)
        try:
            report = empty.verify_chain()
            self.assertTrue(report["signed"])
            self.assertTrue(report["ok"])
            self.assertEqual(report["events"], 0)
        finally:
            empty.close()

    # ----------------------------------------------------------------- bad

    def test_an_edited_event_breaks_the_chain(self):
        """Change the moment an event was recorded, and the chain notices."""
        self.tamper("UPDATE events SET created=1.0 WHERE seq=2")
        report = self.ledger.verify_chain()
        self.assertFalse(report["ok"])
        self.assertEqual(report["broken_at"], 2)
        self.assertIn("changed", report["note"])

    def test_an_edited_payload_breaks_the_chain(self):
        """The payload is covered by the digest, not just the timestamp."""
        row = self.ledger.db.execute(
            "SELECT seq FROM events WHERE data <> '{}' ORDER BY seq LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row, "the scenario has an event that carries data")
        self.tamper(f"UPDATE events SET data='{{\"forged\": true}}' WHERE seq={row['seq']}")
        report = self.ledger.verify_chain()
        self.assertFalse(report["ok"])
        self.assertEqual(report["broken_at"], int(row["seq"]))

    def test_a_deleted_event_breaks_the_chain(self):
        self.tamper("DELETE FROM events WHERE seq=2")
        report = self.ledger.verify_chain()
        self.assertFalse(report["ok"])
        self.assertIsNotNone(report["broken_at"])

    def test_an_inserted_event_breaks_the_chain(self):
        self.tamper(
            "INSERT INTO events(seq,type,task_id,data,created,digest) "
            "VALUES(99,'forged.event','M-397001','{}',1.0,'deadbeef')"
        )
        report = self.ledger.verify_chain()
        self.assertFalse(report["ok"])

    def test_a_truncated_log_is_detected(self):
        """A chain is a prefix, so the head is recorded beside it."""
        self.tamper("DELETE FROM events WHERE seq > 2")
        report = self.ledger.verify_chain()
        self.assertFalse(report["ok"])
        self.assertIn("no longer ends", report["note"])

    def test_the_head_is_reported_so_it_can_be_pinned(self):
        report = self.ledger.verify_chain()
        self.assertEqual(report["head"], self.ledger.events()[-1]["digest"])


class ChainSecretTest(unittest.TestCase):
    """Where the key comes from, and where it must not come from."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = {
            name: os.environ.get(name) for name in ("FLUX_EVENT_KEY", "FLUX_EVENT_KEY_FILE")
        }
        for name in self.previous:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp.cleanup()

    def test_no_key_configured_means_no_key(self):
        self.assertIsNone(event_chain_key())

    def test_the_key_comes_from_the_environment(self):
        os.environ["FLUX_EVENT_KEY"] = KEY
        self.assertEqual(event_chain_key(), KEY)

    def test_the_key_comes_from_a_named_file(self):
        path = self.root / "key.txt"
        path.write_text(f"{KEY}\n", encoding="utf-8")
        os.environ["FLUX_EVENT_KEY_FILE"] = str(path)
        self.assertEqual(event_chain_key(), KEY)

    def test_the_key_is_never_an_argument(self):
        """A secret in an argument is a secret in the shell history."""
        import fluxrecord.__main__ as cli

        signature = cli.open_ledger.__doc__ or ""
        self.assertIn("never from an argument", signature)
        source = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--chain-key", source)
        self.assertNotIn("--event-key", source)


class VerifyCommandTest(unittest.TestCase):
    """`flux verify`, as a user runs it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def flux(self, *args, expect=0, key=KEY):
        environment = dict(os.environ)
        environment["FLUX_DB"] = str(self.db)
        if key:
            environment["FLUX_EVENT_KEY"] = key
        else:
            environment.pop("FLUX_EVENT_KEY", None)
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord", *args],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def test_a_signed_ledger_verifies_from_the_shell(self):
        self.flux("init", "--project", ".fluxrecord")
        self.flux("task-add", "M-1", "--objective", "o", "--acceptance", "a", "--scope", "s")
        report = json.loads(self.flux("verify").stdout)
        self.assertTrue(report["signed"])
        self.assertTrue(report["ok"])

    def test_a_broken_chain_fails_the_command(self):
        self.flux("init", "--project", ".fluxrecord")
        self.flux("task-add", "M-1", "--objective", "o", "--acceptance", "a", "--scope", "s")
        database = sqlite3.connect(self.db)
        try:
            database.execute("UPDATE events SET created=1.0 WHERE seq=1")
            database.commit()
        finally:
            database.close()
        result = self.flux("verify", expect=2)
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])

    def test_an_unsigned_ledger_passes_unless_signed_is_asked_for(self):
        self.flux("init", "--project", ".fluxrecord", key=None)
        self.flux(
            "task-add", "M-1", "--objective", "o", "--acceptance", "a", "--scope", "s", key=None
        )
        report = json.loads(self.flux("verify", key=None).stdout)
        self.assertFalse(report["signed"])
        self.flux("verify", "--signed", expect=2, key=None)

    def test_the_key_never_appears_in_the_output(self):
        self.flux("init", "--project", ".fluxrecord")
        self.flux("task-add", "M-1", "--objective", "o", "--acceptance", "a", "--scope", "s")
        result = self.flux("verify")
        self.assertNotIn(KEY, result.stdout)
        self.assertNotIn(KEY, result.stderr)


if __name__ == "__main__":
    unittest.main()
