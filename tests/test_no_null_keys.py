"""No key is always null.

A key whose value is null in every output it appears in is a promise the
tool will never keep. It reads as "we measured this and the answer was
nothing", when the truth is usually "we cannot measure this yet" or "we
never wired this up". Two of them lived in the ledger for a while —
`measured_cash_cost` and `measured_token_savings` — and both are worse
than an absent key, because a consumer cannot tell them apart from a
number that legitimately has no value.

So the rule is checked rather than remembered. This exercises the ledger
across every state a ticket can be in, collects the value of every key in
every report, and fails if any key is null in all of them.

A key that is null *sometimes* is fine. `updated_at` on a ticket that has
never been touched would be one, and a missing measurement on one ticket
out of many is another. The rule is about keys that are null in every
case, not about keys that are ever null.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from fluxrecord.core import Ledger

#: What a container value looks like in the collected set. A key whose
#: value is sometimes a structure and sometimes null is not an always-null
#: key, and the marker is what says so.
STRUCTURE = ("structure",)


def collect(node: Any, path: str, seen: dict[str, set[Any]]) -> None:
    """Record every value seen at every key path in a report.

    A key is recorded at its own path as well as being descended into, so
    that a key which holds a structure in one report and null in another is
    not mistaken for a key that is always null.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if value is None:
                seen.setdefault(child, set()).add(None)
            elif isinstance(value, (dict, list)):
                seen.setdefault(child, set()).add(STRUCTURE)
                collect(value, child, seen)
            else:
                seen.setdefault(child, set()).add(value)
        return
    if isinstance(node, list):
        for item in node:
            collect(item, f"{path}[]", seen)
        return
    seen.setdefault(path, set()).add(node)


def always_null(seen: dict[str, set[Any]]) -> list[str]:
    """The key paths whose only observed value was null."""
    return sorted(
        path for path, values in seen.items() if values and all(value is None for value in values)
    )


class NoNullKeysTest(unittest.TestCase):
    """The ledger's reports, checked for keys that promise nothing."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite")
        self._build()

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    # ---------------------------------------------------------------- build

    def _add(self, task_id: str, scope: str) -> None:
        self.ledger.add_task(
            task_id,
            f"the objective of {task_id}",
            f"the acceptance criterion of {task_id}",
            scope=[scope],
        )

    def _build(self) -> None:
        """A ledger holding one ticket in every state the ledger can hold."""
        # ready
        self._add("M-000001", "repo/ready")

        # review
        self._add("M-000002", "repo/review")
        session = self.ledger.start("M-000002", "terminal-1", 900)["session_id"]
        self.ledger.checkpoint(session, "a summary", "an evidence", "a next step", 100)
        self.ledger.finish(session, "the evidence")

        # blocked, with a failure
        self._add("M-000003", "repo/blocked")
        session = self.ledger.start("M-000003", "terminal-2", 900)["session_id"]
        self.ledger.fail(session, "tool", "the tool would not run")

        # done
        self._add("M-000004", "repo/done")
        session = self.ledger.start("M-000004", "terminal-3", 900)["session_id"]
        self.ledger.checkpoint(session, "a summary", "an evidence", "a next step", 200)
        self.ledger.finish(session, "the evidence")
        self.ledger.decide("M-000004", "accept", "reviewed")

        # cancelled
        self._add("M-000005", "repo/cancelled")
        self.ledger.decide("M-000005", "cancel", "not wanted")

        # in progress, with a live lease
        self._add("M-000006", "repo/live")
        self.ledger.start("M-000006", "terminal-4", 900)

        # a profile, and contexts with and without an expiry
        self.ledger.profile("local-coder", ["code"], 16000, "zero_incremental")
        self.ledger.add_context("M-000001", "some context", "a source", 10)
        self.ledger.add_context("M-000001", "context that expires", "a source", 5, 3600)

    # ---------------------------------------------------------------- check

    def reports(self) -> list[tuple[str, Any]]:
        """Every report the ledger makes, across every ticket."""
        found: list[tuple[str, Any]] = [
            ("status", self.ledger.status()),
            ("list", self.ledger.list_tasks()),
            ("board", self.ledger.board()),
            ("events", self.ledger.events()),
            ("profile", self.ledger.task_view("M-000001")),
        ]
        for task_id in (
            "M-000001",
            "M-000002",
            "M-000003",
            "M-000004",
            "M-000005",
            "M-000006",
        ):
            found.append((f"show {task_id}", self.ledger.show(task_id)))
            found.append((f"evidence {task_id}", self.ledger.evidence(task_id)))
        # Pack a ticket that has a checkpoint as well as one that has not,
        # so a checkpoint is seen both present and absent.
        found.append(("pack", self.ledger.pack("M-000001")))
        found.append(("pack with a checkpoint", self.ledger.pack("M-000002")))
        found.append(("route", self.ledger.route("M-000001")))
        found.append(("sessions", self.ledger.sessions()))
        return found

    def test_no_report_has_a_key_that_is_always_null(self) -> None:
        seen: dict[str, set[Any]] = {}
        for _name, report in self.reports():
            collect(report, "report", seen)
        problems = always_null(seen)
        self.assertEqual(
            problems,
            [],
            "these keys are null in every report they appear in; a key that is "
            "always null is worse than an absent key: " + ", ".join(problems),
        )

    def test_the_ledger_reports_something(self) -> None:
        """The check above is only meaningful if the reports have content."""
        seen: dict[str, set[Any]] = {}
        for _name, report in self.reports():
            collect(report, "report", seen)
        self.assertGreater(len(seen), 40, "the reports are suspiciously small")
        self.assertTrue(
            any(values for values in seen.values()),
            "no values were collected at all",
        )


if __name__ == "__main__":
    unittest.main()
