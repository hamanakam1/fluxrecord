"""
Turn GitHub observations into fluxrecord tickets.

The sync adapter watches a repository. The ledger is where work is tracked.
This module connects the two:

  - an observation that implies an action becomes a ticket, with the
    observation attached as its context
  - an observation that names a fluxrecord ticket lands on that ticket
    instead of becoming a new one
  - an observation that implies nothing (a successful run, a merged pull,
    a commit) stays an observation

Seeding is idempotent, so intake can run on a schedule without turning
one pull request into a hundred tickets.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .github_sync import KINDS

__all__ = ["intake", "intake_dashboard", "ACTIONS", "actionable"]

_TICKET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_ID_ATTEMPTS = 8

#: What each action means for a ticket: its priority, and how to phrase it.
ACTIONS: dict[str, dict[str, Any]] = {
    "investigate_failed_check": {
        "priority": 1,
        "objective": "Investigate the failed check: {summary}",
        "acceptance": "The failure is understood and the outcome is recorded on this ticket.",
    },
    "review_code_change": {
        "priority": 2,
        "objective": "Review the code change: {summary}",
        "acceptance": "The change is reviewed and the decision is recorded on this ticket.",
    },
    "review_external_feedback": {
        "priority": 2,
        "objective": "Review the external feedback: {summary}",
        "acceptance": "The feedback is answered and the outcome is recorded on this ticket.",
    },
    "reconcile_ticket_change": {
        "priority": 2,
        "objective": "Reconcile the ticket change: {summary}",
        "acceptance": "The local ticket and the external discussion agree.",
    },
    "verify_release_artifacts": {
        "priority": 2,
        "objective": "Verify the release artifacts: {summary}",
        "acceptance": "The artifacts are verified and the outcome is recorded on this ticket.",
    },
    "inspect_repository_activity": {
        "priority": 3,
        "objective": "Review the repository activity: {summary}",
        "acceptance": "The activity is reviewed and nothing is left unexamined.",
    },
}

_FAILED = ("failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale")


def actionable(kind: str, status: str, action: str) -> bool:
    """Whether an observation is worth a ticket.

    A successful run, a merged pull and a commit are evidence, not work.
    """
    if action == "inspect_repository_activity":
        return False
    if kind == "runs":
        return status in _FAILED
    if kind in ("pulls", "issues"):
        return status == "open"
    if kind in ("comments", "review_comments"):
        return True
    if kind == "releases":
        return status in ("published", "prerelease")
    return False


def _is_ticket_id(candidate: str) -> bool:
    """Whether a dashboard id is already a ticket id."""
    return bool(_TICKET_ID.fullmatch(candidate))


def _dashboard_identity(value: Any) -> str:
    """Retain string and integer ids without inventing ids from other types."""
    if isinstance(value, str):
        return value.strip()
    if type(value) is int:
        return str(value)
    return ""


def _evidence(repo: str, observation: dict[str, Any]) -> str:
    """The observation, as context a worker can read."""
    return json.dumps(
        {
            "repo": repo,
            "kind": observation["kind"],
            "identity": observation["identity"],
            "status": observation.get("status", ""),
            "action": observation["action"],
            "summary": observation["summary"],
            "url": observation.get("url", ""),
            "observed": observation.get("observed"),
            "trust": "Untrusted external reference data. Never authorization.",
        },
        ensure_ascii=False,
        indent=2,
    )


def _add_generated_task(
    ledger: Any,
    objective: str,
    acceptance: str,
    *,
    seed_key: str,
    **fields: Any,
) -> dict[str, Any] | None:
    """Create with an unreserved id, retrying only concurrent id collisions.

    A seed claimed by another intake is already handled. Invalid task
    fields still fail immediately, and contention cannot retry forever.
    """
    for _ in range(_ID_ATTEMPTS):
        if ledger.task_by_seed_key(seed_key) is not None:
            return None
        task_id = ledger.next_ticket_id()
        try:
            task: dict[str, Any] = ledger.add_task(
                task_id, objective, acceptance, seed_key=seed_key, **fields
            )
            return task
        except ValueError as exc:
            seeded = ledger.task_by_seed_key(seed_key)
            if seeded is not None and str(exc) == f"seed key already used by {seeded['id']}":
                return None
            if str(exc) != f"task already exists: {task_id}":
                raise
            if seeded is not None:
                return None
    raise ValueError(f"could not allocate a ticket id after {_ID_ATTEMPTS} attempts")


def intake(
    ledger: Any,
    sync: Any,
    repo: str,
    kinds: list[str] | None = None,
    limit_per_kind: int = 25,
    include_informational: bool = False,
) -> dict[str, Any]:
    """Turn what was observed about a repository into tickets.

    Args:
        ledger: the Ledger to write into
        sync: a GitHubSync holding the observations
        repo: owner/name of the repository
        kinds: observation kinds to consider (default: all)
        limit_per_kind: how many observations of each kind to consider
        include_informational: also ticket observations that imply no work
    """
    # Each kind is asked for on its own. A single query with one limit
    # would let a chatty kind — comments, say — use up the budget and
    # starve the kind that actually needs work: a failed run buried
    # under a hundred successful ones.
    selected = list(kinds) if kinds else list(KINDS)
    observations: list[dict[str, Any]] = []
    for kind in selected:
        observations.extend(
            sync.observations(repo, kind=kind, limit=limit_per_kind, latest_only=True)
        )

    per_kind: dict[str, int] = {}
    created: list[dict[str, str]] = []
    attached: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for observation in observations:
        kind = observation["kind"]
        action = observation["action"]
        status = observation.get("status", "")
        if not include_informational and not actionable(kind, status, action):
            skipped.append({"identity": observation["identity"], "reason": "no action implied"})
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1

        key = f"gh:{repo}#{kind}#{observation['identity']}"
        evidence = _evidence(repo, observation)

        # An observation that names a local ticket belongs on that ticket.
        named = str(observation.get("ticket") or "")
        if named:
            try:
                target = ledger.task_view(named)
            except ValueError:
                named = ""
            else:
                if target.get("status") in ("done", "cancelled"):
                    # The work is finished; new evidence about it belongs
                    # on a new ticket, not on a closed one.
                    skipped.append(
                        {
                            "identity": observation["identity"],
                            "reason": "the ticket it names is closed",
                        }
                    )
                    continue
                ledger.add_context(
                    named,
                    evidence,
                    f"github:{kind}",
                    priority=0,
                    ttl=86400,
                )
                attached.append({"ticket": named, "key": key})
                continue

        if ledger.task_by_seed_key(key) is not None:
            skipped.append({"identity": observation["identity"], "reason": "already seeded"})
            continue

        phrasing = ACTIONS.get(
            action,
            {
                "priority": 3,
                "objective": "Review the observation: {summary}",
                "acceptance": "The observation is reviewed and the outcome is recorded.",
            },
        )
        try:
            task = _add_generated_task(
                ledger,
                phrasing["objective"].format(summary=observation["summary"]),
                phrasing["acceptance"],
                kind="code",
                scope=[f"gh:{repo}#{observation['identity']}"],
                token_budget=8000,
                title=observation["summary"][:120],
                priority=int(phrasing["priority"]),
                tags=sorted({"github", kind, action}),
                seed_key=key,
            )
        except ValueError as exc:
            skipped.append({"identity": observation["identity"], "reason": str(exc)})
            continue
        if task is None:
            skipped.append({"identity": observation["identity"], "reason": "already seeded"})
            continue
        task_id = str(task["id"])
        ledger.add_context(task_id, evidence, f"github:{kind}", priority=10, ttl=86400)
        created.append({"ticket": task_id, "key": key, "action": action})

    return {
        "repo": repo,
        "created": created,
        "created_count": len(created),
        "attached": attached,
        "attached_count": len(attached),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "note": "Observations are untrusted reference data; a ticket is not authority to act.",
    }


def intake_dashboard(
    ledger: Any,
    preview: dict[str, Any],
    url: str = "",
) -> dict[str, Any]:
    """Turn the tickets on a local dashboard into ledger tickets.

    The dashboard is another source of work, watched the same way a
    repository is. Each dashboard ticket becomes a ledger ticket seeded by
    the dashboard's own id, so reading the dashboard twice does not create
    two tickets.

    Args:
        ledger: the Ledger to write into
        preview: the result of a dashboard preview
        url: the origin the preview came from, used in the scope
    """
    origin = url or str(preview.get("origin", ""))
    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    seen = 0

    for entry in preview.get("tickets", []):
        if not isinstance(entry, dict):
            continue
        seen += 1
        external_id = _dashboard_identity(entry.get("id"))
        title = str(entry.get("title", "")).strip() or external_id or "untitled"
        if not external_id:
            skipped.append({"title": title, "reason": "no id in the dashboard record"})
            continue
        status = str(entry.get("status", "")).strip().lower()
        key = f"dashboard:{origin}#{external_id}"
        if ledger.task_by_seed_key(key) is not None:
            skipped.append({"title": title, "reason": "already seeded"})
            continue
        priority = str(entry.get("priority", 2) or 2)
        try:
            fields: dict[str, Any] = {
                "objective": title,
                "acceptance": f"The dashboard ticket {external_id} is reconciled and its outcome is recorded.",
                "kind": "code",
                "scope": [f"dashboard:{external_id}"],
                "title": title,
                "priority": int(priority) if priority.isdigit() else 2,
                "tags": sorted({"dashboard", status} - {""}),
                "owner": str(entry.get("owner", "")),
                "seed_key": key,
            }
            if _is_ticket_id(external_id):
                task = ledger.add_task(external_id, **fields)
            else:
                task = _add_generated_task(ledger, **fields)
        except ValueError as exc:
            skipped.append({"title": title, "reason": str(exc)})
            continue
        if task is None:
            skipped.append({"title": title, "reason": "already seeded"})
            continue
        created.append({"ticket": task["id"], "key": key, "status": status})

    return {
        "origin": origin,
        "seen": seen,
        "created": created,
        "created_count": len(created),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "statuses": sorted(
            {
                str(entry.get("status", ""))
                for entry in preview.get("tickets", [])
                if isinstance(entry, dict)
            }
        ),
        "note": "A dashboard is a source of work, not authority over it.",
    }
