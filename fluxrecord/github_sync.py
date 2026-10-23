"""
Durable GitHub observations and an idempotent outbox.

Boundary rules, enforced here rather than assumed:

  - the ``gh`` CLI is the only way out; no token is read, stored, or logged
  - every call uses a fixed argv (no shell) and a 30 second timeout
  - a body carries a marker checked over bounded, validated comment pages
    before sending; run one publisher at a time for retry deduplication
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
    "MAX_COMMENT_PAGES",
    "MARKER_NAMESPACE",
    "LEGACY_MARKER_NAMESPACE",
    "marker_for",
    "legacy_marker_for",
    "markers_for",
    "is_published",
]

TIMEOUT_SECONDS = 30
MAX_KEY_LENGTH = 100
#: Bound marker discovery; an incomplete search must never permit a write.
MAX_COMMENT_PAGES = 20
#: A response larger than this is refused rather than held in memory.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

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
# The key is written into the comment body as part of the marker, so it is
# restricted to characters that cannot break the marker or the comment.
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    repo TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
    revision TEXT NOT NULL, observed REAL NOT NULL, summary TEXT NOT NULL,
    action TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
    ticket TEXT, actor TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (repo, kind, identity, revision));
CREATE TABLE IF NOT EXISTS observation_heads (
    repo TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
    revision TEXT NOT NULL, changed REAL NOT NULL,
    PRIMARY KEY (repo, kind, identity));
CREATE TABLE IF NOT EXISTS outbox (
    repo TEXT NOT NULL, issue INTEGER NOT NULL, key TEXT NOT NULL,
    body_hash TEXT NOT NULL, marker TEXT NOT NULL, state TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (repo, issue, key));
"""

Runner = Callable[[list[str]], Any]


#: The namespace the idempotency marker is written in. It is this
#: project's own name, because the marker is a wire format: another
#: project's name in it is a name this project would have to break later.
MARKER_NAMESPACE = "fluxrecord"

#: The namespace written before the rename. It is still read, so a comment
#: published by an earlier version is recognised and never sent twice. It
#: is never written.
LEGACY_MARKER_NAMESPACE = "taskrivet"


def marker_for(key: str, body_hash: str) -> str:
    """The idempotency marker appended to an outbox body."""
    return f"<!-- {MARKER_NAMESPACE}:{key}:{body_hash[:16]} -->"


def legacy_marker_for(key: str, body_hash: str) -> str:
    """The marker written before the namespace was renamed."""
    return f"<!-- {LEGACY_MARKER_NAMESPACE}:{key}:{body_hash[:16]} -->"


def markers_for(key: str, body_hash: str) -> tuple[str, ...]:
    """Every marker that identifies this body.

    The one written now, and the one written before the namespace was
    renamed. Both are read so that a comment published by an earlier
    version of this tool is recognised during the rename's window.
    """
    return (marker_for(key, body_hash), legacy_marker_for(key, body_hash))


def is_published(comment_bodies: list[str], key: str, body_hash: str) -> bool:
    """Whether a comment carrying this body has already been published."""
    markers = markers_for(key, body_hash)
    return any(marker in body for body in comment_bodies for marker in markers)


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
    if len(result.stdout) > MAX_RESPONSE_BYTES:
        raise OSError(f"gh response exceeded {MAX_RESPONSE_BYTES} bytes; refused")
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


