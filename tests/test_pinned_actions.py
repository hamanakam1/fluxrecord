"""A workflow action is pinned to a commit, or it is a dependency nobody audited.

GitHub Actions run with the repository's secrets — the release job here holds
a PyPI token. An action referenced by a moving tag (`@v4`, `@release/v1`) can
be silently re-pointed at different code tomorrow, and every workflow run
would pick it up without a diff anyone reviewed. Pinning the action to the
commit the tag pointed at when it was reviewed turns that into a visible,
reviewable change: upgrading becomes an edit to this file.

The rules, in order of how badly they would hurt to break:

  1. Every third-party `uses:` in `.github/workflows/` must reference a
     full 40-hex commit SHA. Local actions (`./path`) are exempt: they live
     in this repository and are reviewed with everything else.
  2. Every pinned `uses:` must carry a trailing `# <version>` comment. A
     bare SHA is unreviewable in practice — nobody can tell v4.4.0 from an
     arbitrary commit without looking it up, and comments are how a human
     learns that an upgrade is available.

Like the other gates in this directory, the check is proved against a
synthetic workflow that violates each rule, so the gate cannot silently
decay into a no-op.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"

# `uses: owner/repo/path@ref` or `uses: ./local/path`, optionally with a
# trailing `# comment`
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+(?:\s*#.*)?)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def violations(text: str) -> list[str]:
    """Rule violations in one workflow file, as human-readable lines."""
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _USES.match(line)
        if match is None:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue  # local action: reviewed with the code that runs it
        target, _, comment = reference.partition("#")
        target = target.strip()
        if "@" not in target:
            found.append(f"line {number}: no revision in {target!r}")
            continue
        name, _, revision = target.rpartition("@")
        if not _SHA.match(revision):
            found.append(f"line {number}: {name} is pinned to {revision!r}, not a commit SHA")
        elif not comment.strip():
            found.append(f"line {number}: {name} has no '# <version>' comment next to its SHA")
    return found


class PinnedActionsTest(unittest.TestCase):
    def test_every_workflow_action_is_pinned_to_a_commit(self) -> None:
        files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(files, f"no workflow files found under {WORKFLOWS}")
        for path in files:
            with self.subTest(workflow=path.name):
                found = violations(path.read_text())
                self.assertEqual(
                    found,
                    [],
                    f"{path.relative_to(REPO)} references actions by moving "
                    f"tag; pin them to a reviewed commit:\n  " + "\n  ".join(found),
                )


class CheckerTest(unittest.TestCase):
    """The checker still refuses what it claims to refuse."""

    def test_a_moving_tag_is_a_violation(self) -> None:
        text = "steps:\n  - uses: actions/checkout@v4\n"
        self.assertEqual(len(violations(text)), 1)

    def test_an_unversioned_sha_is_a_violation(self) -> None:
        sha = "11d5960a326750d5838078e36cf38b85af677262"
        text = f"steps:\n  - uses: actions/checkout@{sha}\n"
        self.assertEqual(len(violations(text)), 1)

    def test_a_pinned_action_with_a_version_comment_passes(self) -> None:
        sha = "11d5960a326750d5838078e36cf38b85af677262"
        text = f"steps:\n  - uses: actions/checkout@{sha} # v4.4.0\n"
        self.assertEqual(violations(text), [])

    def test_a_local_action_is_exempt(self) -> None:
        text = "steps:\n  - uses: ./.github/actions/gate\n"
        self.assertEqual(violations(text), [])


if __name__ == "__main__":
    unittest.main()
