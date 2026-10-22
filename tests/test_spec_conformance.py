"""The specification is a contract, and a contract is checked.

`SPEC.md` is the product: the ledger is the reference implementation of it.
That only works while the two agree. They have not always agreed — the
status vocabulary, the transition table, the failure taxonomy, the ticket
schema and the command list have each drifted at least once, and every
drift is a case where the published standard describes a tool that does not
exist.

So the standard is checked against the code, here, in the same way the core
ledger's boundary is checked in `test_boundaries.py`. The specification is
parsed, not trusted: if it names a status the code never produces, or the
code produces a status the specification never defines, this fails.

The failure messages say what the specification says and what the code
does, because the fix is usually a decision, not a typo.

One direction is deliberately forgiving. A status may be listed in the
specification and never stored by the ledger — `accepted` is a station the
work passes through on its way to `done` — provided the specification says
so in the lifecycle table. A status the code can store and the
specification never mentions is always a failure: that is a status the
standard does not define, and an implementer reading the specification
would not know what to do with it.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import FAILURE_CATEGORIES, Ledger
from fluxrecord.files import TICKET_FIELDS

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "SPEC.md"


# --------------------------------------------------------------------------- #
# parsing the specification
# --------------------------------------------------------------------------- #


def spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """The body of a `## heading` section, up to the next heading."""
    pattern = re.compile(r"^## " + re.escape(heading) + r".*?$(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    if match is None:
        raise AssertionError(f"the specification has no section titled {heading!r}")
    return match.group(1)


def rows(body: str) -> list[list[str]]:
    """The rows of a markdown table, as lists of cells."""
    table: list[list[str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        table.append(cells)
    return table


def backticked(text: str) -> list[str]:
    """The `code` spans in a piece of text."""
    return re.findall(r"`([^`]+)`", text)


def status_rows() -> list[tuple[str, str, str]]:
    """(status, meaning, transitions) from the Status Values table."""
    body = section(spec_text(), "Status Values")
    parsed: list[tuple[str, str, str]] = []
    for cells in rows(body)[1:]:
        if len(cells) < 3:
            continue
        names = backticked(cells[0])
        if not names:
            continue
        parsed.append((names[0], cells[1], cells[2]))
    return parsed


def station_rows() -> list[tuple[str, str, str, str]]:
    """(station, entry, exit, gate) from the Ticket Lifecycle Stations table."""
    body = section(spec_text(), "Ticket Lifecycle Stations")
    parsed: list[tuple[str, str, str, str]] = []
    for cells in rows(body)[1:]:
        if len(cells) < 4:
            continue
        name = cells[0].strip("*")
        if not name:
            continue
        parsed.append((name, cells[1], cells[2], cells[3]))
    return parsed


def decision_results() -> dict[str, str]:
    """The status each operator decision produces, from the Acceptance
    Protocol."""
    body = section(spec_text(), "Acceptance Protocol")
    found: dict[str, str] = {}
    for match in re.finditer(r"decide\((\w+)\)\s*→\s*status=(\w+)", body):
        found[match.group(1)] = match.group(2)
    for match in re.finditer(r"(\w+)\(\)\s*→\s*status=(\w+)", body):
        found.setdefault(match.group(1), match.group(2))
    return found


def spec_failure_categories() -> list[str]:
    """The failure taxonomy the specification defines."""
    body = section(spec_text(), "Failure Model")
    match = re.search(r"^category:\s*(.+)$", body, re.M)
    if match is None:
        raise AssertionError("the Failure Model section has no category line")
    return [part.strip() for part in match.group(1).split("|")]


def spec_ticket_fields() -> list[str]:
    """The front-matter fields the ticket schema defines."""
    body = section(spec_text(), "Ticket Schema")
    match = re.search(r"```markdown\n---\n(.*?)\n---", body, re.S)
    if match is None:
        raise AssertionError("the Ticket Schema section has no front matter block")
    fields: list[str] = []
    for line in match.group(1).splitlines():
        name = line.split(":", 1)[0].strip()
        if name and name not in fields:
            fields.append(name)
    return fields


def spec_commands() -> set[str]:
    """The commands the specification's CLI section names."""
    body = section(spec_text(), "CLI Interface (JSON-First)")
    block = re.search(r"```bash\n(.*?)```", body, re.S)
    if block is None:
        raise AssertionError("the CLI Interface section has no bash block")
    return set(re.findall(r"^flux ([a-z][a-z-]*)", block.group(1), re.M))


def code_commands() -> set[str]:
    """The commands the CLI actually has."""
    from fluxrecord.__main__ import parser

    names: set[str] = set()
    for action in parser()._subparsers._group_actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names.update(choices)
    return names


# --------------------------------------------------------------------------- #
# probing the code
# --------------------------------------------------------------------------- #


