"""
Durable GitHub observations and an idempotent outbox.

Boundary rules, enforced here rather than assumed:

  - the ``gh`` CLI is the only way out; no token is read, stored, or logged
  - every call uses a fixed argv (no shell) and a 30 second timeout
  - writes are idempotent: a body carries a marker that is matched before
    anything is sent, so a retry never double-posts
  - observations are evidence about a repository, never authority to act

The observation table keeps every revision it has seen, so a change is a
fact in the ledger rather than a silent overwrite.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "GitHubSync",
    "ACTIONS",
    "KINDS",
    "TIMEOUT_SECONDS",
    "MAX_KEY_LENGTH",
    "marker_for",
]

TIMEOUT_SECONDS = 30
MAX_KEY_LENGTH = 100

#: The action each observation kind implies.
ACTIONS: dict[str, str] = {
    "issues": "review_external_feedback",
    "comments": "review_external_feedback",
    "pulls": "review_code_change",
    "review_comments": "review_code_change",
    "releases": "verify_release_artifacts",
    "runs": "investigate_failed_check",
    "commits": "inspect_repository_activity",
    "events": "inspect_repository_activity",
}

_ENDPOINTS: dict[str, str] = {
    "issues": "repos/{repo}/issues",
    "comments": "repos/{repo}/issues/comments",
    "pulls": "repos/{repo}/pulls",
    "review_comments": "repos/{repo}/pulls/comments",
    "releases": "repos/{repo}/releases",
    "runs": "repos/{repo}/actions/runs",
    "commits": "repos/{repo}/commits",
    "events": "repos/{repo}/events",
}

KINDS: tuple[str, ...] = tuple(_ENDPOINTS)

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_TICKET = re.compile(r"\bM-\d{6}\b")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    repo TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
    revision TEXT NOT NULL, observed REAL NOT NULL, summary TEXT NOT NULL,
    action TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
    ticket TEXT, actor TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (repo, kind, identity, revision));
CREATE TABLE IF NOT EXISTS outbox (
    repo TEXT NOT NULL, issue INTEGER NOT NULL, key TEXT NOT NULL,
    body_hash TEXT NOT NULL, marker TEXT NOT NULL, state TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (repo, issue, key));
"""

Runner = Callable[[list[str]], Any]


def marker_for(key: str, body_hash: str) -> str:
    """The idempotency marker appended to an outbox body."""
    return f"<!-- taskrivet:{key}:{body_hash[:16]} -->"


