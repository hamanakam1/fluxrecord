"""
Import a CSV backlog into the ledger.

The repository shipped its backlog as ``tickets.csv`` long before the ledger
existed. This maps that shape onto the ticket model:

    File              -> scope (a resource key, so conflicts are detectable)
    Epic, Category    -> tags
    Priority P0..P3   -> priority 1..3, with the original label kept as a tag
    Dependencies      -> depends_on
    ID                -> id, and doubles as the seed key

Seeding is idempotent, so an import can be repeated: tickets already seeded
are left alone and reported as unchanged.

Ordinary imports retain ``csv:<ID>`` seed keys. Newly reassigned rows use
``csv:reassigned:<digest>`` keys derived from the original ID, objective and
acceptance, so repeating the import finds the same reassignment. Existing
keys are never changed. Older reassigned tickets only record their new ID;
their original mapping cannot be recovered reliably and is not guessed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .core import validate_task

__all__ = ["import_csv", "read_rows"]

_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "ticket", "ticket_id", "key"),
    "title": ("title", "name", "summary"),
    "objective": ("description", "objective", "detail"),
    "acceptance": ("acceptance", "acceptancecriteria", "acceptance_criteria", "done_when"),
    "priority": ("priority", "prio", "p"),
    "tags": ("tags", "labels"),
    "epic": ("epic", "theme"),
    "category": ("category", "area", "type"),
    "depends_on": ("dependencies", "dependson", "depends_on", "blocked_by"),
    "blocks": ("blocks", "blocking"),
    "scope": ("file", "files", "scope", "resource"),
    "owner": ("owner", "assignee"),
    "kind": ("kind", "type"),
    "token_budget": ("tokenbudget", "token_budget", "budget"),
}

_PRIORITY = {"P0": 1, "P1": 1, "P2": 2, "P3": 3}

#: Only a dependency that names a ticket can be one. The column in a CSV
#: backlog usually mixes ids with prose, and prose is not a dependency.
_TICKET_ID = re.compile(r"M-\d{6}")


def _pick(row: dict[str, str], field: str) -> str:
    lookup = {key.strip().lower().replace(" ", ""): value for key, value in row.items() if key}
    for alias in _ALIASES[field]:
        value = lookup.get(alias.replace(" ", ""))
        if value:
            return value.strip()
    return ""


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def read_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a CSV backlog into a list of string rows."""
    with Path(path).expanduser().open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _next_id(used: set[str]) -> str:
    """The next free sequential ticket id."""
    highest = 0
    for candidate in used:
        digits = candidate[2:] if candidate.startswith("M-") else candidate
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"M-{highest + 1:06d}"


def _reassignment_seed(task_id: str, objective: str, acceptance: str) -> str:
    identity = (task_id, objective, acceptance)
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return f"csv:reassigned:{digest}"