class LedgerFixture:
    """A ledger and the temporary directory that holds it."""

    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite")

    def close(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def task_in(self, status: str, task_id: str = "M-397001") -> Ledger:
        """A ticket driven into the given status, using only the public API."""
        led = self.ledger
        led.add_task(task_id, "an objective", "an acceptance criterion", scope=[task_id])
        if status == "ready":
            return led
        session = led.start(task_id, "terminal-1", 900)["session_id"]
        if status == "in_progress":
            return led
        if status == "blocked":
            led.fail(session, "tool", "the tool would not run")
            return led
        led.checkpoint(session, "a summary", "an evidence", "a next step", 100)
        led.finish(session, "the evidence")
        if status == "review":
            return led
        if status in ("accepted", "done"):
            led.decide(task_id, "accept", "reviewed")
            return led
        if status == "cancelled":
            led.decide(task_id, "cancel", "not wanted")
            return led
        raise AssertionError(f"no way to reach the status {status!r}")


def storable_statuses() -> set[str]:
    """The statuses the ledger can actually hold."""
    found: set[str] = set()
    for candidate in ("ready", "in_progress", "blocked", "review", "accepted", "done", "cancelled"):
        fixture = LedgerFixture()
        try:
            found.add(fixture.task_in(candidate).task_view("M-397001")["status"])
        except Exception:  # noqa: BLE001 - a status that cannot be reached is simply absent
            pass
        finally:
            fixture.close()
    return found


def decision_outcome(decision: str) -> str | None:
    """The status a decision produces, or None when the decision is refused."""
    fixture = LedgerFixture()
    try:
        led = fixture.task_in("review")
        led.decide("M-397001", decision, "a reason")
        return led.task_view("M-397001")["status"]
    except Exception:  # noqa: BLE001 - refused is a legitimate answer
        return None
    finally:
        fixture.close()


# --------------------------------------------------------------------------- #
# the tests
# --------------------------------------------------------------------------- #


class SpecificationTest(unittest.TestCase):
    """The published standard, checked against the code that implements it."""

    def test_every_status_the_code_stores_is_defined(self):
        defined = {name for name, _meaning, _transitions in status_rows()}
        stored = storable_statuses()
        self.assertTrue(defined, "the Status Values table is empty")
        undefined = stored - defined
        self.assertEqual(
            undefined,
            set(),
            f"the ledger stores statuses the specification never defines: {sorted(undefined)}",
        )

    def test_a_status_the_specification_defines_is_reachable(self):
        """A status may be listed and never stored, but only if the
        specification says it is passed through on the way somewhere."""
        defined = {name for name, _meaning, _transitions in status_rows()}
        stored = storable_statuses()
        transient = {
            name
            for name, _entry, exit_criteria, _gate in station_rows()
            if "auto" in exit_criteria.lower()
        }
        unreachable = defined - stored - transient
        self.assertEqual(
            unreachable,
            set(),
            "the specification defines statuses the ledger never holds and does "
            f"not describe as transient: {sorted(unreachable)}",
        )

    def test_every_transition_names_a_status_that_exists(self):
        defined = {name for name, _meaning, _transitions in status_rows()}
        problems: list[str] = []
        for status, _meaning, transitions in status_rows():
            for target in backticked(transitions):
                if target not in defined:
                    problems.append(f"{status} → {target}")
        self.assertEqual(
            problems,
            [],
            "the specification transitions to statuses it does not define: " + ", ".join(problems),
        )

    def test_every_status_has_a_station(self):
        defined = {name for name, _meaning, _transitions in status_rows()}
        stations = {name.lower() for name, _entry, _exit, _gate in station_rows()}
        missing = defined - stations
        self.assertEqual(
            missing,
            set(),
            f"the specification defines statuses with no lifecycle station: {sorted(missing)}",
        )

    def test_the_decisions_produce_the_status_the_specification_names(self):
        expected = decision_results()
        self.assertTrue(expected, "the Acceptance Protocol names no decisions")
        problems: list[str] = []
        for decision, status in sorted(expected.items()):
            if decision not in ("accept", "retry", "cancel"):
                continue
            actual = decision_outcome(decision)
            if actual != status:
                problems.append(
                    f"decide({decision}) produces {actual!r}, the specification says {status!r}"
                )
        self.assertEqual(problems, [], "; ".join(problems))

    def test_the_failure_taxonomy_agrees(self):
        self.assertEqual(
            list(FAILURE_CATEGORIES),
            spec_failure_categories(),
            "the failure categories in the ledger and in the specification differ",
        )

    def test_the_ticket_schema_agrees_with_the_file_format(self):
        self.assertEqual(
            spec_ticket_fields(),
            list(TICKET_FIELDS),
            "the front matter in the specification and the file the exporter "
            "writes have different fields",
        )

    def test_every_command_in_the_specification_exists(self):
        documented = spec_commands()
        available = code_commands()
        self.assertTrue(documented, "the specification names no commands")
        missing = documented - available
        self.assertEqual(
            missing,
            set(),
            f"the specification documents commands the CLI does not have: {sorted(missing)}",
        )

    def test_every_command_is_in_the_specification(self):
        documented = spec_commands()
        available = code_commands()
        undocumented = available - documented
        self.assertEqual(
            undocumented,
            set(),
            f"the CLI has commands the specification never documents: {sorted(undocumented)}",
        )

    def test_the_specification_examples_are_valid_ticket_ids(self):
        """The identifiers the specification shows must be ones the ledger
        accepts."""
        for task_id in sorted(set(re.findall(r"\bM-\d{6}\b", spec_text()))):
            with self.subTest(task_id=task_id):
                fixture = LedgerFixture()
                try:
                    fixture.ledger.add_task(task_id, "o", "a", scope=[task_id])
                finally:
                    fixture.close()


if __name__ == "__main__":
    unittest.main()
