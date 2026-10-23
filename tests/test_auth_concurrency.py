"""Refresh rotation remains single-use across callers sharing one denylist."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from fluxrecord.auth import AuthRoutes, RevocationList, issue, verify

SECRET = "synthetic-refresh-rotation-secret"


class RefreshRotationTest(unittest.TestCase):
    def test_concurrent_refreshes_mint_exactly_one_pair(self):
        for shared_routes in (False, True):
            with self.subTest(shared_routes=shared_routes):
                revoked = RevocationList()
                first = AuthRoutes(SECRET, revoked=revoked, now=1000)
                second = first if shared_routes else AuthRoutes(SECRET, revoked=revoked, now=1000)
                token, old_claims = issue(
                    "worker", SECRET, token_type="refresh", scopes=("read",), now=1000
                )
                barrier = Barrier(2)

                def verified_together(*args, **kwargs):
                    claims = verify(*args, **kwargs)
                    barrier.wait(timeout=5)
                    return claims

                # Both callers have passed the denylist check before either
                # attempts to consume the token. The atomic step must decide.
                with patch("fluxrecord.auth.routes.verify", side_effect=verified_together):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [
                            pool.submit(route.refresh, {"refresh_token": token})
                            for route in (first, second)
                        ]
                        responses = [future.result(timeout=10) for future in futures]

                self.assertEqual(sorted(response.status for response in responses), [200, 401])
                successful = next(response for response in responses if response.status == 200)
                refused = next(response for response in responses if response.status == 401)
                self.assertNotIn("access_token", refused.body)
                self.assertNotIn("refresh_token", refused.body)
                self.assertTrue(revoked.is_revoked(old_claims.token_id))
                for name in ("access_token", "refresh_token"):
                    claims = verify(successful.body[name], SECRET, revoked=revoked, now=1000)
                    self.assertEqual(claims.subject, "worker")
                    self.assertEqual(claims.scopes, ("read",))
                self.assertEqual(first.refresh({"refresh_token": token}).status, 401)

    def test_revocation_after_verification_prevents_rotation(self):
        routes = AuthRoutes(SECRET, now=1000)
        token, _ = issue("worker", SECRET, token_type="refresh", now=1000)

        def verified_then_revoked(*args, **kwargs):
            claims = verify(*args, **kwargs)
            routes.revoked.revoke(claims.token_id)
            return claims

        with (
            patch("fluxrecord.auth.routes.verify", side_effect=verified_then_revoked),
            patch.object(routes, "_pair") as mint,
        ):
            response = routes.refresh({"refresh_token": token})
        self.assertEqual(response.status, 401)
        mint.assert_not_called()

    def test_failed_mint_does_not_restore_the_consumed_token(self):
        routes = AuthRoutes(SECRET, now=1000)
        token, claims = issue("worker", SECRET, token_type="refresh", now=1000)
        with patch.object(routes, "_pair", side_effect=RuntimeError("synthetic mint failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic mint failure"):
                routes.refresh({"refresh_token": token})
        self.assertTrue(routes.revoked.is_revoked(claims.token_id))
        self.assertEqual(routes.refresh({"refresh_token": token}).status, 401)


class RevocationListTest(unittest.TestCase):
    def test_consumption_is_single_use_and_respects_revocation(self):
        revoked = RevocationList()
        self.assertFalse(revoked.consume(""))
        self.assertEqual(len(revoked), 0)
        self.assertTrue(revoked.consume("first"))
        self.assertTrue(revoked.is_revoked("first"))
        self.assertFalse(revoked.consume("first"))
        revoked.revoke("second")
        self.assertFalse(revoked.consume("second"))
        revoked.revoke("")
        self.assertEqual(len(revoked), 2)


if __name__ == "__main__":
    unittest.main()
