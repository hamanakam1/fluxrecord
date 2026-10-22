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
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

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
    seen: dict[str, tuple[str, str, str]] = {}
    used = {str(row.get("ID", "")).strip() for row in rows}

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
        token_budget = int(budget) if budget.isdigit() and int(budget) > 0 else 8000

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

        fingerprint = (objective, acceptance)
        if task_id in seen:
            if seen[task_id][:2] == fingerprint:
                duplicates.append(task_id)
                continue
            if not reassign:
                conflicts.append(
                    {
                        "id": task_id,
                        "title": payload["title"],
                        "reason": "this id is already used by a different ticket in the same file",
                        "held": seen[task_id][2],
                    }
                )
                continue
            fresh = _next_id(used)
            used.add(fresh)
            reassigned.append({"from": task_id, "to": fresh, "title": payload["title"]})
            task_id = fresh
            payload["task_id"] = fresh
            payload["seed_key"] = f"csv:{fresh}"

        if dry_run:
            imported.append(task_id)
            seen[task_id] = (objective, acceptance, payload["title"])
            continue
        try:
            existing = ledger.task_view(task_id)
        except ValueError:
            existing = None
        if existing is not None:
            unchanged.append(task_id)
            seen[task_id] = (objective, acceptance, payload["title"])
            continue
        try:
            ledger.add_task(**payload)
        except ValueError as exc:
            skipped.append({"id": task_id, "reason": str(exc)})
            continue
        imported.append(task_id)
        seen[task_id] = (objective, acceptance, payload["title"])

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
