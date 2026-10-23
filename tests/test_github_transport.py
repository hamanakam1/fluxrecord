"""The gh transport enforces byte and time limits while collecting output."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord import github_sync


class BoundedTransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name) / "synthetic_gh.py"
        self.children = []
        self.popen = subprocess.Popen

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        self.temp.cleanup()

    def call_child(self, source, limit=1024, timeout=2, stderr_limit=65536):
        self.script.write_text(source, encoding="utf-8")

        def launch(args, **kwargs):
            self.assertEqual(args, ["gh", "api", "repos/owner/repo/issues"])
            child = self.popen([sys.executable, "-u", str(self.script)], **kwargs)
            self.children.append(child)
            return child

        with (
            mock.patch.object(github_sync.subprocess, "Popen", side_effect=launch),
            mock.patch.object(github_sync, "MAX_RESPONSE_BYTES", limit),
            mock.patch.object(github_sync, "_MAX_STDERR_BYTES", stderr_limit),
            mock.patch.object(github_sync, "TIMEOUT_SECONDS", timeout),
        ):
            return github_sync._gh_json(["api", "repos/owner/repo/issues"])

    def assert_child_reaped_and_pipes_closed(self):
        self.assertEqual(len(self.children), 1)
        child = self.children[0]
        self.assertIsNotNone(child.returncode)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)

    def test_the_response_limit_counts_utf8_bytes_not_characters(self):
        # This JSON document is ten characters but eighteen UTF-8 bytes.
        source = 'import sys\nsys.stdout.buffer.write((\'"\' + "é" * 8 + \'"\').encode("utf-8"))\n'
        with self.assertRaisesRegex(OSError, "exceeded 12 bytes"):
            self.call_child(source, limit=12)
        self.assert_child_reaped_and_pipes_closed()

    def test_streaming_oversize_is_refused_before_the_child_finishes(self):
        source = "import os, time\nos.write(1, b'x' * 65)\ntime.sleep(5)\n"
        with self.assertRaisesRegex(OSError, "exceeded 64 bytes"):
            self.call_child(source, limit=64, timeout=0.5)
        self.assert_child_reaped_and_pipes_closed()

    def test_utf8_json_at_the_exact_byte_limit_is_accepted(self):
        source = 'import sys\nsys.stdout.buffer.write((\'"\' + "é" * 8 + \'"\').encode("utf-8"))\n'
        self.assertEqual(self.call_child(source, limit=18), "é" * 8)
        self.assert_child_reaped_and_pipes_closed()

    def test_stderr_is_drained_while_waiting_for_stdout(self):
        source = "import os\nos.write(2, b'x' * 100000)\nos.write(1, b'[]')\n"
        self.assertEqual(self.call_child(source, stderr_limit=100000), [])
        self.assert_child_reaped_and_pipes_closed()

    def test_streaming_stderr_also_has_a_byte_ceiling(self):
        source = "import os, time\nos.write(2, b'x' * 65)\ntime.sleep(5)\n"
        with self.assertRaisesRegex(OSError, "stderr exceeded 64 bytes"):
            self.call_child(source, stderr_limit=64, timeout=0.5)
        self.assert_child_reaped_and_pipes_closed()

    def test_invalid_utf8_is_a_transport_error(self):
        with self.assertRaisesRegex(OSError, "UTF-8 JSON"):
            self.call_child("import os\nos.write(1, b'\\xff')\n")
        self.assert_child_reaped_and_pipes_closed()

    def test_a_timeout_kills_and_reaps_the_child(self):
        with self.assertRaisesRegex(OSError, "timed out"):
            self.call_child("import time\ntime.sleep(5)\n", timeout=0.2)
        self.assert_child_reaped_and_pipes_closed()
        self.assertLess(self.children[0].returncode, 0)

    def test_the_deadline_covers_exit_after_both_pipes_close(self):
        source = "import os, time\nos.close(1)\nos.close(2)\ntime.sleep(5)\n"
        with self.assertRaisesRegex(OSError, "timed out"):
            self.call_child(source, timeout=0.2)
        self.assert_child_reaped_and_pipes_closed()
        self.assertLess(self.children[0].returncode, 0)

    def test_cancellation_kills_and_reaps_the_child_and_closes_the_selector(self):
        selector = github_sync.selectors.DefaultSelector()
        with (
            mock.patch.object(github_sync.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(selector, "select", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.call_child("import time\ntime.sleep(5)\n")
        self.assert_child_reaped_and_pipes_closed()
        self.assertIsNone(selector.get_map())

    def test_an_unsupported_platform_is_refused_before_spawning(self):
        with (
            mock.patch.object(github_sync.os, "name", "nt"),
            mock.patch.object(github_sync.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(OSError, "POSIX"):
                github_sync._gh_json(["api", "repos/owner/repo/issues"])
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
