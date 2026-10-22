"""
JWT authentication for the fluxrecord API.

Stdlib only, framework free. See `jwt.py` for the token rules and
`routes.py` for the request shape.
"""

from __future__ import annotations

from .jwt import (
    ALGORITHM,
    AuthError,
    Claims,
    RevocationList,
    decode,
    introspect,
    issue,
    verify,
)
from .middleware import bearer_token, require_scope, require_subject
from .routes import AuthRoutes, Response

__all__ = [
    "ALGORITHM",
    "AuthError",
    "AuthRoutes",
    "Claims",
    "Response",
    "RevocationList",
    "bearer_token",
    "decode",
    "introspect",
    "issue",
    "require_scope",
    "require_subject",
    "verify",
]
