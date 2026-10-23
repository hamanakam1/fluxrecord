"""Refresh rotation has one winner even when requests share a token."""

import base64
import hashlib
import hmac
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

from fluxrecord.auth import AuthRoutes, RevocationList, issue, verify

SECRET = "a-synthetic-signing-secret-for-rotation"


def signed_refresh_payload(payload):
    """Sign synthetic claims, including identities issuance normally prevents."""

    def encode(value):
        data = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(payload)}"
    digest = hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"{signing_input}.{signature}"


class RefreshRotationTest(unittest.TestCase):
    def setUp(self):
        self.revoked = RevocationList()
        self.routes = self.make_routes()
        self.token, self.claims = issue(
            "worker-1", SECRET, now=1000, ttl_seconds=60, token_type="refresh", scopes=("read",)
        )

    def make_routes(self):
        return AuthRoutes(SECRET, revoked=self.revoked, now=1000)

    def refresh(self, routes=None, token=None):
        return (routes or self.routes).handle(
            "POST", "/api/auth/refresh", {"refresh_token": token or self.token}
        )

    def concurrent_refresh(self, routes):
        verified = Barrier(len(routes))

        def verify_together(*args, **kwargs):
            claims = verify(*args, **kwargs)
            # Force every request through verification before any can rotate.
            verified.wait(timeout=5)
            return claims

        with mock.patch("fluxrecord.auth.routes.verify", side_effect=verify_together):
            with ThreadPoolExecutor(max_workers=len(routes)) as pool:
                futures = [pool.submit(self.refresh, route) for route in routes]
                return [future.result(timeout=10) for future in futures]

    def assert_one_rotation(self, responses):
        self.assertEqual(sorted(response.status for response in responses), [200, 401])
        winner = next(response for response in responses if response.status == 200)
        access = verify(winner.body["access_token"], SECRET, now=1000, revoked=self.revoked)
        self.assertEqual(access.subject, "worker-1")
        self.assertEqual(access.scopes, ("read",))
        self.assertTrue(self.revoked.is_revoked(self.claims.token_id))
        self.assertEqual(self.refresh().status, 401)
        self.assertEqual(self.refresh(token=winner.body["refresh_token"]).status, 200)

    def test_concurrent_refresh_on_one_dispatcher_has_one_winner(self):
        responses = self.concurrent_refresh([self.routes, self.routes])
        self.assert_one_rotation(responses)

    def test_concurrent_dispatchers_sharing_a_denylist_have_one_winner(self):
        responses = self.concurrent_refresh([self.routes, self.make_routes()])
        self.assert_one_rotation(responses)

    def test_revocation_after_verification_still_prevents_rotation(self):
        def revoke_after_verification(*args, **kwargs):
            claims = verify(*args, **kwargs)
            self.revoked.revoke(claims.token_id)
            return claims

        with mock.patch("fluxrecord.auth.routes.verify", side_effect=revoke_after_verification):
            response = self.refresh()
        self.assertEqual(response.status, 401)
        self.assertEqual(response.body["detail"], "token revoked")

    def test_refresh_requires_a_nonempty_token_id(self):
        payload = {"sub": "worker-1", "iat": 1000, "exp": 1060, "typ": "refresh"}
        for claims in (payload, {**payload, "jti": ""}):
            with self.subTest(claims=claims):
                response = self.refresh(token=signed_refresh_payload(claims))
                self.assertEqual(response.status, 401)
                self.assertEqual(len(self.revoked), 0)

    def test_refresh_does_not_coerce_invalid_token_ids(self):
        payload = {"sub": "worker-1", "iat": 1000, "exp": 1060, "typ": "refresh"}
        for token_id in (None, False, 0, 42, [], {}):
            with self.subTest(token_id=token_id):
                response = self.refresh(token=signed_refresh_payload({**payload, "jti": token_id}))
                self.assertEqual(response.status, 401)
                self.assertEqual(len(self.revoked), 0)

    def test_an_access_token_is_not_consumed_by_a_refresh_attempt(self):
        token, claims = issue("worker-1", SECRET, now=1000)
        self.assertEqual(self.refresh(token=token).status, 400)
        self.assertFalse(self.revoked.is_revoked(claims.token_id))

    def test_a_bad_signature_cannot_consume_the_real_token_id(self):
        forged, _ = issue(
            "worker-1",
            "another-synthetic-secret",
            now=1000,
            token_type="refresh",
            token_id=self.claims.token_id,
        )
        self.assertEqual(self.refresh(token=forged).status, 401)
        self.assertEqual(self.refresh().status, 200)

    def test_consumption_survives_expiry_during_the_clock_leeway(self):
        self.assertEqual(self.refresh().status, 200)
        self.routes.now = self.claims.expires_at + 29
        response = self.refresh()
        self.assertEqual(response.status, 401)
        self.assertEqual(response.body["detail"], "token revoked")

    def test_an_unspent_token_can_rotate_once_during_the_clock_leeway(self):
        self.routes.now = self.claims.expires_at + 29
        self.assertEqual(self.refresh().status, 200)
        self.assertEqual(self.refresh().status, 401)

    def test_failed_pair_issuance_keeps_the_old_token_spent(self):
        with mock.patch.object(self.routes, "_pair", side_effect=RuntimeError("issuance failed")):
            with self.assertRaisesRegex(RuntimeError, "issuance failed"):
                self.refresh()
        self.assertEqual(self.refresh().status, 401)


if __name__ == "__main__":
    unittest.main()
