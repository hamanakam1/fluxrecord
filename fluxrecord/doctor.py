"""
A report on the ledger, the project, the adapters and the environment.

Every module in the package knows something about the state of the work:
the ledger knows the tickets, the sync adapter knows what it observed,
the tooling inventory knows what is installed, the file layer knows what
was exported. This module asks all of them and puts the answers together,
so one command says what is going on and what to do next.

Nothing here raises for a missing piece. A part that is not there is
reported as not there.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

__all__ = ["diagnose"]

_ENVIRONMENT = ("FLUX_DB", "FLUX_GITHUB_DB", "NO_COLOR", "TERM", "COLORTERM")


def _ledger_report(db_path: str | Path) -> dict[str, Any]:
    from .core import Ledger

    report: dict[str, Any] = {"path": str(db_path)}
    ledger = None
    path = Path(db_path).expanduser()
    if not path.exists():
        # A diagnostic must not create the thing it diagnoses.
        report["readable"] = False
        report["exists"] = False
        report["error"] = "there is no ledger at this path yet"
        return report
    report["exists"] = True
    try:
        from .secrets import event_chain_key

        ledger = Ledger(path, chain_key=event_chain_key(), read_only=True)
    except Exception as exc:  # noqa: BLE001 - a report must not fail
        report["readable"] = False
        report["error"] = str(exc)
        return report
    try:
        # All counts and chain checks describe one committed database state.
        ledger.db.execute("BEGIN")
        report["schema"] = ledger.schema_version()
        status = ledger.status()
        chain = ledger.verify_chain()
        tasks: dict[str, int] = {}
        for task in status["tasks"]:
            tasks[task["status"]] = tasks.get(task["status"], 0) + 1
        sessions: dict[str, int] = {}
        for session in status["sessions"]:
            sessions[session["status"]] = sessions.get(session["status"], 0) + 1
        report.update(
            {
                "readable": True,
                "tasks": tasks,
                "task_count": len(status["tasks"]),
                "sessions": sessions,
                "session_count": len(status["sessions"]),
                "failure_count": len(status["failures"]),
                "profiles": status.get("profiles", []),
                "chain": {
                    "signed": chain["signed"],
                    "ok": chain["ok"],
                    "broken_at": chain["broken_at"],
                },
                "metrics": status["metrics"],
                "bytes": path.stat().st_size,
            }
        )
    except Exception as exc:  # noqa: BLE001 - a report must not fail
        report["readable"] = False
        report["error"] = str(exc)
    finally:
        if ledger is not None:
            ledger.close()
    return report


def _project_report(project_dir: str | Path) -> dict[str, Any]:
    root = Path(project_dir).expanduser()
    report: dict[str, Any] = {"path": str(root), "exists": root.is_dir()}
    if not root.is_dir():
        return report
    tickets = root / "tickets"
    index = root / "index.json"
    files = sorted(tickets.glob("*.md")) if tickets.is_dir() else []
    report["ticket_files"] = len(files)
    report["evidence_dirs"] = (
        len(list((root / "evidence").glob("*"))) if (root / "evidence").is_dir() else 0
    )
    if index.is_file():
        try:
            entries = json.loads(index.read_text())
            report["index_entries"] = len(entries)
            report["index_updated"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(index.stat().st_mtime)
            )
        except (OSError, json.JSONDecodeError) as exc:
            report["index_error"] = str(exc)
    return report


def _github_report(sync_db: str | Path) -> dict[str, Any]:
    report: dict[str, Any] = {"sync_db": str(sync_db)}
    gh = shutil.which("gh")
    report["gh_available"] = gh is not None
    report["gh_path"] = gh
    path = Path(sync_db).expanduser()
    report["sync_db_exists"] = path.exists()
    if not path.exists():
        return report
    database = None
    try:
        database = sqlite3.connect(
            path.absolute().as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None
        )
        database.row_factory = sqlite3.Row
        database.execute("BEGIN")
        repos: dict[str, int] = {}
        for row in database.execute("SELECT repo, COUNT(*) AS n FROM observations GROUP BY repo"):
            repos[row["repo"]] = row["n"]
        outbox: dict[str, int] = {}
        for row in database.execute("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state"):
            outbox[row["state"]] = row["n"]
        report["observations"] = repos
        report["observation_count"] = sum(repos.values())
        report["outbox"] = outbox
    except Exception as exc:  # noqa: BLE001 - a report must not fail
        report["error"] = str(exc)
    finally:
        if database is not None:
            database.close()
    return report


def _tools_report() -> dict[str, Any]:
    from .tools import MAX_ENTRIES, inventory, scan_path

    try:
        found = inventory(limit=1)
        # The presence check needs the whole of PATH, not the first page of it.
        everything = scan_path(limit=MAX_ENTRIES)
    except Exception as exc:  # noqa: BLE001 - a report must not fail
        return {"error": str(exc)}
    names = {tool["name"] for tool in everything}
    present = {
        name: (name in names or shutil.which(name) is not None)
        for name in ("git", "gh", "python3", "pytest", "ruff", "mypy")
    }
    return {
        "count": found["count"],
        "capped": found.get("scan_capped", False),
        "present": present,
        "paths": {name: shutil.which(name) for name in present},
        "note": found["note"],
    }


def _dashboard_report(url: str | None) -> dict[str, Any]:
    report: dict[str, Any] = {"url": url}
    if not url:
        report["probed"] = False
        return report
    from .dashboard import preview

    try:
        previewed = preview(url, "planned", 0)
    except (OSError, ValueError) as exc:
        report["probed"] = True
        report["reachable"] = False
        report["error"] = str(exc)
        return report
    report["probed"] = True
    report["reachable"] = True
    report["status_code"] = previewed["status_code"]
    report["tickets"] = previewed["count"]
    report["truncated"] = previewed["truncated"]
    return report


def diagnose(
    db_path: str | Path,
    project_dir: str | Path = ".fluxrecord",
    sync_db: str | Path | None = None,
    dashboard_url: str | None = None,
) -> dict[str, Any]:
    """Report on the ledger, the project, the adapters and the environment."""
    notes: list[str] = []
    ledger = _ledger_report(db_path)
    project = _project_report(project_dir)
    github = _github_report(sync_db) if sync_db else {"sync_db": None, "gh_available": False}
    tools = _tools_report()
    dashboard = _dashboard_report(dashboard_url)

    if not ledger.get("readable"):
        if ledger.get("exists"):
            notes.append(
                "the ledger could not be read; inspect the reported error before changing it"
            )
        else:
            notes.append("the ledger could not be read; run `flux init` to create one")
    else:
        chain = ledger.get("chain", {})
        if chain.get("signed") and not chain.get("ok"):
            notes.append(
                "the event chain does not verify; the log has been changed "
                f"since it was written (at event {chain.get('broken_at')})"
            )
        elif not chain.get("signed"):
            notes.append(
                "the ledger is not signed; set FLUX_EVENT_KEY to make the event log tamper-evident"
            )
        tasks = ledger.get("tasks", {})
        if tasks.get("blocked"):
            notes.append(
                f"{tasks['blocked']} task(s) are blocked; run `flux reconcile` or decide on them"
            )
        if tasks.get("review"):
            notes.append(f"{tasks['review']} task(s) are waiting for review")
        if ledger.get("failure_count"):
            notes.append(f"{ledger['failure_count']} failure(s) are recorded")
        for profile in ledger.get("profiles", []):
            if profile.get("verified_until", 0) <= time.time():
                notes.append(f"profile {profile['id']} has expired; declare it again")

    if not project.get("exists"):
        notes.append("no project directory; run `flux init --project .` to create one")
    else:
        tickets = project.get("ticket_files", 0)
        entries = project.get("index_entries")
        if entries is not None and entries != tickets:
            notes.append(
                f"the project has {tickets} ticket files and {entries} index entries; "
                "run `flux export` to bring them back in step"
            )
        count = ledger.get("task_count")
        if count is not None and count != tickets:
            notes.append(
                f"the ledger holds {count} ticket(s) and the project holds {tickets}; "
                "run `flux export` to bring the files back in step"
            )

    if github.get("gh_available") is False:
        notes.append("the gh CLI is not installed; GitHub sync cannot run")
    if github.get("observation_count"):
        notes.append(
            f"{github['observation_count']} observation(s) are recorded; "
            "run `flux github-intake` to turn them into tickets"
        )

    if dashboard.get("probed") and dashboard.get("reachable") is False:
        notes.append("the local dashboard did not answer")

    from .wire import WIRE_FORMAT

    return {
        "wire_format": WIRE_FORMAT,
        "ledger": ledger,
        "project": project,
        "github": github,
        "tools": tools,
        "dashboard": dashboard,
        "environment": {name: os.environ.get(name, "") for name in _ENVIRONMENT},
        "notes": notes,
        "note": "A report about the machine. It is not authority to act.",
    }
