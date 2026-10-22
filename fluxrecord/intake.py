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
from typing import Any

__all__ = ["intake", "ACTIONS", "actionable"]

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
    observations = sync.observations(repo, limit=limit_per_kind * 10)
    per_kind: dict[str, int] = {}
    created: list[dict[str, str]] = []
    attached: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for observation in observations:
        kind = observation["kind"]
        seen = per_kind.get(kind, 0)
        if seen >= limit_per_kind:
            continue
        per_kind[kind] = seen + 1

        action = observation["action"]
        status = observation.get("status", "")
        if not include_informational and not actionable(kind, status, action):
            skipped.append({"identity": observation["identity"], "reason": "no action implied"})
            continue

        key = f"gh:{repo}#{kind}#{observation['identity']}"
        evidence = _evidence(repo, observation)

        # An observation that names a local ticket belongs on that ticket.
        named = str(observation.get("ticket") or "")
        if named:
            try:
                ledger.task_view(named)
            except ValueError:
                named = ""
            else:
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
        task_id = ledger.next_ticket_id()
        try:
            ledger.add_task(
                task_id,
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