def import_csv(
    ledger: Any,
    path: str | Path,
    kind: str = "code",
    dry_run: bool = False,
    reassign: bool = False,
) -> dict[str, Any]:
    """Import a CSV backlog into the ledger.

    Args:
        ledger: the Ledger to write into
        path: the CSV file to read
        kind: task kind for imported tickets that do not name one
        dry_run: report what would be imported without writing
        reassign: give a fresh id to rows whose id is already used by a
            different ticket, instead of refusing them
    """
    rows = read_rows(path)
    imported: list[str] = []
    unchanged: list[str] = []
    skipped: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    duplicates: list[str] = []
    reassigned: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    # Read the full identity inventory without status(), which reconciles
    # expired sessions and would make a dry run mutate the ledger.
    tasks = {
        str(task["id"]): dict(task)
        for task in ledger.db.execute("SELECT id,objective,acceptance,title,seed_key FROM tasks")
    }
    seeded = {task["seed_key"]: task for task in tasks.values() if task["seed_key"]}
    source_tasks = dict(tasks)
    prepared: list[dict[str, Any]] = []

    for row in rows:
        task_id = _pick(row, "id")
        if not task_id:
            skipped.append({"row": str(row)[:120], "reason": "no id"})
            continue
        objective = _pick(row, "objective") or _pick(row, "title")
        acceptance = _pick(row, "acceptance")
        if not objective or not acceptance:
            skipped.append({"id": task_id, "reason": "missing objective or acceptance"})
            continue

        scope = _split(_pick(row, "scope")) or [task_id]
        tags = _split(_pick(row, "tags"))
        for extra in ("epic", "category"):
            value = _pick(row, extra)
            if value:
                tags.extend(_split(value))
        label = _pick(row, "priority")
        priority = _PRIORITY.get(label.upper())
        if priority is None:
            priority = 2
        if label:
            tags.append(label.lower())
        budget = _pick(row, "token_budget")
        token_budget = 8000
        if budget.isdigit():
            try:
                parsed_budget = int(budget)
            except ValueError:
                skipped.append({"id": task_id, "reason": "token_budget must be a positive integer"})
                continue
            if parsed_budget > 0:
                token_budget = parsed_budget

        payload: dict[str, Any] = {
            "task_id": task_id,
            "objective": objective,
            "acceptance": acceptance,
            "kind": _pick(row, "kind") or kind,
            "scope": scope,
            "token_budget": token_budget,
            "title": _pick(row, "title") or objective,
            "priority": priority,
            "tags": sorted(set(tags)),
            "depends_on": [
                item for item in _split(_pick(row, "depends_on")) if _TICKET_ID.fullmatch(item)
            ],
            "blocks": _split(_pick(row, "blocks")),
            "owner": _pick(row, "owner"),
            "seed_key": f"csv:{task_id}",
        }
        source_task = source_tasks.get(task_id)
        if source_task is not None:
            # Existing IDs can be longer than a normal CSV seed permits.
            # Validate the seed the operation will actually use instead.
            payload["seed_key"] = source_task["seed_key"]
            if reassign and (source_task["objective"], source_task["acceptance"]) != (
                objective,
                acceptance,
            ):
                payload["seed_key"] = _reassignment_seed(task_id, objective, acceptance)
        try:
            validate_task(**payload)
        except ValueError as exc:
            skipped.append({"id": task_id, "reason": str(exc)})
            continue
        seed_owner = seeded.get(payload["seed_key"])
        if source_task is None and seed_owner is not None:
            skipped.append(
                {"id": task_id, "reason": f"seed key already used by {seed_owner['id']}"}
            )
            continue
        prepared.append(payload)
        source_tasks.setdefault(task_id, payload)

    # An invalid row cannot claim an ID, count as a duplicate, or move the
    # next reassignment. Both preview and execution use these same rows.
    used = set(tasks) | {payload["task_id"] for payload in prepared}
    for payload in prepared:
        task_id = payload["task_id"]
        objective = payload["objective"]
        acceptance = payload["acceptance"]
        fingerprint = (objective, acceptance)
        identity = (task_id, objective, acceptance)
        if identity in seen:
            duplicates.append(task_id)
            continue
        existing = tasks.get(task_id)
        if existing is not None:
            if (existing["objective"], existing["acceptance"]) == fingerprint:
                unchanged.append(task_id)
                seen.add(identity)
                continue
            if not reassign:
                conflicts.append(
                    {
                        "id": task_id,
                        "title": payload["title"],
                        "reason": "this id is already used by a different ticket",
                        "held": existing["title"],
                    }
                )
                continue
            payload["seed_key"] = _reassignment_seed(*identity)
            previous = seeded.get(payload["seed_key"])
            if (
                previous is not None
                and (previous["objective"], previous["acceptance"]) != fingerprint
            ):
                conflicts.append(
                    {
                        "id": task_id,
                        "title": payload["title"],
                        "reason": "the reassignment seed is already used by a different ticket",
                        "held": previous["title"],
                    }
                )
                continue
            fresh = str(previous["id"]) if previous is not None else _next_id(used)
            payload = payload | {"task_id": fresh}
            try:
                validate_task(**payload)
            except ValueError as exc:
                skipped.append({"id": task_id, "reason": str(exc)})
                continue
            reassigned.append({"from": task_id, "to": fresh, "title": payload["title"]})
            if previous is not None:
                unchanged.append(fresh)
                seen.add(identity)
                continue
            used.add(fresh)
            task_id = fresh

        if not dry_run:
            try:
                ledger.add_task(**payload)
            except ValueError as exc:
                skipped.append({"id": task_id, "reason": str(exc)})
                continue
        imported.append(task_id)
        seen.add(identity)
        tasks[task_id] = payload | {"id": task_id}
        seeded[payload["seed_key"]] = tasks[task_id]

    return {
        "source": str(path),
        "rows": len(rows),
        "imported": imported,
        "imported_count": len(imported),
        "unchanged": unchanged,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "reassigned": reassigned,
        "skipped": skipped,
        "dry_run": dry_run,
        "note": "Imported tickets carry a csv: seed key, so the import can be repeated.",
    }