def _status(kind: str, record: dict[str, Any]) -> str:
    """The record's own state, as the observation should report it."""
    if kind == "pulls":
        if record.get("merged"):
            return "merged"
        return str(record.get("state", ""))
    if kind in ("issues", "releases"):
        return str(record.get("state") or record.get("status") or "")
    if kind == "runs":
        return str(record.get("conclusion") or record.get("status") or "")
    return ""


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
        if sqlite3.sqlite_version_info < (3, 25, 0):
            raise OSError("GitHub sync requires SQLite 3.25 or newer for observation migration")
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(observations)")}
        if "status" not in columns:
            self.db.execute("ALTER TABLE observations ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        try:
            self._backfill_heads()
        except BaseException:
            self.db.close()
            raise
        self._run: Runner = runner if runner is not None else _gh_json

    def _backfill_heads(self) -> None:
        """Add missing heads from legacy history without replacing live heads.

        Older databases only retain the first observation of each revision.
        Their most recently observed row, with rowid breaking timestamp ties,
        is the best recoverable head. A past reversion cannot be recovered.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO observation_heads(repo,kind,identity,revision,changed)"
                " SELECT repo,kind,identity,revision,observed FROM ("
                " SELECT o.repo,o.kind,o.identity,o.revision,o.observed,"
                " ROW_NUMBER() OVER (PARTITION BY o.repo,o.kind,o.identity"
                " ORDER BY o.observed DESC,o.rowid DESC) AS position FROM observations o"
                " WHERE NOT EXISTS (SELECT 1 FROM observation_heads h"
                " WHERE h.repo=o.repo AND h.kind=o.kind AND h.identity=o.identity)"
                ") WHERE position=1"
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> GitHubSync:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ read

    def _observe(self, repo: str, kind: str, record: dict[str, Any]) -> bool:
        """Retain a revision and atomically advance its last-observed head.

        Returns True only for a transition from a previous current revision.
        Missing or deleted remote entries are not inferred from poll absence.
        """
        identity = _identity(kind, record)
        if not identity:
            return False
        revision = _revision(record)
        actor = record.get("user") or record.get("actor") or {}
        login = actor.get("login", "") if isinstance(actor, dict) else ""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            known = self.db.execute(
                "SELECT revision FROM observation_heads WHERE repo=? AND kind=? AND identity=?",
                (repo, kind, identity),
            ).fetchone()
            changed = known is not None and known["revision"] != revision
            observed = time.time()
            self.db.execute(
                "INSERT INTO observations"
                "(repo,kind,identity,revision,observed,summary,action,url,ticket,actor,status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(repo,kind,identity,revision) DO NOTHING",
                (
                    repo,
                    kind,
                    identity,
                    revision,
                    observed,
                    _summary(kind, record),
                    ACTIONS[kind],
                    str(record.get("html_url", "")),
                    _ticket_ref(record),
                    login,
                    _status(kind, record),
                ),
            )
            self.db.execute(
                "INSERT INTO observation_heads(repo,kind,identity,revision,changed) VALUES (?,?,?,?,?)"
                " ON CONFLICT(repo,kind,identity) DO UPDATE"
                " SET revision=excluded.revision,changed=excluded.changed"
                " WHERE observation_heads.revision<>excluded.revision",
                (repo, kind, identity, revision, observed),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
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
                query = "per_page=100"
                if kind in ("issues", "pulls"):
                    # Include closures and prioritize recent transitions
                    # within the caller's bounded page budget.
                    query += "&state=all&sort=updated&direction=desc"
                if page > 1:
                    query += f"&page={page}"
                payload = self._run(["api", f"{endpoint}?{query}"])
                if kind == "runs":
                    # GitHub wraps run pages; injected runners may still use
                    # the bare list accepted by earlier versions.
                    if isinstance(payload, dict):
                        payload = payload.get("workflow_runs")
                    if not isinstance(payload, list):
                        raise OSError("GitHub workflow runs response must contain a list")
                    if any(not isinstance(record, dict) for record in payload):
                        raise OSError("GitHub workflow runs response must contain objects")
                if not isinstance(payload, list) or not payload:
                    break
                accepted = 0
                for record in payload:
                    if not isinstance(record, dict):
                        continue
                    # GitHub includes pull requests in its issues endpoint.
                    # They belong to the separately selected pulls kind.
                    if kind == "issues" and "pull_request" in record:
                        continue
                    accepted += 1
                    observed += 1
                    if self._observe(repo, kind, record):
                        changed += 1
                by_kind[kind] = by_kind.get(kind, 0) + accepted
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

    def observations(
        self,
        repo: str,
        kind: str | None = None,
        action: str | None = None,
        limit: int = 200,
        latest: bool = False,
    ) -> list[dict[str, Any]]:
        """Revision history, or last-observed heads with latest=True.

        A head records the last state observed locally, not authoritative
        live state. Its revision keeps its original observation timestamp.
        """
        query = "SELECT o.* FROM observations o"
        if latest:
            query += (
                " JOIN observation_heads h ON h.repo=o.repo AND h.kind=o.kind"
                " AND h.identity=o.identity AND h.revision=o.revision"
            )
        query += " WHERE o.repo=?"
        params: list[Any] = [repo]
        if kind:
            query += " AND o.kind=?"
            params.append(kind)
        if action:
            query += " AND o.action=?"
            params.append(action)
        ordering = "h.changed" if latest else "o.observed"
        query += f" ORDER BY {ordering} DESC, o.kind, o.identity LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.db.execute(query, params)]

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
        if not _KEY.fullmatch(key):
            raise ValueError("key must be a plain token: letters, digits, . _ : -")
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
            " url=CASE WHEN outbox.body_hash=excluded.body_hash THEN outbox.url ELSE '' END,"
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

    def _has_marker(self, repo: str, issue: int, key: str, body_hash: str) -> bool:
        """Find a marker, or establish absence over a complete bounded lookup.

        A found marker is sufficient even on a full page. Absence requires
        a short final page; malformed or capped results leave the outbox
        unchanged so an incomplete lookup cannot authorize a duplicate.
        """
        endpoint = f"repos/{repo}/issues/{issue}/comments"
        for page in range(1, MAX_COMMENT_PAGES + 1):
            query = "per_page=100" if page == 1 else f"per_page=100&page={page}"
            payload = self._run(["api", f"{endpoint}?{query}"])
            if (
                not isinstance(payload, list)
                or len(payload) > 100
                or any(
                    not isinstance(item, dict) or not isinstance(item.get("body"), str)
                    for item in payload
                )
            ):
                raise OSError("gh returned a malformed issue comments page")
            if is_published([item["body"] for item in payload], key, body_hash):
                return True
            if len(payload) < 100:
                return False
        raise OSError(f"comment marker lookup exceeded {MAX_COMMENT_PAGES} pages")

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
            marker = marker_for(row["key"], row["body_hash"])
            try:
                found = self._has_marker(repo, int(row["issue"]), row["key"], row["body_hash"])
            except OSError:
                failed.append(row["key"])
                continue
            if found:
                if self._set_state(row, "confirmed"):
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
            self._set_state(row, "sent", url, marker)
            # A successful POST describes this attempt, even if another
            # connection has since queued a replacement under the same key.
            sent.append(row["key"])
        pending = self.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE repo=? AND state='pending'", (repo,)
        ).fetchone()[0]
        return {
            "repo": repo,
            "sent": sent,
            "confirmed": confirmed,
            "failed": failed,
            "pending": pending,
            "note": "Comments carry a retry marker; incomplete lookups remain pending.",
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
                found = self._has_marker(repo, int(row["issue"]), row["key"], row["body_hash"])
            except OSError:
                missing.append(row["key"])
                continue
            if found and self._set_state(row, "confirmed"):
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
        self,
        row: sqlite3.Row,
        state: str,
        url: str | None = None,
        marker: str | None = None,
    ) -> bool:
        """Apply a result only to the payload and state this attempt read.

        The state, receipt and marker change together. A reply from an old
        attempt must not overwrite a replacement queued during transport.
        No transaction or write lock is held while calling GitHub.
        """
        result = self.db.execute(
            "UPDATE outbox SET state=?, url=COALESCE(?,url), marker=COALESCE(?,marker), updated=?"
            " WHERE repo=? AND issue=? AND key=? AND body_hash=? AND state=?",
            (
                state,
                url,
                marker,
                time.time(),
                row["repo"],
                row["issue"],
                row["key"],
                row["body_hash"],
                row["state"],
            ),
        )
        return result.rowcount == 1
