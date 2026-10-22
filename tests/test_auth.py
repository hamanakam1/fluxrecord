"""JWT auth: issuance, verification, and the routes that serve them."""

import base64
import json
import time
import unittest

from fluxrecord.auth import (
    AuthError,
    AuthRoutes,
    RevocationList,
    bearer_token,
    introspect,
    issue,
    require_scope,
    verify,
)

SECRET = "a-signing-secret-that-is-long-enough"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class IssueTest(unittest.TestCase):
    def test_a_token_has_three_parts(self):
        token, claims = issue("terminal-1", SECRET, ttl_seconds=60)
        self.assertEqual(token.count("."), 2)
        self.assertEqual(claims.subject, "terminal-1")

    def test_the_header_declares_hs256(self):
        token, _ = issue("terminal-1", SECRET)
        header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        self.assertEqual(header["alg"], "HS256")
        self.assertEqual(header["typ"], "JWT")

    def test_a_token_carries_its_claims(self):
        token, _ = issue(
            "terminal-1",
            SECRET,
            ttl_seconds=60,
            issuer="fluxrecord",
            audience="api",
            scopes=["read", "write"],
        )
        claims = verify(token, SECRET)
        self.assertEqual(claims.subject, "terminal-1")
        self.assertEqual(claims.issuer, "fluxrecord")
        self.assertEqual(claims.audience, "api")
        self.assertEqual(claims.scopes, ("read", "write"))
        self.assertTrue(claims.token_id)

    def test_extra_claims_cannot_override_the_standard_ones(self):
        token, _ = issue(
            "terminal-1",
            SECRET,
            ttl_seconds=60,
            extra={"sub": "root", "tenant": "acme"},
        )
        claims = verify(token, SECRET)
        self.assertEqual(claims.subject, "terminal-1")
        self.assertEqual(claims.extra["tenant"], "acme")

    def test_an_empty_subject_is_refused(self):
        with self.assertRaises(ValueError):
            issue("", SECRET)

    def test_a_non_positive_ttl_is_refused(self):
        with self.assertRaises(ValueError):
            issue("terminal-1", SECRET, ttl_seconds=0)


class VerifyTest(unittest.TestCase):
    def test_a_good_token_verifies(self):
        token, claims = issue("terminal-1", SECRET, ttl_seconds=60)
        self.assertEqual(verify(token, SECRET).subject, "terminal-1")

    def test_a_token_signed_with_another_secret_is_refused(self):
        token, _ = issue("terminal-1", SECRET, ttl_seconds=60)
        with self.assertRaises(AuthError):
            verify(token, "a-different-secret")

    def test_a_tampered_payload_is_refused(self):
        token, _ = issue("terminal-1", SECRET, ttl_seconds=60)
        header, payload, signature = token.split(".")
        forged = b64(json.dumps({"sub": "root", "iat": 0, "exp": 9999999999}).encode())
        with self.assertRaises(AuthError):
            verify(f"{header}.{forged}.{signature}", SECRET)

    def test_alg_none_is_refused(self):
        header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = b64(json.dumps({"sub": "root", "iat": 0, "exp": 9999999999}).encode())
        with self.assertRaises(AuthError):
            verify(f"{header}.{payload}.", SECRET)

    def test_a_malformed_token_is_refused(self):
        for token in ("", "not-a-token", "one.two", "one.two.three.four"):
            with self.assertRaises(AuthError):
                verify(token, SECRET)

    def test_an_expired_token_is_refused(self):
        token, _ = issue("terminal-1", SECRET, ttl_seconds=60, now=int(time.time()) - 3600)
        with self.assertRaises(AuthError):
            verify(token, SECRET)

    def test_a_token_from_the_future_is_refused(self):
        token, _ = issue("terminal-1", SECRET, ttl_seconds=60, now=int(time.time()) + 3600)
        with self.assertRaises(AuthError):
            verify(token, SECRET)

    def test_the_issuer_must_match(self):
        token, _ = issue("terminal-1", SECRET, issuer="someone-else")
        with self.assertRaises(AuthError):
            verify(token, SECRET, issuer="fluxrecord")

    def test_the_audience_must_match(self):
        token, _ = issue("terminal-1", SECRET, audience="other-api")
        with self.assertRaises(AuthError):
            verify(token, SECRET, audience="api")

    def test_a_revoked_token_is_refused(self):
        token, claims = issue("terminal-1", SECRET, ttl_seconds=60)
        revoked = RevocationList()
        revoked.revoke(claims.token_id)
        with self.assertRaises(AuthError):
            verify(token, SECRET, revoked=revoked)


class IntrospectTest(unittest.TestCase):
    def test_an_active_token_is_described(self):
        token, _ = issue("terminal-1", SECRET, ttl_seconds=60, scopes=["read"])
        report = introspect(token, SECRET)
        self.assertTrue(report["active"])
        self.assertEqual(report["sub"], "terminal-1")
        self.assertEqual(report["scope"], "read")

    def test_an_unusable_token_is_inactive_rather_than_an_error(self):
        report = introspect("not-a-token", SECRET)
        self.assertEqual(report, {"active": False})

    def test_an_expired_token_is_inactive(self):
        token, _ = issue("terminal-1", SECRET, ttl_seconds=60, now=int(time.time()) - 3600)
        self.assertFalse(introspect(token, SECRET)["active"])


