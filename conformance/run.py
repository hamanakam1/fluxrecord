#!/usr/bin/env python3
"""Run the conformance suite against an implementation of the standard.

The standard is the shapes in `docs/wire-format.schema.json`. This runner
takes an executable that speaks the standard, runs a fixed sequence of
commands against it, and checks every report against the published
schema.

It is deliberately not a test suite for this package: it knows nothing
about fluxrecord's internals, only about the wire format. Point it at a
different implementation and it means the same thing:

    python3 conformance/run.py --command "python3 -m fluxrecord"
    python3 conformance/run.py --command "my-ledger" --db /tmp/check.sqlite

The report is on stdout, one line per check, and the exit code is the
verdict: zero when every check passed, one otherwise. A JSON report can
be asked for with `--json`, for a build to consume.

The sequence is fixed on purpose. A conformance suite that let the
implementation choose what to run would check nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "docs" / "wire-format.schema.json"


def published() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validator_for(schema: dict, shape: str):
    """A validator for one shape of the published schema."""
    from jsonschema import Draft202012Validator

    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$ref": f"#/$defs/{shape}",
            "$defs": schema["$defs"],
        }
    )


class Runner:
    """Runs a command and checks what it prints."""

    def __init__(self, command: str, db: str, project: str) -> None:
        self.command = shlex.split(command)
        self.db = db
        self.project = project
        self.schema = published()

    def run(self, *args: str, expect: int = 0) -> tuple[dict, str, int]:
        environment = dict(os.environ)
        environment["FLUX_DB"] = self.db
        result = subprocess.run(
            [*self.command, *args],
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode != expect:
            raise RuntimeError(
                f"{' '.join(args)} exited {result.returncode}, expected {expect}\n"
                f"{result.stderr.strip()}"
            )
        try:
            return (
                json.loads(result.stdout) if result.stdout.strip() else {},
                result.stdout,
                result.returncode,
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{' '.join(args)} did not print JSON: {exc}") from exc


def checks(runner: Runner) -> list[tuple[str, str, object]]:
    """The fixed sequence, and what each report is checked against.

    Every check is a (name, shape, value) triple. The shape is the name of
    a definition in the published schema.
    """
    results: list[tuple[str, str, object]] = []

    def field(report: dict, name: str, label: str) -> object:
        """A field a report must have.

        An implementation that does not return it has not implemented the
        standard, and that is a conformance failure rather than a crash:
        the person running the suite wants a report, not a traceback.
        """
        if name not in report:
            raise RuntimeError(f"{label} did not return {name!r}")
        return report[name]

    runner.run("init", "--project", runner.project)
    runner.run(
        "task-add",
        "M-000001",
        "--objective",
        "the first",
        "--acceptance",
        "it is done",
        "--scope",
        "repo/one",
        "--title",
        "First",
    )
    runner.run(
        "task-add",
        "M-000002",
        "--objective",
        "the second",
        "--acceptance",
        "it is reviewed",
        "--scope",
        "repo/two",
    )
    started, _out, _code = runner.run("start", "M-000002", "--owner", "terminal-1")
    runner.run(
        "checkpoint",
        field(started, "session_id", "start"),
        "--summary",
        "a summary",
        "--evidence",
        "an evidence",
        "--next",
        "a next step",
        "--tokens",
        "100",
    )
    runner.run("finish", field(started, "session_id", "start"), "--evidence", "the evidence")
    runner.run("decide", "M-000001", "cancel", "--reason", "not wanted")

    listing, _out, _code = runner.run("list")
    for ticket in listing.get("tickets", []):
        results.append(("list ticket", "ticket", ticket))

    shown, _out, _code = runner.run("show", "M-000002")
    results.append(("show ticket", "ticket", field(shown, "ticket", "show")))

    packed, _out, _code = runner.run("pack", "M-000002")
    results.append(("pack", "packet", field(packed, "packet", "pack")))

    sessions, _out, _code = runner.run("sessions")
    for session in sessions.get("sessions", []):
        results.append(("session", "session", session))

    board, _out, _code = runner.run("status", "--summary")
    for row in board.get("board", []):
        results.append(("board row", "board_row", row))

    version, _out, _code = runner.run("version")
    results.append(("version", "version", version))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--command",
        default=f"{sys.executable} -m fluxrecord",
        help="the executable to check, with its arguments",
    )
    parser.add_argument("--db", default=None, help="the ledger to use")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="flux-conformance-") as tmp:
        root = Path(tmp)
        db = args.db or str(root / "ledger.sqlite")
        project = str(root / "project")
        runner = Runner(args.command, db, project)
        try:
            found = checks(runner)
        except RuntimeError as exc:
            if args.json:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            else:
                print(f"FAILED: {exc}", file=sys.stderr)
            return 1

        failures: list[str] = []
        for name, shape, value in found:
            if shape == "version":
                # The version report is not a frozen shape, but an
                # implementation has to say which wire format it speaks.
                if "wire_format" not in value:
                    failures.append(f"{name}: the version report does not name a wire format")
                continue
            errors = list(validator_for(runner.schema, shape).iter_errors(value))
            if errors:
                where = "/".join(str(part) for part in errors[0].path) or "(the whole document)"
                failures.append(f"{name}: {where}: {errors[0].message}")

        if args.json:
            print(
                json.dumps(
                    {
                        "ok": not failures,
                        "checks": len(found),
                        "failures": failures,
                    },
                    indent=2,
                )
            )
        else:
            for name, shape, value in found:
                status = "ok"
                if any(failure.startswith(name) for failure in failures):
                    status = "FAILED"
                print(f"{status:>6}  {name} ({shape})")
            if failures:
                print()
                for failure in failures:
                    print(f"  {failure}", file=sys.stderr)
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
