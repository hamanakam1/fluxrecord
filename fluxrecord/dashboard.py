"""
Bounded, read-only preview of a local dashboard.

The boundary is enforced here rather than assumed:

  - local origins only (loopback host)
  - GET only, and redirects are refused rather than followed
  - 8 MiB response ceiling
  - 10 second timeout
  - no credentials, no cookies, no proxy, no telemetry

A preview is evidence about a dashboard, never authority over a task.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

__all__ = ["preview", "MAX_BYTES", "TIMEOUT_SECONDS", "LOOPBACK_HOSTS"]

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 10
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_STATUS_TOKEN = re.compile(r"[a-z][a-z0-9_-]*")


class _NoRedirect(request.HTTPRedirectHandler):
    """Refuse redirects: a preview must read exactly the URL it was given."""

    def redirect_request(
        self,
        req: request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _target(url: str, status: str, offset: int) -> str:
    """Validate the origin and build the preview URL."""
    if not url or not url.strip():
        raise ValueError("url must be a nonempty local dashboard origin")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError("dashboard url must use http or https")
    host = (parts.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"dashboard url must be local; refusing host {host!r}")
    if parts.username or parts.password:
        raise ValueError("dashboard url must not carry credentials")
    if not _STATUS_TOKEN.fullmatch(status):
        raise ValueError("status filter must be a lowercase token")
    if offset < 0:
        raise ValueError("offset must be nonnegative")
    query = f"status={status}&offset={offset}"
    base = f"{parts.scheme}://{parts.netloc}/api/tickets"
    return f"{base}?{query}"


def _normalize(entry: Any) -> dict[str, Any]:
    """Reduce an arbitrary dashboard record to the fields fluxrecord trusts."""
    if not isinstance(entry, dict):
        return {"title": str(entry)}
    record: dict[str, Any] = {}
    for field in ("id", "title", "status", "priority", "owner", "updated_at"):
        if field in entry:
            record[field] = entry[field]
    return record


def preview(url: str, status: str = "planned", offset: int = 0) -> dict[str, Any]:
    """Fetch a bounded preview of the local dashboard.

    Args:
        url: local dashboard origin, e.g. http://127.0.0.1:8080
        status: status filter passed through to the dashboard
        offset: row offset passed through to the dashboard

    Raises:
        ValueError: the url is not a local origin, or the offset is invalid
        OSError: the dashboard could not be read within the boundary
    """
    target = _target(url, status, offset)
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    req = request.Request(
        target,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "fluxrecord"},
    )
    try:
        with opener.open(req, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read(MAX_BYTES + 1)
            status_code = getattr(response, "status", 200)
    except error.HTTPError as exc:
        exc.close()
        raise OSError(f"dashboard refused the preview request: {exc.code} {exc.reason}") from exc

    truncated = len(payload) > MAX_BYTES
    if truncated:
        payload = payload[:MAX_BYTES]

    if truncated:
        # A clipped body is not a document. Report the clip instead of
        # pretending the partial bytes are trustworthy.
        return {
            "url": target,
            "origin": url,
            "status": status,
            "offset": offset,
            "status_code": status_code,
            "tickets": [],
            "count": 0,
            "bytes": len(payload),
            "truncated": True,
            "boundary": {
                "method": "GET",
                "redirects": "refused",
                "max_bytes": MAX_BYTES,
                "timeout_seconds": TIMEOUT_SECONDS,
                "credentials": "none",
            },
            "note": "Response exceeded the byte ceiling and was clipped; no records were read.",
        }

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OSError(f"dashboard response was not JSON: {exc}") from exc

    if isinstance(document, dict):
        raw = document.get("tickets", document.get("items", document.get("data", [])))
    else:
        raw = document
    if not isinstance(raw, list):
        raw = []
    tickets = [_normalize(entry) for entry in raw]

    return {
        "url": target,
        "origin": url,
        "status": status,
        "offset": offset,
        "status_code": status_code,
        "tickets": tickets,
        "count": len(tickets),
        "bytes": len(payload),
        "truncated": truncated,
        "boundary": {
            "method": "GET",
            "redirects": "refused",
            "max_bytes": MAX_BYTES,
            "timeout_seconds": TIMEOUT_SECONDS,
            "credentials": "none",
        },
        "note": "Read-only preview. Evidence about a dashboard, never authority over a task.",
    }
