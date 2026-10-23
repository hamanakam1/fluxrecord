"""Task creation retains its API contract when writers arrive together."""

import concurrent.futures
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fluxrecord.core import Ledger


class TaskCreationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path, chain_key="synthetic-test-key")

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def race(self, requests: list[tuple[str, str]]) -> list[dict[str, Any] | Exception]:
        gate = threading.Barrier(len(requests), timeout=5)

        class SynchronizedLedger(Ledger):
            @contextmanager
            def transaction(self) -> Iterator[None]:
                # Both writers reach the transaction boundary before either
                # acquires the lock. Never wait while holding the write lock.
                gate.wait()
                with super().transaction():
                    yield

        def create(request: tuple[str, str]) -> dict[str, Any] | Exception:
            with SynchronizedLedger(self.path, chain_key="synthetic-test-key") as ledger:
                try:
                    return ledger.add_task(
                        request[0],
                        "Synthetic objective",
                        "Synthetic acceptance",
                        seed_key=request[1],
                    )
                except Exception as exc:
                    return exc

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return list(pool.map(create, requests))

    def assert_one_creation(self) -> None:
        self.assertEqual(len(self.ledger.status()["tasks"]), 1)
        created = [event for event in self.ledger.events() if event["type"] == "task.created"]
        self.assertEqual(len(created), 1)
        self.assertTrue(self.ledger.verify_chain()["ok"])

    def test_concurrent_same_id_and_seed_returns_the_same_ticket(self) -> None:
        results = self.race([("M-397001", "same-seed"), ("M-397001", "same-seed")])
        for result in results:
            self.assertIsInstance(result, dict)
        self.assertEqual(results[0], results[1])
        self.assert_one_creation()

    def test_concurrent_same_id_without_seed_raises_value_error(self) -> None:
        results = self.race([("M-397001", ""), ("M-397001", "")])
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        errors = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(str(errors[0]), "task already exists: M-397001")
        self.assert_one_creation()

    def test_concurrent_same_id_with_different_seeds_raises_value_error(self) -> None:
        results = self.race([("M-397001", "seed-one"), ("M-397001", "seed-two")])
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        errors = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(str(errors[0]), "task already exists: M-397001")
        self.assert_one_creation()

    def test_concurrent_same_seed_with_different_ids_raises_value_error(self) -> None:
        results = self.race([("M-397001", "same-seed"), ("M-397002", "same-seed")])
        created = [result for result in results if isinstance(result, dict)]
        errors = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(str(errors[0]), f"seed key already used by {created[0]['id']}")
        self.assert_one_creation()

    def test_next_id_ignores_identifiers_outside_the_numeric_m_namespace(self) -> None:
        for identifier in ("AB999999", "X-777777", "m-888888", "999999", "M-ticket", "M-42-extra"):
            with self.subTest(identifier=identifier):
                self.ledger.add_task(identifier, "Synthetic objective", "Synthetic acceptance")
                self.assertEqual(self.ledger.next_ticket_id(), "M-000001")

    def test_next_id_preserves_short_and_zero_padded_numeric_ids(self) -> None:
        self.assertEqual(self.ledger.next_ticket_id(), "M-000001")
        for identifier in ("M-7", "M-000003", "M-000042"):
            self.ledger.add_task(identifier, "Synthetic objective", "Synthetic acceptance")
        self.assertEqual(self.ledger.next_ticket_id(), "M-000043")


if __name__ == "__main__":
    unittest.main()
