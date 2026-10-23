"""Local coordination ledger. No model calls, shell execution, or telemetry."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Conflict(ValueError):
    """An ownership, budget, or lifecycle constraint refused an operation."""


SCHEMA_VERSION = 7

#: Additive, idempotent migrations. Each entry moves a database from the
#: previous version to the next; nothing is ever dropped or rewritten.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        2,
        (
            "ALTER TABLE tasks ADD COLUMN title TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 2",
            "ALTER TABLE tasks ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE tasks ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE tasks ADD COLUMN blocks TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN seed_key TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tasks ADD COLUMN updated_at REAL",
        ),
    ),
    (
        3,
        (
            # The specification calls the working station in_progress.
            "UPDATE tasks SET status='in_progress' WHERE status='running'",
        ),
    ),
    (
        4,
        (
            # Profiles declare the tools they need.
            "ALTER TABLE profiles ADD COLUMN requires TEXT NOT NULL DEFAULT '[]'",
        ),
    ),
    (
        6,
        (
            # Each event carries the digest of the one before it.
            "ALTER TABLE events ADD COLUMN digest TEXT NOT NULL DEFAULT ''",
        ),
    ),
    (
        7,
        (
            # A packet remembers the status it was taken at, so a delta can
            # say that the ticket moved even when no new evidence arrived.
            "ALTER TABLE packets ADD COLUMN status TEXT NOT NULL DEFAULT ''",
        ),
    ),
    (
        5,
        (
            # Every packet is addressed by the hash of its content.
            "CREATE TABLE IF NOT EXISTS packets ("
            " id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),"
            " hash TEXT NOT NULL, created REAL NOT NULL, checkpoint_id INTEGER,"
            " tokens INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,"
            " context_hashes TEXT NOT NULL DEFAULT '[]',"
            " failure_fingerprints TEXT NOT NULL DEFAULT '[]',"
            " UNIQUE(task_id, hash))",
        ),
    ),
)

_JSON_COLUMNS = ("scope", "tags", "depends_on", "blocks")

#: A ticket id becomes a file name when a ticket is exported, so it is
#: restricted to characters that cannot walk out of a directory.
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: The failure taxonomy from SPEC.md. A failure outside it is a typo, not a
#: new category, so it is refused rather than recorded.
FAILURE_CATEGORIES = ("lease_expired", "budget", "tool", "dependency", "no_progress")

#: The kinds of work a ticket can be. The specification names them, so a
#: kind outside the list is refused rather than recorded.
TASK_KINDS = ("code", "fix", "closure", "planning", "product")

#: The context budget a ticket kind gets when nobody says otherwise. The
#: byte budget is exact; these are the defaults the specification names.
BUDGET_POLICY: dict[str, int] = {
    "code": 12000,
    "fix": 8000,
    "planning": 6000,
    "closure": 4000,
    "product": 8000,
}
DEFAULT_BUDGET = 12000


def positive(value: Any, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def required(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


class Ledger:
    def __init__(
        self,
        path: str | Path,
        tokenizer: Callable[[str], int] | None = None,
        chain_key: str | bytes | None = None,
    ) -> None:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        # A tokenizer is injected, never imported: the core stays stdlib-only
        # and the plug-in boundary stays a boundary.
        self.tokenizer = tokenizer
        # A chain key makes the event log tamper-evident. It is off by
        # default: a ledger that nobody signs is still a ledger.
        self.chain_key = chain_key
        try:
            self._prepare(path)
        except BaseException:
            self.db.close()
            raise
        fresh = (
            self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()
            is None
        )
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, objective TEXT NOT NULL, acceptance TEXT NOT NULL,
            kind TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
            token_budget INTEGER NOT NULL, created REAL NOT NULL,
            title TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 2,
            tags TEXT NOT NULL DEFAULT '[]', depends_on TEXT NOT NULL DEFAULT '[]',
            blocks TEXT NOT NULL DEFAULT '[]', owner TEXT NOT NULL DEFAULT '',
            seed_key TEXT NOT NULL DEFAULT '', updated_at REAL);
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            owner TEXT NOT NULL, status TEXT NOT NULL, lease_until REAL NOT NULL,
            tokens INTEGER NOT NULL DEFAULT 0, progress TEXT NOT NULL DEFAULT '',
            stagnant INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active ON sessions(task_id)
            WHERE status='active';
        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            summary TEXT NOT NULL, evidence TEXT NOT NULL, next_step TEXT NOT NULL,
            tokens INTEGER NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS contexts (
            hash TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_context (
            task_id TEXT NOT NULL REFERENCES tasks(id), hash TEXT NOT NULL REFERENCES contexts(hash),
            source TEXT NOT NULL, priority INTEGER NOT NULL, expires REAL,
            PRIMARY KEY(task_id, hash));
        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            session_id TEXT REFERENCES sessions(id), category TEXT NOT NULL,
            fingerprint TEXT NOT NULL, summary TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY, kinds TEXT NOT NULL, context_limit INTEGER NOT NULL,
            cost TEXT NOT NULL, verified_until REAL NOT NULL,
            requires TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, type TEXT NOT NULL, task_id TEXT,
            data TEXT NOT NULL, created REAL NOT NULL,
            digest TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS packets (
            id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            hash TEXT NOT NULL, created REAL NOT NULL, checkpoint_id INTEGER,
            tokens INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
            context_hashes TEXT NOT NULL DEFAULT '[]',
            failure_fingerprints TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT '',
            UNIQUE(task_id, hash));
        """)
        self._migrate(fresh)
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS task_seed_key ON tasks(seed_key) WHERE seed_key <> ''"
        )

    def _prepare(self, path: Path) -> None:
        """Everything the database needs before it can be used."""
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")

    def _migrate(self, fresh: bool) -> None:
        """Move the database forward, additively and idempotently."""
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 1
        if fresh:
            current = SCHEMA_VERSION
        else:
            for version, statements in _MIGRATIONS:
                if version <= current:
                    continue
                for statement in statements:
                    self._apply(statement)
                current = version
        self.db.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(current),),
        )

    def _apply(self, statement: str) -> None:
        """Apply one migration statement.

        A column that is already there is not an error: a database may have
        been written by a version of the code that had the column before the
        migration that adds it existed.
        """
        match = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", statement)
        if match:
            table, column = match.groups()
            columns = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
            if column in columns:
                return
        self.db.execute(statement)

    def schema_version(self) -> int:
        """The schema version this database is running."""
        row = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 1

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, kind: str, task: str | None, **data: Any) -> None:
        payload = json.dumps(data, sort_keys=True)
        created = time.time()
        digest = self._digest(kind, task, payload, created)
        self.db.execute(
            "INSERT INTO events(type,task_id,data,created,digest) VALUES (?,?,?,?,?)",
            (kind, task, payload, created, digest),
        )
        if digest:
            # The head of the chain is recorded beside the log, so that
            # truncating the end of the log is detectable. A chain is a
            # prefix: without this, dropping the last few events would
            # leave a perfectly valid chain over what was left.
            self.db.execute(
                "INSERT INTO meta(key,value) VALUES('chain_head',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (digest,),
            )

    def _digest(
        self,
        kind: str,
        task: str | None,
        payload: str,
        created: float,
        previous: str | None = None,
    ) -> str:
        """The digest of one event, chained to the one before it.

        With no chain key the digest is empty: the log is not signed, and
        says so by having nothing to check.
        """
        if self.chain_key is None:
            return ""
        key = self.chain_key.encode("utf-8") if isinstance(self.chain_key, str) else self.chain_key
        if previous is None:
            row = self.db.execute("SELECT digest FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            previous = str(row["digest"]) if row is not None else ""
        material = f"{previous}|{kind}|{task or ''}|{payload}|{created}".encode()
        return hmac.new(key, material, hashlib.sha256).hexdigest()

    def verify_chain(self) -> dict[str, Any]:
        """Check the event chain, and say where it breaks if it does.

        A signed ledger makes a silent edit detectable: the digest of an
        event covers the digest of the one before it, so changing an event
        in the middle invalidates everything after it.
        """
        events = self.db.execute("SELECT * FROM events ORDER BY seq").fetchall()
        if not events:
            return {
                "signed": self.chain_key is not None,
                "ok": True,
                "events": 0,
                "broken_at": None,
            }
        if self.chain_key is None:
            return {
                "signed": False,
                "ok": True,
                "events": len(events),
                "broken_at": None,
                "note": "The ledger is not signed, so the log cannot be checked.",
            }
        # A compaction leaves the digest of the last pruned event behind,
        # and the chain continues from there.
        previous = self._chain_base()
        for index, event in enumerate(events):
            expected = self._digest(
                str(event["type"]),
                event["task_id"],
                str(event["data"]),
                float(event["created"]),
                previous if previous or index else "",
            )
            if not str(event["digest"]):
                return {
                    "signed": True,
                    "ok": False,
                    "events": len(events),
                    "broken_at": int(event["seq"]),
                    "reason": "written before the ledger was signed",
                    "note": "The chain starts here: this event was written while "
                    "the ledger was unsigned, so there is nothing to check it "
                    "against.",
                }
            if expected != str(event["digest"]):
                return {
                    "signed": True,
                    "ok": False,
                    "events": len(events),
                    "broken_at": int(event["seq"]),
                    "reason": "changed after it was written",
                    "note": "The chain breaks here: this event, or one before it, "
                    "was changed after it was written.",
                }
            previous = str(event["digest"])
        return {
            "signed": True,
            "ok": self._head_matches(events),
            "events": len(events),
            "broken_at": None,
            "head": str(events[-1]["digest"]) if events else "",
            "note": self._head_note(events),
        }

    def _chain_base(self) -> str:
        """The digest the chain starts from, after a compaction."""
        row = self.db.execute("SELECT value FROM meta WHERE key='chain_base'").fetchone()
        return str(row["value"]) if row is not None else ""

    def _recorded_head(self) -> str:
        """The head of the chain, as recorded when the last event was
        written."""
        row = self.db.execute("SELECT value FROM meta WHERE key='chain_head'").fetchone()
        return str(row["value"]) if row is not None else ""

    def _head_matches(self, events: list[Any]) -> bool:
        """Whether the log still ends where it was recorded to end.

        A chain is a prefix, so a log that has been truncated at the end
        still verifies over what is left. The recorded head catches that,
        unless whoever truncated the log also updated the head — which
        they can, because the head is in the same file.
        """
        recorded = self._recorded_head()
        if not recorded:
            return True
        actual = str(events[-1]["digest"]) if events else ""
        return actual == recorded

    def _head_note(self, events: list[Any]) -> str:
        if self._head_matches(events):
            return "Every event matches the digest of the one before it."
        return (
            "Every event matches the digest of the one before it, but the log no "
            "longer ends where it was recorded to end: events were removed from "
            "the end, or the recorded head was changed."
        )

    def task(self, task_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown task: {task_id}")
        return dict(row)

    def _decode(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        """Decode the JSON columns of a task row for display."""
        view = dict(row)
        for field in _JSON_COLUMNS:
            if field in view and isinstance(view[field], str):
                try:
                    view[field] = json.loads(view[field])
                except json.JSONDecodeError:
                    pass
        return view

    def task_by_seed_key(self, seed_key: str) -> dict[str, Any] | None:
        """The task seeded with a key, if there is one."""
        if not seed_key:
            return None
        row = self.db.execute(
            "SELECT * FROM tasks WHERE seed_key=?",
            (seed_key,),
        ).fetchone()
        return self._decode(row) if row is not None else None

    def next_ticket_id(self) -> str:
        """The next free sequential ticket id, in the M-###### space."""
        highest = 0
        for row in self.db.execute("SELECT id FROM tasks"):
            match = re.fullmatch(r"M-([0-9]+)", str(row["id"]))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"M-{highest + 1:06d}"

    def task_view(self, task_id: str) -> dict[str, Any]:
        """A task as it appears on the wire: JSON columns decoded."""
        return self._decode(self.task(task_id))

    def add_task(
        self,
        task_id: str,
        objective: str,
        acceptance: str,
        kind: str = "code",
        scope: list[str] | None = None,
        token_budget: int = 8000,
        title: str = "",
        priority: int = 2,
        tags: list[str] | None = None,
        depends_on: list[str] | None = None,
        blocks: list[str] | None = None,
        owner: str = "",
        seed_key: str = "",
    ) -> dict[str, Any]:
        for value, name in [
            (task_id, "id"),
            (objective, "objective"),
            (acceptance, "acceptance"),
            (kind, "kind"),
        ]:
            required(value, name)
        if not _TASK_ID.fullmatch(task_id) or task_id in (".", ".."):
            raise ValueError(
                "id must be a plain identifier such as M-397001; it cannot contain path separators"
            )
        if kind not in TASK_KINDS:
            raise ValueError(f"kind must be one of {', '.join(TASK_KINDS)}")
        positive(token_budget, "token_budget")
        if type(priority) is not int or priority not in (1, 2, 3):
            raise ValueError("priority must be 1, 2, or 3")
        if seed_key and len(seed_key) > 100:
            raise ValueError("seed_key must be at most 100 characters")
        if scope is None:
            scope = [task_id]
        if not isinstance(scope, list) or not scope:
            raise ValueError("scope must be a nonempty list of resource keys")
        for key in scope:
            required(key, "scope key")
        for name, values in (
            ("tags", tags),
            ("depends_on", depends_on),
            ("blocks", blocks),
        ):
            for value in values or []:
                required(value, f"{name} entry")
        with self.transaction():
            # Serialize identity checks with the insert so concurrent callers
            # receive the same idempotent result or domain error as a retry.
            if seed_key:
                seeded = self.db.execute(
                    "SELECT id FROM tasks WHERE seed_key=?", (seed_key,)
                ).fetchone()
                if seeded:
                    if seeded["id"] != task_id:
                        raise ValueError(f"seed key already used by {seeded['id']}")
                    return self.task_view(task_id)
            if self.db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise ValueError(f"task already exists: {task_id}")
            now = time.time()
            self.db.execute(
                "INSERT INTO tasks(id,objective,acceptance,kind,scope,status,token_budget,"
                "created,title,priority,tags,depends_on,blocks,owner,seed_key,updated_at)"
                " VALUES (?,?,?,?,?,'ready',?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    objective,
                    acceptance,
                    kind,
                    json.dumps(sorted(set(scope))),
                    token_budget,
                    now,
                    title or objective,
                    priority,
                    json.dumps(tags or []),
                    json.dumps(depends_on or []),
                    json.dumps(blocks or []),
                    owner,
                    seed_key,
                    now,
                ),
            )
            self.event("task.created", task_id)
        return self.task_view(task_id)

    def _touch(self, task_id: str) -> None:
        """Stamp the task as changed."""
        self.db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), task_id))

    def _failure(self, task: str, session: str, category: str, summary: str) -> None:
        digest = hashlib.sha256(f"{category}:{summary}".encode()).hexdigest()[:16]
        self.db.execute(
            "INSERT INTO failures(task_id,session_id,category,fingerprint,summary,created) VALUES (?,?,?,?,?,?)",
            (task, session, category, digest, summary, time.time()),
        )
        self.event("failure.recorded", task, session=session, category=category, fingerprint=digest)

    def _expire(self) -> list[str]:
        rows = self.db.execute(
            "SELECT * FROM sessions WHERE status='active' AND lease_until<=?", (time.time(),)
        ).fetchall()
        expired: list[str] = []
        for row in rows:
            self.db.execute("UPDATE sessions SET status='expired' WHERE id=?", (row["id"],))
            self.db.execute("UPDATE tasks SET status='blocked' WHERE id=?", (row["task_id"],))
            self._touch(row["task_id"])
            self._failure(
                row["task_id"],
                row["id"],
                "lease_expired",
                "Ownership expired; reconcile the old worker before retrying.",
            )
            expired.append(str(row["id"]))
        return expired

    def reconcile(self) -> dict[str, Any]:
        """Expire leases that have run out, and report what was expired."""
        with self.transaction():
            expired = self._expire()
        return {"expired": expired, "count": len(expired)}

    def start(self, task_id: str, owner: str, lease_seconds: int = 900) -> dict[str, Any]:
        required(owner, "owner")
        positive(lease_seconds, "lease_seconds")
        self.reconcile()
        with self.transaction():
            task = self.task(task_id)
            if task["status"] != "ready":
                raise Conflict(f"{task_id} is {task['status']}; expected ready")
            self._check_dependencies(task)
            used = self.db.execute(
                "SELECT COALESCE(SUM(tokens),0) FROM sessions WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            if used >= task["token_budget"]:
                raise Conflict("task token budget exhausted across attempts")
            scopes = set(json.loads(task["scope"]))
            for row in self.db.execute(
                "SELECT t.id,t.scope,s.owner,s.lease_until FROM tasks t"
                " LEFT JOIN sessions s ON s.task_id=t.id AND s.status='active'"
                " WHERE t.status IN ('in_progress','blocked','review')"
            ):
                shared = scopes.intersection(json.loads(row["scope"]))
                if not shared:
                    continue
                holder = row["owner"] or "another worker"
                until = row["lease_until"]
                until_text = (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)) if until else "unknown"
                )
                raise Conflict(
                    f"resource scope reserved by {row['id']} "
                    f"({', '.join(sorted(shared))}); held by {holder} until {until_text}"
                )
            sid = uuid.uuid4().hex
            now = time.time()
            self.db.execute(
                "INSERT INTO sessions(id,task_id,owner,status,lease_until,created) VALUES (?,?,?,'active',?,?)",
                (sid, task_id, owner, now + lease_seconds, now),
            )
            self.db.execute(
                "UPDATE tasks SET status='in_progress',owner=? WHERE id=?", (owner, task_id)
            )
            self._touch(task_id)
            self.event("session.started", task_id, session=sid, owner=owner)
        return {"session_id": sid, "task_id": task_id, "lease_until": now + lease_seconds}

    def _session(self, sid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("unknown session")
        if row["status"] != "active" or row["lease_until"] <= time.time():
            raise Conflict("session is inactive or expired")
        return dict(row)

    def checkpoint(
        self,
        sid: str,
        summary: str,
        evidence: str,
        next_step: str,
        tokens: int,
        lease_seconds: int = 900,
    ) -> dict[str, Any]:
        for value, name in [
            (summary, "summary"),
            (evidence, "evidence"),
            (next_step, "next_step"),
        ]:
            required(value, name)
        if type(tokens) is not int or tokens < 0:
            raise ValueError("tokens must be a cumulative nonnegative session count")
        positive(lease_seconds, "lease_seconds")
        self.reconcile()
        with self.transaction():
            session = self._session(sid)
            if tokens < session["tokens"]:
                raise Conflict("cumulative token count cannot decrease")
            fingerprint = hashlib.sha256(evidence.encode()).hexdigest()
            stagnant = session["stagnant"] + 1 if fingerprint == session["progress"] else 0
            others = self.db.execute(
                "SELECT COALESCE(SUM(tokens),0) FROM sessions WHERE task_id=? AND id!=?",
                (session["task_id"], sid),
            ).fetchone()[0]
            exhausted = others + tokens >= self.task(session["task_id"])["token_budget"]
            blocked = exhausted or stagnant >= 2
            self.db.execute(
                "UPDATE sessions SET tokens=?,progress=?,stagnant=?,lease_until=?,status=? WHERE id=?",
                (
                    tokens,
                    fingerprint,
                    stagnant,
                    max(session["lease_until"], time.time() + lease_seconds),
                    "blocked" if blocked else "active",
                    sid,
                ),
            )
            self.db.execute(
                "INSERT INTO checkpoints(session_id,summary,evidence,next_step,tokens,created) VALUES (?,?,?,?,?,?)",
                (sid, summary, evidence, next_step, tokens, time.time()),
            )
            self.event(
                "session.checkpoint",
                session["task_id"],
                session=sid,
                tokens=tokens,
                stagnant=stagnant,
            )
            if blocked:
                self.db.execute(
                    "UPDATE tasks SET status='blocked' WHERE id=?", (session["task_id"],)
                )
                self._touch(session["task_id"])
                self._failure(
                    session["task_id"],
                    sid,
                    "budget" if exhausted else "no_progress",
                    "Task token budget reached."
                    if exhausted
                    else "Three consecutive checkpoints cite identical evidence.",
                )
        return {"session_id": sid, "status": "blocked" if blocked else "active", "tokens": tokens}

    def _check_dependencies(self, task: dict[str, Any]) -> None:
        """Refuse work whose dependencies are not met.

        The specification puts "dependencies met" on the way into READY, so
        a ticket that names a dependency cannot be worked on until that
        dependency is accepted.
        """
        for dependency in json.loads(task["depends_on"]):
            row = self.db.execute("SELECT status FROM tasks WHERE id=?", (dependency,)).fetchone()
            if row is None:
                raise Conflict(f"dependency {dependency} is not a known ticket")
            if row["status"] not in ("accepted", "done"):
                raise Conflict(f"dependency {dependency} is {row['status']}")

    def renew(self, sid: str, lease_seconds: int = 900) -> dict[str, Any]:
        """Extend the lease of an active session."""
        positive(lease_seconds, "lease_seconds")
        with self.transaction():
            session = self._session(sid)
            until = max(session["lease_until"], time.time() + lease_seconds)
            self.db.execute("UPDATE sessions SET lease_until=? WHERE id=?", (until, sid))
            self._touch(session["task_id"])
            self.event("session.renewed", session["task_id"], session=sid, lease_until=until)
        return {"session_id": sid, "task_id": session["task_id"], "lease_until": until}

    def handoff(self, sid: str, owner: str, reason: str) -> dict[str, Any]:
        """Hand a lease to another worker, deliberately.

        The lease continues; the owner changes. The reason is recorded,
        because a handoff without a reason is a handover nobody can
        explain later.
        """
        required(owner, "owner")
        required(reason, "reason")
        with self.transaction():
            session = self._session(sid)
            previous = str(session["owner"])
            self.db.execute("UPDATE sessions SET owner=? WHERE id=?", (owner, sid))
            self.db.execute("UPDATE tasks SET owner=? WHERE id=?", (owner, session["task_id"]))
            self._touch(session["task_id"])
            self.event(
                "session.handed_off",
                session["task_id"],
                session=sid,
                previous_owner=previous,
                owner=owner,
                reason=reason,
            )
        return {
            "session_id": sid,
            "task_id": session["task_id"],
            "previous_owner": previous,
            "owner": owner,
            "lease_until": session["lease_until"],
        }

    def adopt(self, sid: str, owner: str, reason: str) -> dict[str, Any]:
        """Take over a lease whose owner has gone away.

        The operator says so, and the reason is recorded. The lease is
        extended, because the new owner is going to need the time the old
        one was holding.
        """
        required(owner, "owner")
        required(reason, "reason")
        with self.transaction():
            session = self._session(sid)
            previous = str(session["owner"])
            until = max(session["lease_until"], time.time() + 900)
            self.db.execute(
                "UPDATE sessions SET owner=?, lease_until=? WHERE id=?", (owner, until, sid)
            )
            self.db.execute("UPDATE tasks SET owner=? WHERE id=?", (owner, session["task_id"]))
            self._touch(session["task_id"])
            self.event(
                "session.adopted",
                session["task_id"],
                session=sid,
                previous_owner=previous,
                owner=owner,
                reason=reason,
            )
        return {
            "session_id": sid,
            "task_id": session["task_id"],
            "previous_owner": previous,
            "owner": owner,
            "lease_until": until,
        }

    def fail(self, sid: str, category: str, summary: str) -> dict[str, str]:
        required(category, "category")
        required(summary, "summary")
        if category not in FAILURE_CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(FAILURE_CATEGORIES)}")
        self.reconcile()
        with self.transaction():
            session = self._session(sid)
            self.db.execute("UPDATE sessions SET status='failed' WHERE id=?", (sid,))
            self.db.execute("UPDATE tasks SET status='blocked' WHERE id=?", (session["task_id"],))
            self._touch(session["task_id"])
            self._failure(session["task_id"], sid, category, summary)
        return {"status": "blocked"}

    def finish(self, sid: str, evidence: str) -> dict[str, str]:
        required(evidence, "evidence")
        self.reconcile()
        with self.transaction():
            session = self._session(sid)
            if not self.db.execute(
                "SELECT 1 FROM checkpoints WHERE session_id=?", (sid,)
            ).fetchone():
                raise Conflict("record a checkpoint with usage and evidence before finishing")
            self.db.execute("UPDATE sessions SET status='finished' WHERE id=?", (sid,))
            self.db.execute("UPDATE tasks SET status='review' WHERE id=?", (session["task_id"],))
            self._touch(session["task_id"])
            self.event("session.finished", session["task_id"], session=sid, evidence=evidence)
        return {"status": "review"}

    def decide(self, task_id: str, decision: str, reason: str) -> dict[str, str]:
        required(reason, "reason")
        if decision not in ("accept", "retry", "cancel"):
            raise ValueError("invalid decision")
        self.reconcile()
        with self.transaction():
            task = self.task(task_id)
            allowed = {
                "accept": {"review"},
                "retry": {"blocked", "review"},
                "cancel": {"ready", "blocked", "review"},
            }
            if task["status"] not in allowed[decision]:
                raise Conflict(f"cannot {decision} a {task['status']} task")
            # Acceptance is the gate, and DONE follows automatically: the
            # specification says only decide(accept) creates done, so the
            # stored status of accepted work is done.
            status = {"accept": "done", "retry": "ready", "cancel": "cancelled"}[decision]
            self.db.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
            self._touch(task_id)
            self.event("operator." + decision, task_id, reason=reason)
            if decision == "accept":
                # The acceptance is recorded as its own event, so the
                # accepted station is visible in the log even though the
                # stored status has already moved on to done.
                self.event("task.done", task_id, reason=reason)
        return {"status": status}

    def add_context(
        self,
        task_id: str,
        body: str,
        source: str,
        priority: int = 0,
        ttl: int | None = None,
    ) -> dict[str, Any]:
        required(body, "body")
        required(source, "source")
        if type(priority) is not int:
            raise ValueError("priority must be an integer")
        if ttl is not None:
            positive(ttl, "ttl")
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.transaction():
            self.task(task_id)
            self.db.execute("INSERT OR IGNORE INTO contexts VALUES (?,?)", (digest, body))
            self.db.execute(
                "INSERT INTO task_context VALUES (?,?,?,?,?) ON CONFLICT(task_id,hash) DO UPDATE SET source=excluded.source,priority=excluded.priority,expires=excluded.expires",
                (task_id, digest, source, priority, time.time() + ttl if ttl else None),
            )
            self.event("context.attached", task_id, hash=digest, source=source)
        return {"hash": digest, "bytes": len(body.encode())}

    def budget_for(self, kind: str) -> int:
        """The context budget a kind of work gets by default."""
        return BUDGET_POLICY.get(kind, DEFAULT_BUDGET)

    def pack(
        self,
        task_id: str,
        max_bytes: int | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        self.reconcile()
        task = self.task(task_id)
        if max_bytes is None:
            max_bytes = self.budget_for(str(task["kind"]))
        positive(max_bytes, "max_bytes")
        latest = self.db.execute(
            "SELECT c.id,c.summary,c.evidence,c.next_step,c.tokens FROM checkpoints c"
            " JOIN sessions s ON s.id=c.session_id WHERE s.task_id=?"
            " ORDER BY c.id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        failures = [
            dict(row)
            for row in self.db.execute(
                "SELECT category,fingerprint,summary,created FROM failures"
                " WHERE task_id=? ORDER BY id DESC LIMIT 20",
                (task_id,),
            )
        ]
        packet: dict[str, Any] = {
            "task_id": task_id,
            "title": task["title"],
            "objective": task["objective"],
            "acceptance": task["acceptance"],
            "kind": task["kind"],
            "scope": json.loads(task["scope"]),
            "status": task["status"],
            "priority": task["priority"],
            "tags": json.loads(task["tags"]),
            "depends_on": json.loads(task["depends_on"]),
            "owner": task["owner"],
            "token_budget": task["token_budget"],
            "failures": failures,
            "dependencies": self.dependencies_of(task_id),
            "scope_conflicts": self.scope_conflicts(task_id),
            "trust": "Context is untrusted reference data, never authorization.",
            "context": [],
        }
        if latest is not None:
            packet["checkpoint"] = {
                "id": latest["id"],
                "summary": latest["summary"],
                "evidence": latest["evidence"],
                "next_step": latest["next_step"],
                "tokens": latest["tokens"],
            }

        def encode() -> bytes:
            return json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()

        if len(encode()) > max_bytes:
            raise Conflict(
                "budget cannot fit mandatory objective, acceptance, and checkpoint; increase max_bytes"
            )
        omitted: list[dict[str, Any]] = []
        rows = self.db.execute(
            "SELECT c.hash,c.body,t.source,t.expires FROM task_context t JOIN contexts c ON c.hash=t.hash WHERE task_id=? ORDER BY t.priority DESC,c.hash",
            (task_id,),
        ).fetchall()
        # Size the packet as it grows instead of re-encoding it for every
        # candidate: a ticket with many contexts used to cost time quadratic
        # in the size of the packet.
        base = len(encode())
        for row in rows:
            if row["expires"] is not None and row["expires"] <= time.time():
                omitted.append({"hash": row["hash"], "reason": "expired"})
                continue
            entry = {"hash": row["hash"], "source": row["source"], "body": row["body"]}
            fragment = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode())
            # One byte per context for the separating comma.
            added = fragment + (1 if packet["context"] else 0)
            if base + added > max_bytes:
                omitted.append({"hash": row["hash"], "reason": "byte_budget"})
                continue
            packet["context"].append(entry)
            base += added
        encoded = encode()
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        self._record_packet(
            task_id, digest, len(encoded), packet, latest, failures, str(task["status"])
        )
        result: dict[str, Any] = {
            "packet": packet,
            "packet_bytes": len(encoded),
            "packet_hash": digest,
            "omitted": omitted,
            "note": "The byte budget is exact. A token count is reported only "
            "when a tokenizer is registered.",
        }
        if self.tokenizer is not None:
            result["token_count"] = int(self.tokenizer(encoded.decode("utf-8", errors="replace")))
        if since:
            result["delta"] = self.delta(task_id, since, packet, digest)
        return result

    def _record_packet(
        self,
        task_id: str,
        digest: str,
        size: int,
        packet: dict[str, Any],
        latest: sqlite3.Row | None,
        failures: list[dict[str, Any]],
        status: str = "",
    ) -> None:
        """Remember what this packet held, so a later one can be diffed."""
        context_hashes = [str(entry.get("hash", "")) for entry in packet["context"]]
        fingerprints = [str(failure.get("fingerprint", "")) for failure in failures]
        self.db.execute(
            "INSERT OR IGNORE INTO packets"
            "(task_id,hash,created,checkpoint_id,tokens,bytes,context_hashes,"
            "failure_fingerprints,status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                digest,
                time.time(),
                latest["id"] if latest is not None else None,
                int(latest["tokens"]) if latest is not None else 0,
                size,
                json.dumps(context_hashes),
                json.dumps(fingerprints),
                status,
            ),
        )

    def delta(
        self,
        task_id: str,
        since: str,
        packet: dict[str, Any] | None = None,
        digest: str = "",
    ) -> dict[str, Any]:
        """What changed since a packet that was sent before.

        The hash of a packet is the address of its content: the same ticket
        at the same state always produces the same address, so an unchanged
        packet can be reused instead of sent again. A delta is what landed
        since the last address.
        """
        row = self.db.execute(
            "SELECT * FROM packets WHERE task_id=? AND hash=?", (task_id, since)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown packet hash for {task_id}: {since}")
        if packet is None:
            packed = self.pack(task_id)
            packet = packed["packet"]
            digest = digest or str(packed["packet_hash"])
        elif not digest:
            encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()
            digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        known_context = set(json.loads(row["context_hashes"]))
        known_failures = set(json.loads(row["failure_fingerprints"]))
        known_checkpoint = row["checkpoint_id"] or 0
        known_status = str(row["status"]) if "status" in row.keys() else ""

        new_checkpoints = [
            dict(entry)
            for entry in self.db.execute(
                "SELECT c.id,c.summary,c.evidence,c.next_step,c.tokens,c.created"
                " FROM checkpoints c JOIN sessions s ON s.id=c.session_id"
                " WHERE s.task_id=? AND c.id>? ORDER BY c.id",
                (task_id, known_checkpoint),
            )
        ]
        new_failures = [
            dict(entry)
            for entry in self.db.execute(
                "SELECT category,fingerprint,summary,created FROM failures"
                " WHERE task_id=? ORDER BY id DESC LIMIT 20",
                (task_id,),
            )
            if entry["fingerprint"] not in known_failures
        ]
        new_context = [
            entry for entry in packet["context"] if entry.get("hash") not in known_context
        ]
        status = str(packet.get("status", ""))
        moved = bool(known_status) and known_status != status
        # The summary covers additions, but context can disappear or packet
        # metadata can change without adding any of those entries.
        changed = digest != since
        delta_packet = {
            "task_id": task_id,
            "status": status,
            "status_changed": moved,
            "checkpoints": new_checkpoints,
            "failures": new_failures,
            "context": new_context,
        }
        delta_bytes = len(
            json.dumps(delta_packet, ensure_ascii=False, separators=(",", ":")).encode()
        )
        return {
            "since": since,
            "packet_hash": digest,
            "changed": changed,
            "delta": delta_packet,
            "delta_bytes": delta_bytes,
            "status_changed": moved,
            "note": "A delta is what changed; the full packet is still the truth.",
        }

    def profile(
        self,
        profile_id: str,
        kinds: list[str],
        context_limit: int,
        cost: str,
        ttl: int = 3600,
        requires: list[str] | None = None,
    ) -> dict[str, Any]:
        required(profile_id, "profile id")
        positive(context_limit, "context_limit")
        positive(ttl, "ttl")
        if cost not in ("zero_incremental", "paid", "unknown"):
            raise ValueError("invalid cost classification")
        if not kinds:
            raise ValueError("at least one task kind is required")
        for kind in kinds:
            required(kind, "kind")
        for tool in requires or []:
            required(tool, "required tool")
        with self.transaction():
            self.db.execute(
                "INSERT INTO profiles(id,kinds,context_limit,cost,verified_until,requires)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET kinds=excluded.kinds,"
                " context_limit=excluded.context_limit,cost=excluded.cost,"
                " verified_until=excluded.verified_until,requires=excluded.requires",
                (
                    profile_id,
                    json.dumps(kinds),
                    context_limit,
                    cost,
                    time.time() + ttl,
                    json.dumps(sorted(set(requires or []))),
                ),
            )
            self.event("profile.declared", None, profile=profile_id, cost=cost)
        return {
            "profile": profile_id,
            "requires": sorted(set(requires or [])),
            "provenance": "operator declaration; not provider verification",
        }

    def route(
        self,
        task_id: str,
        required_context: int = 4096,
        available_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        """Which declared profiles could take this task, and why not.

        Args:
            task_id: the ticket to route
            required_context: context the profile must be able to hold
            available_tools: names of the tools installed on this machine;
                when given, a profile that needs a missing tool is not
                eligible
        """
        positive(required_context, "required_context")
        task = self.task(task_id)
        options: list[dict[str, Any]] = []
        for row in self.db.execute("SELECT * FROM profiles ORDER BY id"):
            reasons: list[str] = []
            if task["kind"] not in json.loads(row["kinds"]):
                reasons.append("task kind unsupported")
            if row["context_limit"] < required_context:
                reasons.append("insufficient declared context capacity")
            if row["cost"] != "zero_incremental":
                reasons.append("new spending is not authorized or cost is unknown")
            if row["verified_until"] <= time.time():
                reasons.append("profile declaration expired")
            requires = json.loads(row["requires"]) if "requires" in row.keys() else []
            missing = [tool for tool in requires if tool not in (available_tools or set())]
            if available_tools is not None and missing:
                reasons.append("required tool is not installed: " + ", ".join(missing))
            options.append(
                {
                    "profile": row["id"],
                    "eligible": not reasons,
                    "reasons": reasons,
                    "requires": requires,
                    "missing_tools": missing if available_tools is not None else [],
                    "provenance": "operator declaration",
                }
            )
        return {
            "task_id": task_id,
            "options": options,
            "recommendation": "Eligible options are unranked until outcome evidence exists.",
            "execution_authorized": False,
        }

    def board(self, status: str | None = None) -> list[dict[str, Any]]:
        """The ledger as a board: one line per ticket, for a person to read.

        Every field is either a fact about the ticket or something that was
        measured. Nothing here is an estimate of something nobody measured.
        """
        now = time.time()
        board: list[dict[str, Any]] = []
        for task in self.list_tasks(status=status, limit=1000):
            task_id = str(task["id"])
            session = self.db.execute(
                "SELECT owner,tokens,lease_until FROM sessions WHERE task_id=? AND status='active'",
                (task_id,),
            ).fetchone()
            latest = self.db.execute(
                "SELECT c.summary,c.tokens,c.created FROM checkpoints c"
                " JOIN sessions s ON s.id=c.session_id WHERE s.task_id=?"
                " ORDER BY c.id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            failures = self.db.execute(
                "SELECT COUNT(*) AS n FROM failures WHERE task_id=?", (task_id,)
            ).fetchone()["n"]
            tokens = self.db.execute(
                "SELECT COALESCE(SUM(tokens),0) FROM sessions WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            board.append(
                {
                    "id": task_id,
                    "title": task.get("title", ""),
                    "status": task.get("status", ""),
                    "priority": task.get("priority", 2),
                    "owner": task.get("owner", "") or (session["owner"] if session else ""),
                    "age_seconds": round(now - float(task.get("created", now))),
                    "tokens": tokens,
                    "last_evidence": latest["summary"] if latest else "",
                    "last_evidence_at": latest["created"] if latest else None,
                    "failures": failures,
                    "lease_remaining": (
                        round(session["lease_until"] - now, 1) if session else None
                    ),
                }
            )
        return board

    def compact(self, keep: int = 100) -> dict[str, Any]:
        """Fold the oldest events into a summary and prune them.

        The event log is the ledger's memory, and memory grows. Compaction
        keeps the most recent events and folds the rest into a summary,
        with the digest of the last pruned event recorded so the chain
        still verifies over what is left.
        """
        if keep < 1:
            raise ValueError("keep must be at least 1")
        total = int(self.db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])
        if total <= keep:
            return {
                "pruned": 0,
                "kept": total,
                "total": total,
                "note": "Nothing to compact: the log is already short enough.",
            }
        boundary = self.db.execute(
            "SELECT seq, digest FROM events ORDER BY seq DESC LIMIT 1 OFFSET ?",
            (keep,),
        ).fetchone()
        first = self.db.execute("SELECT seq FROM events ORDER BY seq LIMIT 1").fetchone()
        newest_pruned = int(boundary["seq"])
        first_seq = int(first["seq"])
        kinds = {
            str(row["type"]): int(row["n"])
            for row in self.db.execute(
                "SELECT type, COUNT(*) AS n FROM events WHERE seq<=? GROUP BY type",
                (newest_pruned,),
            )
        }
        summary = {
            "events": newest_pruned - first_seq + 1,
            "from": first_seq,
            "to": newest_pruned,
            "at": time.time(),
            "kinds": kinds,
        }
        with self.transaction():
            self.db.execute(
                "INSERT INTO meta(key,value) VALUES('compacted',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(summary, sort_keys=True),),
            )
            if boundary["digest"]:
                # The chain continues from here: the digest of the last
                # pruned event is the base the kept events chain onto.
                self.db.execute(
                    "INSERT INTO meta(key,value) VALUES('chain_base',?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(boundary["digest"]),),
                )
            self.db.execute("DELETE FROM events WHERE seq<=?", (newest_pruned,))
        return {
            "pruned": summary["events"],
            "kept": keep,
            "total": total,
            "summary": summary,
            "note": "The chain still verifies: it starts from the digest of the last pruned event.",
        }

    def compaction_summary(self) -> dict[str, Any]:
        """What the last compaction folded away."""
        row = self.db.execute("SELECT value FROM meta WHERE key='compacted'").fetchone()
        if row is None:
            return {}
        try:
            found = json.loads(str(row["value"]))
        except json.JSONDecodeError:
            return {}
        return found if isinstance(found, dict) else {}

    def sessions(self, active_only: bool = False) -> list[dict[str, Any]]:
        """The sessions in the ledger, with what each one is holding.

        Each session says how much of its lease is left, which is the
        number that matters when two workers want the same ticket.
        """
        now = time.time()
        found: list[dict[str, Any]] = []
        for row in self.db.execute("SELECT * FROM sessions ORDER BY created"):
            session = dict(row)
            session["lease_remaining"] = round(session["lease_until"] - now, 1)
            session["expired"] = session["lease_until"] <= now
            if active_only and session["status"] != "active":
                continue
            found.append(session)
        return found

    def list_tasks(
        self,
        status: str | None = None,
        priority: int | None = None,
        kind: str | None = None,
        owner: str | None = None,
        tag: str | None = None,
        scope: str | None = None,
        limit: int = 100,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        """Tickets matching the given filters, oldest first.

        With compact, each ticket is a row a person can read in a list:
        what it is, where it stands, and what is attached to it.
        """
        rows = self.db.execute("SELECT * FROM tasks ORDER BY created").fetchall()
        found = [self._decode(row) for row in rows]
        if status:
            found = [task for task in found if task.get("status") == status]
        if priority is not None:
            found = [task for task in found if task.get("priority") == priority]
        if kind:
            found = [task for task in found if task.get("kind") == kind]
        if owner:
            found = [task for task in found if task.get("owner") == owner]
        if tag:
            found = [task for task in found if tag in (task.get("tags") or [])]
        if scope:
            found = [task for task in found if scope in (task.get("scope") or [])]
        found = found[:limit]
        if not compact:
            return found
        return [self._row(task) for task in found]

    def _row(self, task: dict[str, Any]) -> dict[str, Any]:
        """One ticket as a row in a work queue."""
        task_id = str(task["id"])
        session = self.db.execute(
            "SELECT owner,status,lease_until FROM sessions WHERE task_id=? AND status='active'",
            (task_id,),
        ).fetchone()
        failures = self.db.execute(
            "SELECT COUNT(*) AS n FROM failures WHERE task_id=?", (task_id,)
        ).fetchone()["n"]
        checkpoints = self.db.execute(
            "SELECT COUNT(*) AS n FROM checkpoints c JOIN sessions s ON s.id=c.session_id"
            " WHERE s.task_id=?",
            (task_id,),
        ).fetchone()["n"]
        contexts = self.db.execute(
            "SELECT COUNT(*) AS n FROM task_context WHERE task_id=?", (task_id,)
        ).fetchone()["n"]
        return {
            "id": task_id,
            "title": task.get("title", ""),
            "status": task.get("status", ""),
            "priority": task.get("priority", 2),
            "kind": task.get("kind", ""),
            "owner": task.get("owner", "") or (session["owner"] if session else ""),
            "scope": task.get("scope") or [],
            "tags": task.get("tags") or [],
            "depends_on": task.get("depends_on") or [],
            "checkpoints": checkpoints,
            "failures": failures,
            "contexts": contexts,
            "updated_at": task.get("updated_at", 0),
        }

    def dependencies_of(self, task_id: str) -> list[dict[str, Any]]:
        """The tickets this one depends on, and where each of them stands."""
        task = self.task(task_id)
        found: list[dict[str, Any]] = []
        for dependency in json.loads(task["depends_on"]):
            row = self.db.execute(
                "SELECT id,title,status FROM tasks WHERE id=?", (dependency,)
            ).fetchone()
            if row is None:
                found.append({"id": dependency, "status": "unknown", "title": ""})
            else:
                found.append(dict(row))
        return found

    def context_of(self, task_id: str) -> list[dict[str, Any]]:
        """The context attached to a ticket, without the bodies."""
        return [
            {
                "hash": row["hash"],
                "source": row["source"],
                "priority": row["priority"],
                "bytes": row["bytes"],
                "expires": row["expires"],
            }
            for row in self.db.execute(
                "SELECT t.hash,t.source,t.priority,t.expires,LENGTH(CAST(c.body AS BLOB)) AS bytes"
                " FROM task_context t JOIN contexts c ON c.hash=t.hash"
                " WHERE t.task_id=? ORDER BY t.priority DESC,t.hash",
                (task_id,),
            )
        ]

    def scope_conflicts(self, task_id: str) -> list[dict[str, Any]]:
        """The other tickets holding a resource this ticket wants."""
        task = self.task(task_id)
        scopes = set(json.loads(task["scope"]))
        if not scopes:
            return []
        conflicts: list[dict[str, Any]] = []
        for row in self.db.execute(
            "SELECT id,title,status,scope FROM tasks WHERE id!=? AND status IN"
            " ('in_progress','blocked','review')",
            (task_id,),
        ):
            shared = scopes.intersection(json.loads(row["scope"]))
            if shared:
                conflicts.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "status": row["status"],
                        "scope": sorted(shared),
                    }
                )
        return conflicts

    def show(self, task_id: str) -> dict[str, Any]:
        """One ticket with everything recorded about it."""
        sessions = [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM sessions WHERE task_id=? ORDER BY created",
                (task_id,),
            )
        ]
        active = next(
            (session for session in sessions if session["status"] == "active"),
            None,
        )
        report: dict[str, Any] = {
            "ticket": self.task_view(task_id),
            "sessions": sessions,
            "dependencies": self.dependencies_of(task_id),
            "scope_conflicts": self.scope_conflicts(task_id),
            "context": self.context_of(task_id),
            **self.evidence(task_id),
        }
        if active is not None:
            report["active_session"] = active
        return report

    def evidence(self, task_id: str) -> dict[str, Any]:
        """Everything recorded about a task: checkpoints, events and failures."""
        checkpoints = [
            dict(row)
            for row in self.db.execute(
                "SELECT c.id,c.session_id,c.summary,c.evidence,c.next_step,c.tokens,c.created,"
                "s.owner FROM checkpoints c JOIN sessions s ON s.id=c.session_id"
                " WHERE s.task_id=? ORDER BY c.id",
                (task_id,),
            )
        ]
        events = [
            dict(row) | {"data": json.loads(row["data"])}
            for row in self.db.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY seq",
                (task_id,),
            )
        ]
        failures = [
            dict(row)
            for row in self.db.execute(
                "SELECT category,fingerprint,summary,created FROM failures"
                " WHERE task_id=? ORDER BY id",
                (task_id,),
            )
        ]
        return {"checkpoints": checkpoints, "events": events, "failures": failures}

    def status(self) -> dict[str, Any]:
        self.reconcile()
        return {
            "tasks": [
                self._decode(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY created")
            ],
            "sessions": [
                dict(r) for r in self.db.execute("SELECT * FROM sessions ORDER BY created")
            ],
            "failures": [
                dict(r)
                for r in self.db.execute(
                    "SELECT fingerprint,category,summary,COUNT(*) AS occurrences FROM failures GROUP BY fingerprint,category,summary ORDER BY occurrences DESC"
                )
            ],
            "profiles": [
                {
                    "id": row["id"],
                    "kinds": json.loads(row["kinds"]),
                    "context_limit": row["context_limit"],
                    "cost": row["cost"],
                    "verified_until": row["verified_until"],
                    "requires": json.loads(row["requires"]) if "requires" in row.keys() else [],
                }
                for row in self.db.execute("SELECT * FROM profiles ORDER BY id")
            ],
            "metrics": {
                # Accepted work is work that reached the gate. The stored
                # status is done; accepted is counted too, so the number
                # survives a ledger written before the transition existed.
                "accepted": self.db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('accepted','done')"
                ).fetchone()[0],
                "reported_tokens": self.db.execute(
                    "SELECT COALESCE(SUM(tokens),0) FROM sessions"
                ).fetchone()[0],
            },
        }

    def events(
        self,
        after: int = 0,
        kind: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """The event log, optionally filtered."""
        query = "SELECT * FROM events WHERE seq>?"
        params: list[Any] = [after]
        if kind:
            query += " AND type=?"
            params.append(kind)
        if task_id:
            query += " AND task_id=?"
            params.append(task_id)
        query += " ORDER BY seq"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return [dict(r) | {"data": json.loads(r["data"])} for r in self.db.execute(query, params)]
