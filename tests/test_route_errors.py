"""The remaining error paths: the auth routes and the cycle config."""

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord.auth import AuthRoutes
from fluxrecord.github_cycle import cycle

SECRET = "a-signing-secret-that-is-long-enough"


class RouteErrorTest(unittest.TestCase):
    def setUp(self):
        self.routes = AuthRoutes(
            SECRET,
            issuer="fluxrecord",
            audience="api",
            verify_credentials=lambda user, password: ("read",) if password == "good" else None,
        )

    def login(self):
        return self.routes.handle(
            "POST", "/api/auth/login", {"username": "terminal-1", "password": "good"}
        ).body

    def test_a_refresh_without_a_token_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/refresh", {})
        self.assertEqual(response.status, 400)
        self.assertEqual(response.body["type"], "https://fluxrecord.dev/errors/invalid-request")

    def test_a_refresh_with_a_broken_token_is_refused(self):
        response = self.routes.handle("POST", "/api/auth/refresh", {"refresh_token": "not-a-token"})
        self.assertEqual(response.status, 401)

    def test_a_revoke_without_a_token_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/revoke", {})
        self.assertEqual(response.status, 400)

    def test_a_revoke_with_a_broken_token_is_refused(self):
        response = self.routes.handle("POST", "/api/auth/revoke", {"token": "not-a-token"})
        self.assertEqual(response.status, 401)

    def test_an_introspect_without_a_token_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/introspect", {})
        self.assertEqual(response.status, 400)

    def test_a_logout_with_tokens_that_are_not_a_list_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/logout", {"tokens": "one"})
        self.assertEqual(response.status, 400)

    def test_a_logout_ignores_tokens_it_cannot_read(self):
        response = self.routes.handle(
            "POST", "/api/auth/logout", {"tokens": ["not-a-token", "also-not"]}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["revoked"], 0)

    def test_a_login_without_a_password_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/login", {"username": "terminal-1"})
        self.assertEqual(response.status, 400)

    def test_a_login_without_a_username_is_a_bad_request(self):
        response = self.routes.handle("POST", "/api/auth/login", {"password": "good"})
        self.assertEqual(response.status, 400)

    def test_the_problem_body_has_the_shape_rfc_7807_asks_for(self):
        response = self.routes.handle("POST", "/api/auth/login", {})
        for field in ("type", "title", "status", "detail"):
            self.assertIn(field, response.body)
        self.assertEqual(response.headers["Content-Type"], "application/problem+json")

    def test_a_revoked_access_token_cannot_be_revoked_again_silently(self):
        body = self.login()
        first = self.routes.handle("POST", "/api/auth/revoke", {"token": body["access_token"]})
        self.assertEqual(first.status, 200)
        second = self.routes.handle("POST", "/api/auth/revoke", {"token": body["access_token"]})
        self.assertEqual(second.status, 200)
        self.assertEqual(second.body["jti"], first.body["jti"])


class CycleConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "cycle.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, payload):
        self.config.write_text(json.dumps(payload))
        return str(self.config)

    def test_a_config_that_is_not_json_is_refused(self):
        self.config.write_text("{not json")
        with self.assertRaises(ValueError):
            cycle(str(self.config))

    def test_a_config_that_is_not_an_object_is_refused(self):
        path = self.write([1, 2, 3])
        with self.assertRaises(ValueError):
            cycle(path)

    def test_a_config_without_repositories_is_refused(self):
        path = self.write({"repos": []})
        with self.assertRaises(ValueError):
            cycle(path)

    def test_kinds_must_be_a_list(self):
        path = self.write({"repos": ["owner/repo"], "kinds": "issues"})
        with self.assertRaises(ValueError):
            cycle(path)

    def test_a_repository_entry_needs_a_name(self):
        path = self.write({"repos": [{"max_pages": 2}]})
        with self.assertRaises(ValueError):
            cycle(path)

    def test_a_dry_run_of_a_valid_config_does_nothing(self):
        path = self.write(
            {"repos": ["owner/repo"], "kinds": ["issues"], "sync_db": str(self.root / "g.sqlite")}
        )
        result = cycle(path, runner=lambda args: [])
        self.assertEqual(result["observed"], 0)


if __name__ == "__main__":
    unittest.main()
