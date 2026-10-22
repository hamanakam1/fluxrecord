"""
Bearer token handling for request pipelines.

Framework free on purpose: the core stays stdlib-only and free of any web
framework, so this module deals in plain mappings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .jwt import AuthError, Claims

__all__ = ["bearer_token", "require_scope", "require_subject"]


def bearer_token(headers: Mapping[str, Any]) -> str:
    """Pull the bearer token out of request headers.

    Raises:
        AuthError: the header is missing or is not a bearer token
    """
    raw = ""
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            raw = str(value)
            break
    if not raw:
        raise AuthError("missing authorization header", status=401)
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("authorization header must be a bearer token", status=401)
    token = parts[1].strip()
    if not token:
        raise AuthError("bearer token is empty", status=401)
    return token


def require_scope(claims: Claims, scope: str) -> None:
    """Require that the token carries a scope."""
    if scope not in claims.scopes:
        raise AuthError(f"token is missing the {scope} scope", status=403)


def require_subject(claims: Claims, subject: str) -> None:
    """Require that the token was issued for a particular subject."""
    if claims.subject != subject:
        raise AuthError("token is for another subject", status=403)
