"""
How much context a worker actually spends on a ticket.

The first promise is *less repeated context*. A promise nobody measures is
a marketing claim, so this measures it: the bytes of the packet a worker
is handed, against the bytes it would spend reading the files the ticket
touches from scratch.

The comparison is deliberately conservative. It counts the files the
ticket's scope names and that actually exist; anything else is not
counted, because a number that flatters itself is worth nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["measure"]


def _files_in_scope(scope: list[str], root: Path) -> list[dict[str, Any]]:
    """The files the scope names that exist, with their sizes."""
    found: list[dict[str, Any]] = []
    for key in scope:
        path = root / key
        try:
            if path.is_file():
                found.append({"path": key, "bytes": path.stat().st_size})
        except OSError:
            continue
    return found


def measure(ledger: Any, task_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Measure the context cost of a ticket.

    Args:
        ledger: the Ledger holding the ticket
        task_id: the ticket to measure
        root: the directory the scope's paths are relative to
    """
    base = Path(root).expanduser()
    packed = ledger.pack(task_id)
    task = ledger.task_view(task_id)
    files = _files_in_scope(list(task.get("scope") or []), base)
    reread = sum(int(entry["bytes"]) for entry in files)
    packet_bytes = int(packed["packet_bytes"])
    ratio = round(packet_bytes / reread, 4) if reread else None
    return {
        "task_id": task_id,
        "packet_bytes": packet_bytes,
        "packet_hash": packed["packet_hash"],
        "reread_bytes": reread,
        "saving_bytes": reread - packet_bytes,
        "ratio": ratio,
        "files": files,
        "scoped_files_found": len(files),
        "scoped_files_total": len(task.get("scope") or []),
        "note": "The packet against the files the ticket names. Files that "
        "do not exist are not counted, so the comparison never flatters "
        "itself.",
    }