class MiddlewareTest(unittest.TestCase):
    def test_the_bearer_token_is_extracted(self):
        self.assertEqual(bearer_token({"Authorization": "Bearer abc"}), "abc")

    def test_a_missing_header_is_refused(self):
        with self.assertRaises(AuthError):
            bearer_token({})

    def test_a_non_bearer_header_is_refused(self):
        with self.assertRaises(AuthError):
            bearer_token({"authorization": "Basic abc"})

    def test_a_scope_is_required(self):
        token, _ = issue("terminal-1", SECRET, scopes=["read"])
        claims = verify(token, SECRET)
        require_scope(claims, "read")
        with self.assertRaises(AuthError):
            require_scope(claims, "write")


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.routes = AuthRoutes(
            SECRET,
            issuer="fluxrecord",
            audience="api",
            verify_credentials=lambda user, password: (
                ("read", "write") if user == "terminal-1" and password == "good" else None
            ),
        )

    def test_login_returns_a_token_pair(self):
        response = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["token_type"], "Bearer")
        self.assertIn("access_token", response.body)
        self.assertIn("refresh_token", response.body)

    def test_login_with_bad_credentials_is_refused(self):
        response = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "wrong"}
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(response.body["type"], "https://fluxrecord.dev/errors/invalid-credentials")

    def test_a_client_cannot_ask_for_a_scope(self):
        response = self.routes.handle(
            "POST",
            "/api/auth/login",
            {"username": "terminal-1", "password": "good", "scopes": ["admin", "root"]},
        )
        self.assertEqual(response.status, 200)
        from fluxrecord.auth import verify

        claims = verify(response.body["access_token"], SECRET)
        self.assertEqual(claims.scopes, ("read", "write"))
        self.assertNotIn("admin", claims.scopes)

    def test_login_without_a_body_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/login", {})
        self.assertEqual(response.status, 400)

    def test_a_refresh_token_buys_a_new_pair(self):
        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        refreshed = self.routes.handle(
            "POST", "/api/auth/refresh", {"refresh_token": login.body["refresh_token"]}
        )
        self.assertEqual(refreshed.status, 200)
        self.assertNotEqual(refreshed.body["refresh_token"], login.body["refresh_token"])

    def test_a_refreshed_token_keeps_its_scopes(self):
        from fluxrecord.auth import verify

        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        refreshed = self.routes.handle(
            "POST", "/api/auth/refresh", {"refresh_token": login.body["refresh_token"]}
        )
        claims = verify(refreshed.body["access_token"], SECRET)
        self.assertEqual(claims.scopes, ("read", "write"))

    def test_a_refresh_token_is_spent_once(self):
        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        token = login.body["refresh_token"]
        self.routes.handle("POST", "/api/auth/refresh", {"refresh_token": token})
        again = self.routes.handle("POST", "/api/auth/refresh", {"refresh_token": token})
        self.assertEqual(again.status, 401)

    def test_an_access_token_cannot_be_used_to_refresh(self):
        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        response = self.routes.handle(
            "POST", "/api/auth/refresh", {"refresh_token": login.body["access_token"]}
        )
        self.assertEqual(response.status, 400)

    def test_revoke_makes_the_token_unusable(self):
        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        revoked = self.routes.handle(
            "POST", "/api/auth/revoke", {"token": login.body["access_token"]}
        )
        self.assertEqual(revoked.status, 200)
        report = self.routes.handle(
            "POST", "/api/auth/introspect", {"token": login.body["access_token"]}
        )
        self.assertFalse(report.body["active"])

    def test_introspect_describes_an_active_token(self):
        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        report = self.routes.handle(
            "POST", "/api/auth/introspect", {"token": login.body["access_token"]}
        )
        self.assertEqual(report.status, 200)
        self.assertTrue(report.body["active"])
        self.assertEqual(report.body["sub"], "terminal-1")

    def test_logout_revokes_every_token_it_is_given(self):
        login = self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        )
        response = self.routes.handle(
            "POST",
            "/api/auth/logout",
            {"tokens": [login.body["access_token"], login.body["refresh_token"]]},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["revoked"], 2)

    def test_an_unknown_route_is_a_not_found(self):
        response = self.routes.handle("POST", "/api/auth/nope", {})
        self.assertEqual(response.status, 404)

    def test_a_get_is_not_allowed(self):
        response = self.routes.handle("GET", "/api/auth/login", {})
        self.assertEqual(response.status, 405)

    def test_a_route_without_a_verifier_says_so(self):
        routes = AuthRoutes(SECRET)
        response = routes.handle("POST", "/api/auth/login", {"username": "a", "password": "b"})
        self.assertEqual(response.status, 500)


if __name__ == "__main__":
    unittest.main()