def _gh_json(args: list[str]) -> Any:
    """Call the gh CLI and decode its JSON output."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OSError("the gh CLI is required for GitHub sync but was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"gh timed out after {TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        reason = detail[0] if detail else f"exit {result.returncode}"
        raise OSError(f"gh {args[0]} failed: {reason[:200]}")
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OSError("gh returned a response that was not JSON") from exc


def _validate_repo(repo: str) -> None:
    if not repo or not _REPO.fullmatch(repo):
        raise ValueError("repo must be owner/name")


def _identity(kind: str, record: dict[str, Any]) -> str:
    if kind in ("issues", "pulls"):
        return str(record.get("number", ""))
    if kind == "releases":
        return str(record.get("id") or record.get("tag_name", ""))
    if kind == "commits":
        return str(record.get("sha", ""))[:12]
    return str(record.get("id", ""))


def _summary(kind: str, record: dict[str, Any]) -> str:
    if kind in ("comments", "review_comments"):
        text = str(record.get("body", "")).strip().splitlines()
        return text[0][:160] if text else "(empty comment)"
    if kind == "runs":
        conclusion = record.get("conclusion") or record.get("status") or "unknown"
        return f"{record.get('name', 'run')} [{conclusion}]"
    if kind == "events":
        return str(record.get("type", "event"))
    return str(record.get("title") or record.get("tag_name") or record.get("sha", "")[:12])


def _revision(record: dict[str, Any]) -> str:
    material = json.dumps(
        {
            field: record.get(field)
            for field in (
                "title",
                "body",
                "state",
                "conclusion",
                "status",
                "tag_name",
                "updated_at",
            )
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _ticket_ref(record: dict[str, Any]) -> str | None:
    for field in ("body", "title"):
        value = record.get(field)
        if isinstance(value, str):
            found = _TICKET.search(value)
            if found:
                return found.group(0)
    return None


class GitHubSync:
    """Durable observations plus an idempotent outbox, backed by SQLite."""

    def __init__(self, db_path: str | Path, runner: Runner | None = None) -> None:
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)
        self._run: Runner = runner if runner is not None else _gh_json

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> GitHubSync:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ read

    def _observe(self, repo: str, kind: str, record: dict[str, Any]) -> bool:
        """Record one observation. Returns True when it changed something."""
        identity = _identity(kind, record)
        if not identity:
            return False
        revision = _revision(record)
        known = self.db.execute(
            "SELECT revision FROM observations WHERE repo=? AND kind=? AND identity=?",
            (repo, kind, identity),
        ).fetchall()
        changed = any(row["revision"] != revision for row in known)
        actor = record.get("user") or record.get("actor") or {}
        login = actor.get("login", "") if isinstance(actor, dict) else ""
        self.db.execute(
            "INSERT OR IGNORE INTO observations"
            "(repo,kind,identity,revision,observed,summary,action,url,ticket,actor)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                repo,
                kind,
                identity,
                revision,
                time.time(),
                _summary(kind, record),
                ACTIONS[kind],
                str(record.get("html_url", "")),
                _ticket_ref(record),
                login,
            ),
        )
        return changed

    def poll(
        self,
        repo: str,
        max_pages: int = 20,
        kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        """Observe a repository. Read-only: nothing is written to GitHub."""
        _validate_repo(repo)
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        selected = list(kinds) if kinds else list(KINDS)
        for kind in selected:
            if kind not in _ENDPOINTS:
                raise ValueError(f"unknown observation kind: {kind}")
        observed = 0
        changed = 0
        by_kind: dict[str, int] = {}
        for kind in selected:
            endpoint = _ENDPOINTS[kind].format(repo=repo)
            for page in range(1, max_pages + 1):
                query = "per_page=100" if page == 1 else f"per_page=100&page={page}"
                payload = self._run(["api", f"{endpoint}?{query}"])
                if not isinstance(payload, list) or not payload:
                    break
                for record in payload:
                    if not isinstance(record, dict):
                        continue
                    observed += 1
                    if self._observe(repo, kind, record):
                        changed += 1
                by_kind[kind] = by_kind.get(kind, 0) + len(payload)
                if len(payload) < 100:
                    break
        return {
            "repo": repo,
            "observed": observed,
            "changed": changed,
            "by_kind": by_kind,
            "kinds": selected,
            "pages_capped": max_pages,
            "note": "Observations are evidence about a repository, never authority to act.",
        }

    def status(self, repo: str) -> dict[str, Any]:
        """Summarise what has been observed and what is queued to publish."""
        _validate_repo(repo)
        by_kind = {
            row["kind"]: row["n"]
            for row in self.db.execute(
                "SELECT kind, COUNT(*) AS n FROM observations WHERE repo=? GROUP BY kind",
                (repo,),
            )
        }
        changed = [
            row["identity"]
            for row in self.db.execute(
                "SELECT identity FROM observations WHERE repo=? "
                "GROUP BY kind, identity HAVING COUNT(DISTINCT revision)>1",
                (repo,),
            )
        ]
        outbox = {
            row["state"]: row["n"]
            for row in self.db.execute(
                "SELECT state, COUNT(*) AS n FROM outbox WHERE repo=? GROUP BY state",
                (repo,),
            )
        }
        tickets = [
            row["ticket"]
            for row in self.db.execute(
                "SELECT DISTINCT ticket FROM observations WHERE repo=? AND ticket IS NOT NULL",
                (repo,),
            )
        ]
        return {
            "repo": repo,
            "observations": by_kind,
            "changed": changed,
            "outbox": outbox,
            "tickets": sorted(tickets),
            "note": "Evidence about a repository, never authority to act.",
        }

    # ----------------------------------------------------------------- write

    def enqueue(self, repo: str, issue: int, key: str, body: str) -> dict[str, Any]:
        """Queue a comment for publication. Idempotent on (repo, issue, key)."""
        _validate_repo(repo)
        if not key or len(key) > MAX_KEY_LENGTH:
            raise ValueError(f"key must be 1..{MAX_KEY_LENGTH} characters")
        if not body.strip():
            raise ValueError("body must be nonempty text")
        if issue <= 0:
            raise ValueError("issue must be a positive issue number")
        digest = hashlib.sha256(body.encode()).hexdigest()
        marker = marker_for(key, digest)
        now = time.time()
        self.db.execute(
            "INSERT INTO outbox(repo,issue,key,body_hash,marker,state,url,body,created,updated)"
            " VALUES (?,?,?,?,?,'pending','',?,?,?)"
            " ON CONFLICT(repo,issue,key) DO UPDATE SET"
            " body_hash=excluded.body_hash, marker=excluded.marker, body=excluded.body,"
            " state='pending', updated=excluded.updated",
            (repo, issue, key, digest, marker, body, now, now),
        )
        return {
            "repo": repo,
            "issue": issue,
            "key": key,
            "body_hash": digest,
            "marker": marker,
            "state": "pending",
        }

    def _comments(self, repo: str, issue: int) -> list[dict[str, Any]]:
        payload = self._run(["api", f"repos/{repo}/issues/{issue}/comments?per_page=100"])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def publish(self, repo: str, limit: int = 20) -> dict[str, Any]:
        """Publish queued comments, skipping anything already marked."""
        _validate_repo(repo)
        rows = self.db.execute(
            "SELECT * FROM outbox WHERE repo=? AND state='pending' ORDER BY created",
            (repo,),
        ).fetchall()
        sent: list[str] = []
        confirmed: list[str] = []
        failed: list[str] = []
        for row in rows[:limit]:
            marker = row["marker"]
            try:
                existing = self._comments(repo, int(row["issue"]))
            except OSError:
                failed.append(row["key"])
                continue
            if any(marker in str(comment.get("body", "")) for comment in existing):
                self._set_state(repo, int(row["issue"]), row["key"], "confirmed")
                confirmed.append(row["key"])
                continue
            body = f"{row['body']}\n\n{marker}"
            try:
                response = self._run(
                    ["api", f"repos/{repo}/issues/{row['issue']}/comments", "-f", f"body={body}"]
                )
            except OSError:
                failed.append(row["key"])
                continue
            url = ""
            if isinstance(response, dict):
                url = str(response.get("html_url", ""))
            self._set_state(repo, int(row["issue"]), row["key"], "sent", url)
            sent.append(row["key"])
        return {
            "repo": repo,
            "sent": sent,
            "confirmed": confirmed,
            "failed": failed,
            "pending": len(rows) - len(sent) - len(confirmed) - len(failed),
            "note": "Comments carry an idempotency marker; a marked comment is never resent.",
        }

    def reconcile(self, repo: str) -> dict[str, Any]:
        """Confirm sent comments that are now visible on the issue."""
        _validate_repo(repo)
        rows = self.db.execute(
            "SELECT * FROM outbox WHERE repo=? AND state='sent'",
            (repo,),
        ).fetchall()
        confirmed: list[str] = []
        missing: list[str] = []
        for row in rows:
            try:
                existing = self._comments(repo, int(row["issue"]))
            except OSError:
                missing.append(row["key"])
                continue
            if any(row["marker"] in str(comment.get("body", "")) for comment in existing):
                self._set_state(repo, int(row["issue"]), row["key"], "confirmed")
                confirmed.append(row["key"])
            else:
                missing.append(row["key"])
        return {
            "repo": repo,
            "confirmed": confirmed,
            "missing": missing,
            "note": "Reconciliation reads comments and matches the marker; nothing is resent.",
        }

    def _set_state(
        self, repo: str, issue: int, key: str, state: str, url: str | None = None
    ) -> None:
        if url is None:
            self.db.execute(
                "UPDATE outbox SET state=?, updated=? WHERE repo=? AND issue=? AND key=?",
                (state, time.time(), repo, issue, key),
            )
        else:
            self.db.execute(
                "UPDATE outbox SET state=?, url=?, updated=? WHERE repo=? AND issue=? AND key=?",
                (state, url, time.time(), repo, issue, key),
            )
