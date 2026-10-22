"""
Authentication routes, without a web framework.

The dispatcher takes a method, a path, a body and headers, and returns a
response. Anything that wants to serve these routes can adapt its own
framework to that shape; nothing here depends on one.

Errors follow RFC 7807.

The credential verifier returns the scopes to grant, or None to refuse the
credentials. Scopes are never taken from the request: a client that could
name its own scope would be promoting itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .jwt import AuthError, RevocationList, introspect, issue, verify

__all__ = ["AuthRoutes", "Response", "PROBLEM_BASE"]

PROBLEM_BASE = "https://fluxrecord.dev/errors/"


@dataclass
class Response:
    """A response in the shape every adapter can translate."""

    status: int
    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


def _problem(status: int, kind: str, detail: str) -> Response:
    return Response(
        status=status,
        body={
            "type": f"{PROBLEM_BASE}{kind}",
            "title": kind.replace("-", " ").title(),
            "status": status,
            "detail": detail,
        },
        headers={"Content-Type": "application/problem+json"},
    )


class AuthRoutes:
    """Login, refresh, revoke, introspect and logout."""

    def __init__(
        self,
        secret: str | bytes,
        issuer: str = "",
        audience: str = "",
        access_ttl: int = 900,
        refresh_ttl: int = 86400,
        verify_credentials: Callable[[str, str], tuple[str, ...] | None] | None = None,
        revoked: RevocationList | None = None,
        now: int | None = None,
    ) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self.secret = secret
        self.issuer = issuer
        self.audience = audience
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self.verify_credentials = verify_credentials
        self.revoked = revoked if revoked is not None else RevocationList()
        self.now = now

    # ---------------------------------------------------------------- helpers

    def _moment(self) -> int:
        return int(time.time()) if self.now is None else int(self.now)

    def _pair(self, subject: str, scopes: tuple[str, ...]) -> dict[str, Any]:
        access, access_claims = issue(
            subject,
            self.secret,
            ttl_seconds=self.access_ttl,
            issuer=self.issuer,
            audience=self.audience,
            scopes=scopes,
            token_type="access",
            now=self._moment(),
        )
        # The refresh token carries the scopes the server granted, so the
        # access token minted from it keeps them.
        refresh, refresh_claims = issue(
            subject,
            self.secret,
            ttl_seconds=self.refresh_ttl,
            issuer=self.issuer,
            audience=self.audience,
            scopes=scopes,
            token_type="refresh",
            now=self._moment(),
        )
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": self.access_ttl,
            "access_jti": access_claims.token_id,
            "refresh_jti": refresh_claims.token_id,
        }

    # ---------------------------------------------------------------- routes

    def login(self, body: Mapping[str, Any]) -> Response:
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        if not username or not password:
            return _problem(400, "invalid-request", "username and password are required")
        if self.verify_credentials is None:
            return _problem(500, "not-configured", "no credential verifier is configured")
        granted = self.verify_credentials(username, password)
        if granted is None:
            return _problem(401, "invalid-credentials", "username or password is wrong")
        # The scopes come from the verifier, never from the request: a client
        # that could ask for a scope would be asking for its own promotion.
        return Response(200, self._pair(username, tuple(str(scope) for scope in granted)))

    def refresh(self, body: Mapping[str, Any]) -> Response:
        token = str(body.get("refresh_token", ""))
        if not token:
            return _problem(400, "invalid-request", "refresh_token is required")
        try:
            claims = verify(
                token,
                self.secret,
                now=self._moment(),
                revoked=self.revoked,
                issuer=self.issuer,
                audience=self.audience,
            )
        except AuthError as exc:
            return _problem(exc.status, "invalid-token", exc.reason)
        if claims.token_type != "refresh":
            return _problem(400, "invalid-request", "token is not a refresh token")
        # Rotation: the old refresh token is spent the moment a new one is issued.
        self.revoked.revoke(claims.token_id)
        return Response(200, self._pair(claims.subject, claims.scopes))

    def revoke(self, body: Mapping[str, Any]) -> Response:
        token = str(body.get("token", ""))
        if not token:
            return _problem(400, "invalid-request", "token is required")
        try:
            claims = verify(
                token,
                self.secret,
                now=self._moment(),
                issuer=self.issuer,
                audience=self.audience,
            )
        except AuthError as exc:
            return _problem(exc.status, "invalid-token", exc.reason)
        self.revoked.revoke(claims.token_id)
        return Response(200, {"revoked": True, "jti": claims.token_id})

    def introspect(self, body: Mapping[str, Any]) -> Response:
        token = str(body.get("token", ""))
        if not token:
            return _problem(400, "invalid-request", "token is required")
        report = introspect(token, self.secret, now=self._moment(), revoked=self.revoked)
        return Response(200, report)

    def logout(self, body: Mapping[str, Any]) -> Response:
        tokens = body.get("tokens") or []
        if not isinstance(tokens, (list, tuple)):
            return _problem(400, "invalid-request", "tokens must be a list")
        revoked = 0
        for token in tokens:
            try:
                claims = verify(
                    str(token),
                    self.secret,
                    now=self._moment(),
                    issuer=self.issuer,
                    audience=self.audience,
                )
            except AuthError:
                continue
            self.revoked.revoke(claims.token_id)
            revoked += 1
        return Response(200, {"revoked": revoked})

    # ------------------------------------------------------------- dispatch

    def handle(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, Any] | None = None,
    ) -> Response:
        """Dispatch one request."""
        payload = dict(body or {})
        route = path.rstrip("/") or "/"
        if method.upper() != "POST":
            return _problem(405, "method-not-allowed", f"{method} is not allowed here")
        handlers = {
            "/api/auth/login": self.login,
            "/api/auth/refresh": self.refresh,
            "/api/auth/revoke": self.revoke,
            "/api/auth/introspect": self.introspect,
            "/api/auth/logout": self.logout,
        }
        handler = handlers.get(route)
        if handler is None:
            return _problem(404, "not-found", f"no auth route at {route}")
        return handler(payload)
