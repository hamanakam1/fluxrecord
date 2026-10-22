"""Property-based tests for the token rules."""

import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fluxrecord.auth import AuthError, decode, introspect, issue, verify

SLOW = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

SECRET = "a-signing-secret-that-is-long-enough"
OTHER = "a-different-secret-that-is-also-long"

SUBJECTS = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=40
)
SECRETS = st.text(min_size=1, max_size=80)
TTLS = st.integers(min_value=1, max_value=86400)


class TokenPropertyTest(unittest.TestCase):
    @SLOW
    @given(subject=SUBJECTS, secret=SECRETS, ttl=TTLS)
    def test_a_token_round_trips_its_subject(self, subject, secret, ttl):
        token, claims = issue(subject, secret, ttl_seconds=ttl)
        self.assertEqual(verify(token, secret).subject, subject)
        self.assertEqual(claims.expires_at - claims.issued_at, ttl)

    @SLOW
    @given(subject=SUBJECTS, secret=SECRETS)
    def test_another_secret_never_verifies(self, subject, secret):
        token, _ = issue(subject, secret, ttl_seconds=60)
        with self.assertRaises(AuthError):
            verify(token, OTHER)

    @SLOW
    @given(subject=SUBJECTS, secret=SECRETS, position=st.integers(min_value=0, max_value=50))
    def test_changing_any_character_invalidates_the_token(self, subject, secret, position):
        token, _ = issue(subject, secret, ttl_seconds=60)
        index = position % len(token)
        altered = token[:index] + ("A" if token[index] != "A" else "B") + token[index + 1 :]
        with self.assertRaises(AuthError):
            verify(altered, secret)

    @SLOW
    @given(subject=SUBJECTS, secret=SECRETS, ttl=TTLS)
    def test_introspection_agrees_with_verification(self, subject, secret, ttl):
        token, _ = issue(subject, secret, ttl_seconds=ttl)
        self.assertTrue(introspect(token, secret)["active"])
        self.assertFalse(introspect(token, OTHER)["active"])

    @SLOW
    @given(subject=SUBJECTS, secret=SECRETS, ttl=TTLS)
    def test_the_payload_carries_only_the_claims_it_should(self, subject, secret, ttl):
        token, _ = issue(subject, secret, ttl_seconds=ttl)
        claims = decode(token, secret)
        self.assertEqual(claims.subject, subject)
        self.assertGreater(claims.expires_at, claims.issued_at)
        self.assertTrue(claims.token_id)


if __name__ == "__main__":
    unittest.main()
