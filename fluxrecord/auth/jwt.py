"""
JWT issuance and verification.

Stdlib only: HMAC-SHA256 through hmac and hashlib, base64url by hand. No
third-party crypto, no network, no clock beyond the caller's.

The rules that matter, and where they live:

  - the algorithm is fixed to HS256 and read from the header, never trusted
    from it, so "alg: none" and algorithm confusion are refused
  - signatures are compared with hmac.compare_digest
  - exp, nbf, iss and aud are checked on every verification
  - every token carries a jti, so a token can be revoked
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ALGORITHM",
    "AuthError",
    "Claims",
    "RevocationList",
    "decode",
    "introspect",
    "issue",
    "verify",
]

ALGORITHM = "HS256"
_LEEWAY_SECONDS = 30


class AuthError(Exception):
    """A token was malformed, unsigned, expired or otherwise unacceptable."""

    def __init__(self, reason: str, status: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(signing_input: bytes, secret: str | bytes) -> bytes:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, signing_input, hashlib.sha256).digest()


@dataclass(frozen=True)
class Claims:
    """What a verified token asserts."""

    subject: str
    issued_at: int
    expires_at: int
    issuer: str = ""
    audience: str = ""
    token_id: str = ""
    token_type: str = "access"
    scopes: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sub": self.subject,
            "iat": self.issued_at,
            "exp": self.expires_at,
        }
        if self.issuer:
            payload["iss"] = self.issuer
        if self.audience:
            payload["aud"] = self.audience
        if self.token_id:
            payload["jti"] = self.token_id
        if self.token_type:
            payload["typ"] = self.token_type
        if self.scopes:
            payload["scope"] = " ".join(self.scopes)
        payload.update(self.extra)
        return payload

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= int(time.time())


class RevocationList:
    """Token ids that must not be accepted again."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()

    def revoke(self, token_id: str) -> None:
        if token_id:
            self._revoked.add(token_id)

    def is_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked

    def __len__(self) -> int:
        return len(self._revoked)


def issue(
    subject: str,
    secret: str | bytes,
    ttl_seconds: int = 900,
    issuer: str = "",
    audience: str = "",
    scopes: Iterable[str] = (),
    token_type: str = "access",
    now: int | None = None,
    token_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, Claims]:
    """Issue a signed token and return it with its claims."""
    if not subject:
        raise ValueError("subject must be nonempty")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    issued = int(time.time()) if now is None else int(now)
    claims = Claims(
        subject=subject,
        issued_at=issued,
        expires_at=issued + int(ttl_seconds),
        issuer=issuer,
        audience=audience,
        token_id=token_id or secrets.token_urlsafe(16),
        token_type=token_type,
        scopes=tuple(scopes),
        extra=dict(extra or {}),
    )
    header = {"alg": ALGORITHM, "typ": "JWT"}
    header_part = _b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    payload_part = _b64encode(
        json.dumps(claims.to_dict(), separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = _b64encode(_sign(signing_input, secret))
    return f"{header_part}.{payload_part}.{signature}", claims


def decode(token: str, secret: str | bytes, now: int | None = None) -> Claims:
    """Decode and verify a token. Raises AuthError when it is not acceptable."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise AuthError("malformed token")
    header_part, payload_part, signature_part = token.split(".")
    try:
        header = json.loads(_b64decode(header_part))
        payload = json.loads(_b64decode(payload_part))
        signature = _b64decode(signature_part)
    except (ValueError, TypeError) as exc:
        raise AuthError("malformed token") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise AuthError("malformed token")
    if header.get("alg") != ALGORITHM:
        raise AuthError("unsupported algorithm")
    expected = _sign(f"{header_part}.{payload_part}".encode(), secret)
    if not hmac.compare_digest(expected, signature):
        raise AuthError("bad signature")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("missing subject")
    issued = payload.get("iat")
    expires = payload.get("exp")
    if not isinstance(issued, int) or not isinstance(expires, int):
        raise AuthError("missing issued or expiry")
    moment = int(time.time()) if now is None else int(now)
    if expires <= moment - _LEEWAY_SECONDS:
        raise AuthError("token expired")
    if issued > moment + _LEEWAY_SECONDS:
        raise AuthError("token is not valid yet")
    return Claims(
        subject=subject,
        issued_at=issued,
        expires_at=expires,
        issuer=str(payload.get("iss", "")),
        audience=str(payload.get("aud", "")),
        token_id=str(payload.get("jti", "")),
        token_type=str(payload.get("typ", "access")),
        scopes=tuple(str(payload.get("scope", "")).split()) if payload.get("scope") else (),
        extra={
            key: value
            for key, value in payload.items()
            if key not in ("sub", "iat", "exp", "iss", "aud", "jti", "typ", "scope")
        },
    )


def verify(
    token: str,
    secret: str | bytes,
    now: int | None = None,
    revoked: RevocationList | None = None,
    issuer: str = "",
    audience: str = "",
) -> Claims:
    """Verify a token and check it against the issuer, audience and denylist."""
    claims = decode(token, secret, now)
    if issuer and claims.issuer != issuer:
        raise AuthError("wrong issuer")
    if audience and claims.audience != audience:
        raise AuthError("wrong audience")
    if revoked is not None and revoked.is_revoked(claims.token_id):
        raise AuthError("token revoked")
    return claims


def introspect(
    token: str,
    secret: str | bytes,
    now: int | None = None,
    revoked: RevocationList | None = None,
) -> dict[str, Any]:
    """Describe a token the way RFC 7662 asks for.

    An unusable token is reported as inactive, not as an error: the point of
    introspection is to tell a relying party whether to trust the token.
    """
    try:
        claims = verify(token, secret, now, revoked)
    except AuthError:
        return {"active": False}
    return {
        "active": True,
        "sub": claims.subject,
        "iss": claims.issuer,
        "aud": claims.audience,
        "jti": claims.token_id,
        "typ": claims.token_type,
        "scope": " ".join(claims.scopes),
        "iat": claims.issued_at,
        "exp": claims.expires_at,
    }
