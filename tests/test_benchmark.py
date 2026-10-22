"""The benchmark: the promise, measured."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.benchmark import measure
from fluxrecord.core import Ledger


class BenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        (self.repo / "fluxrecord").mkdir(parents=True)
        (self.repo / "fluxrecord" / "core.py").write_text("x" * 5000)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task(
            "M-1",
            "Split core.py",
            "All routes work",
            scope=["fluxrecord/core.py"],
        )

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_the_packet_is_measured_against_the_files(self):
        result = measure(self.ledger, "M-1", self.repo)
        self.assertEqual(result["reread_bytes"], 5000)
        self.assertGreater(result["packet_bytes"], 0)
        self.assertEqual(result["scoped_files_found"], 1)
        self.assertEqual(result["scoped_files_total"], 1)

    def test_the_saving_is_the_difference(self):
        result = measure(self.ledger, "M-1", self.repo)
        self.assertEqual(result["saving_bytes"], result["reread_bytes"] - result["packet_bytes"])
        self.assertGreater(result["saving_bytes"], 0)

    def test_the_measurement_is_reproducible(self):
        first = measure(self.ledger, "M-1", self.repo)
        second = measure(self.ledger, "M-1", self.repo)
        self.assertEqual(first["packet_bytes"], second["packet_bytes"])
        self.assertEqual(first["reread_bytes"], second["reread_bytes"])
        self.assertEqual(first["ratio"], second["ratio"])

    def test_a_file_that_does_not_exist_is_not_counted(self):
        self.ledger.add_task("M-2", "Elsewhere", "Done", scope=["nowhere/missing.py"])
        result = measure(self.ledger, "M-2", self.repo)
        self.assertEqual(result["reread_bytes"], 0)
        self.assertIsNone(result["ratio"])
        self.assertEqual(result["scoped_files_found"], 0)
        self.assertEqual(result["scoped_files_total"], 1)

    def test_a_scope_that_is_not_a_path_is_not_counted(self):
        self.ledger.add_task("M-3", "Abstract", "Done", scope=["repo/api"])
        result = measure(self.ledger, "M-3", self.repo)
        self.assertEqual(result["reread_bytes"], 0)

    def test_the_packet_hash_is_reported(self):
        result = measure(self.ledger, "M-1", self.repo)
        self.assertTrue(result["packet_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
