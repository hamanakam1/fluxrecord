"""The conformance runner, and the standard it enforces.

`conformance/run.py` is the standard's teeth. It runs a fixed sequence of
commands against an implementation and checks every report against the
published schema. It knows nothing about fluxrecord's internals, so it
means the same thing pointed at somebody else's implementation.

Two things have to be true for it to be worth anything, and both are
checked here.

The first is that this package passes it. A conformance suite that the
reference implementation fails is not a standard, it is a wish.

The second is that it fails something. A suite that accepts every output
is not a check. So a deliberately broken implementation is written here —
one that answers with a ticket of the wrong shape — and the runner has to
reject it. A gate that cannot fail is decoration, and this one has to be
able to fail against a real implementation too.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.cli_support import isolated_cli_environment

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "conformance" / "run.py"

#: An implementation that answers, but answers wrongly: a ticket whose id
#: is a number, and which is missing most of the fields the standard
#: requires.
BROKEN = """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:1] == ["start"]:
    print(json.dumps({"session_id": "a-session"}))
elif args[:1] == ["list"]:
    print(json.dumps({"tickets": [{"id": 5}], "count": 1, "filters": {}}))
elif args[:1] == ["version"]:
    print(json.dumps({"name": "broken", "version": "0.0.0"}))
else:
    print(json.dumps({}))
"""


class ConformanceRunnerTest(unittest.TestCase):
    """The runner, against this package and against a broken one."""

    def test_the_reference_implementation_passes_the_conformance_suite(self):
        result = subprocess.run(
            [sys.executable, str(RUNNER)],
            capture_output=True,
            text=True,
            cwd=REPO,
            env=isolated_cli_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok", result.stdout)

    def test_the_report_can_be_read_as_json(self):
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--json"],
            capture_output=True,
            text=True,
            cwd=REPO,
            env=isolated_cli_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertGreater(report["checks"], 0)
        self.assertEqual(report["failures"], [])

    def test_the_suite_rejects_an_implementation_that_breaks_the_format(self):
        """A suite that accepts everything is not a check."""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "broken"
            script.write_text(BROKEN, encoding="utf-8")
            script.chmod(0o755)
            result = subprocess.run(
                [sys.executable, str(RUNNER), "--command", f"{sys.executable} {script}"],
                capture_output=True,
                text=True,
                cwd=REPO,
                env=isolated_cli_environment(),
            )
        self.assertEqual(result.returncode, 1, "a broken implementation was accepted")
        self.assertIn("FAILED", result.stdout + result.stderr)

    def test_the_suite_reports_an_implementation_that_does_not_answer(self):
        """No output at all is also a failure."""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "silent"
            script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            script.chmod(0o755)
            result = subprocess.run(
                [sys.executable, str(RUNNER), "--command", f"{sys.executable} {script}"],
                capture_output=True,
                text=True,
                cwd=REPO,
                env=isolated_cli_environment(),
            )
        self.assertEqual(result.returncode, 1, "an implementation that answers nothing passed")


if __name__ == "__main__":
    unittest.main()
