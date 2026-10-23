"""Token commands: the shell's way into the authentication module."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord.tokens import (
    introspect_token,
    issue_token,
    secret_from_environment,
    verify_token,
)

SECRET = "a-signing-secret-that-is-long-enough"


class SecretTest(unittest.TestCase):
    def test_the_secret_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"FLUX_TOKEN_SECRET": SECRET}, clear=True):
            self.assertEqual(secret_from_environment(), SECRET)

    def test_the_secret_can_come_from_a_file(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "secret"
            path.write_text(SECRET + "\n")
            with mock.patch.dict(os.environ, {"FLUX_TOKEN_SECRET_FILE": str(path)}, clear=True):
                self.assertEqual(secret_from_environment(), SECRET)

    def test_no_secret_is_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as caught:
                secret_from_environment()
        self.assertIn("FLUX_TOKEN_SECRET", str(caught.exception))

    def test_a_missing_secret_file_is_an_error(self):
        with mock.patch.dict(os.environ, {"FLUX_TOKEN_SECRET_FILE": "/nope/secret"}, clear=True):
            with self.assertRaises(ValueError):
                secret_from_environment()


class TokenCommandTest(unittest.TestCase):
    def test_an_issued_token_verifies(self):
        issued = issue_token("terminal-1", SECRET, ttl_seconds=300, scopes=("read",))
        verified = verify_token(issued["token"], SECRET)
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["subject"], "terminal-1")
        self.assertEqual(verified["scopes"], ["read"])

    def test_another_secret_does_not_verify(self):
        issued = issue_token("terminal-1", SECRET)
        verified = verify_token(issued["token"], "another-secret")
        self.assertFalse(verified["valid"])
        self.assertIn("signature", verified["reason"])

    def test_introspection_reports_an_active_token(self):
        issued = issue_token("terminal-1", SECRET)
        report = introspect_token(issued["token"], SECRET)
        self.assertTrue(report["active"])
        self.assertEqual(report["sub"], "terminal-1")

    def test_introspection_reports_an_unusable_token_as_inactive(self):
        report = introspect_token("not-a-token", SECRET)
        self.assertEqual(report, {"active": False})

    def test_introspection_checks_the_expected_issuer_and_audience(self):
        issued = issue_token("terminal-1", SECRET, issuer="issuer-a", audience="api-a")
        self.assertTrue(
            introspect_token(issued["token"], SECRET, issuer="issuer-a", audience="api-a")["active"]
        )
        for expected in ({"issuer": "issuer-b"}, {"audience": "api-b"}):
            with self.subTest(expected=expected):
                self.assertEqual(
                    introspect_token(issued["token"], SECRET, **expected), {"active": False}
                )

    def test_the_issuer_is_checked(self):
        issued = issue_token("terminal-1", SECRET, issuer="fluxrecord")
        self.assertFalse(verify_token(issued["token"], SECRET, issuer="other")["valid"])
        self.assertTrue(verify_token(issued["token"], SECRET, issuer="fluxrecord")["valid"])

    def test_a_refresh_token_can_be_issued(self):
        issued = issue_token("terminal-1", SECRET, token_type="refresh")
        self.assertEqual(issued["token_type"], "refresh")

    def test_the_expiry_is_described(self):
        issued = issue_token("terminal-1", SECRET, ttl_seconds=120)
        self.assertLessEqual(issued["expires_in"], 120)
        self.assertEqual(issued["expires_at"] - issued["issued_at"], 120)


if __name__ == "__main__":
    unittest.main()
