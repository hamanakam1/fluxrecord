"""The tokenizer plug-in and the budget policy."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord import tokenizers
from fluxrecord.core import BUDGET_POLICY, DEFAULT_BUDGET, Ledger


class TokenizerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"

    def tearDown(self):
        tokenizers.clear()
        self.temp.cleanup()

    def test_no_tokenizer_means_no_count(self):
        self.assertIsNone(tokenizers.count("anything"))
        self.assertFalse(tokenizers.registered())

    def test_a_registered_tokenizer_counts(self):
        tokenizers.register(lambda text: len(text.split()))
        self.assertTrue(tokenizers.registered())
        self.assertEqual(tokenizers.count("one two three"), 3)

    def test_the_registry_can_be_cleared(self):
        tokenizers.register(lambda text: 1)
        tokenizers.clear()
        self.assertFalse(tokenizers.registered())
        self.assertIsNone(tokenizers.count("anything"))

    def test_something_that_is_not_callable_is_refused(self):
        with self.assertRaises(ValueError):
            tokenizers.register("not a tokenizer")  # type: ignore[arg-type]

    def test_a_packet_has_no_token_count_without_a_tokenizer(self):
        with Ledger(self.path) as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
            self.assertNotIn("token_count", ledger.pack("M-1"))

    def test_a_packet_reports_a_token_count_with_one(self):
        with Ledger(self.path, tokenizer=lambda text: len(text.split())) as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
            packed = ledger.pack("M-1")
            self.assertIn("token_count", packed)
            self.assertGreater(packed["token_count"], 0)

    def test_the_token_count_is_of_the_packet_itself(self):
        with Ledger(self.path, tokenizer=lambda text: len(text)) as ledger:
            ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
            packed = ledger.pack("M-1")
            self.assertEqual(packed["token_count"], packed["packet_bytes"])


class BudgetPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_the_policy_has_a_default_for_every_kind(self):
        self.assertIn("code", BUDGET_POLICY)
        self.assertEqual(BUDGET_POLICY["code"], 12000)
        self.assertGreater(DEFAULT_BUDGET, 0)

    def test_a_ticket_gets_the_budget_of_its_kind(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], kind="fix")
        self.assertEqual(self.ledger.budget_for("fix"), BUDGET_POLICY["fix"])

    def test_an_unknown_kind_gets_the_default(self):
        self.assertEqual(self.ledger.budget_for("something-else"), DEFAULT_BUDGET)

    def test_a_pack_uses_the_policy_when_no_budget_is_given(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], kind="fix")
        packed = self.ledger.pack("M-1")
        self.assertLessEqual(packed["packet_bytes"], BUDGET_POLICY["fix"])

    def test_a_budget_can_still_be_given(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        with self.assertRaises(ValueError):
            self.ledger.pack("M-1", max_bytes=10)


if __name__ == "__main__":
    unittest.main()
