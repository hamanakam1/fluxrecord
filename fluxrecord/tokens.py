"""
Token commands.

The authentication module is a library first. These commands make it
reachable from a shell, so a dashboard can be asked for a token and a
worker can check one, without writing a script.

The signing secret never appears on the command line: a secret in an
argument is a secret in the shell history and in the process table. It
comes from FLUX_TOKEN_SECRET, or from a file the operator names.
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["secret_from_environment", "issue_token", "verify_token", "introspect_token"]


def secret_from_environment() -> str:
    """The signing secret, from the environment or from a named file.

    Raises:
        ValueError: no secret was configured
    """
    from .secrets import token_secret

    return token_secret()


def issue_token(
    subject: str,
    secret: str,
    ttl_seconds: int = 900,
    issuer: str = "",
    audience: str = "",
    scopes: tuple[str, ...] = (),
    token_type: str = "access",
) -> dict[str, Any]:
    """Issue a token and describe it."""
    from .auth import issue

    token, claims = issue(
        subject,
        secret,
        ttl_seconds=ttl_seconds,
        issuer=issuer,
        audience=audience,
        scopes=scopes,
        token_type=token_type,
    )
    return {
        "token": token,
        "subject": claims.subject,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
        "expires_in": max(0, claims.expires_at - int(time.time())),
        "scopes": list(claims.scopes),
        "token_type": claims.token_type,
        "issuer": claims.issuer,
        "audience": claims.audience,
        "token_id": claims.token_id,
    }


def verify_token(
    token: str,
    secret: str,
    issuer: str = "",
    audience: str = "",
) -> dict[str, Any]:
    """Verify a token and describe what it asserts."""
    from .auth import AuthError, verify

    try:
        claims = verify(token, secret, issuer=issuer, audience=audience)
    except AuthError as exc:
        return {"valid": False, "reason": exc.reason}
    return {
        "valid": True,
        "subject": claims.subject,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
        "scopes": list(claims.scopes),
        "token_type": claims.token_type,
        "issuer": claims.issuer,
        "audience": claims.audience,
        "token_id": claims.token_id,
    }


def introspect_token(
    token: str,
    secret: str,
    issuer: str = "",
    audience: str = "",
) -> dict[str, Any]:
    """Describe a token the way RFC 7662 asks for."""
    from .auth import introspect

    return introspect(token, secret)
