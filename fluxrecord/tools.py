"""
Local executable inventory.

Answers one question: what can a worker on this machine actually call?

Only the filesystem is inspected. Nothing is executed, no network call is
made, and no telemetry leaves the machine. Names are read from PATH, so the
result is evidence about the machine, not a claim about any one tool.
"""

from __future__ import annotations

import os
import shutil
import stat
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = ["available", "inventory", "scan_path", "MAX_ENTRIES"]

MAX_ENTRIES = 500


def _path_entries() -> list[str]:
    raw = os.environ.get("PATH", "")
    return [entry for entry in raw.split(os.pathsep) if entry]


def scan_path(limit: int = MAX_ENTRIES, metadata: bool = False) -> list[dict[str, Any]]:
    """List executables reachable through PATH, first occurrence wins.

    Args:
        limit: maximum number of entries to return
        metadata: include size and modification time for each entry
    """
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for directory in _path_entries():
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(found) >= limit:
                        return found
                    if entry.name in seen:
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=True):
                            continue
                        info = entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    if not info.st_mode & stat.S_IXUSR:
                        continue
                    seen.add(entry.name)
                    record: dict[str, Any] = {
                        "name": entry.name,
                        "path": str(Path(directory) / entry.name),
                    }
                    if metadata:
                        record["bytes"] = info.st_size
                        record["modified"] = info.st_mtime
                    found.append(record)
        except OSError:
            continue
    return found


def available(names: Iterable[str]) -> dict[str, str | None]:
    """Where each named tool is, or None where it is not.

    The filesystem is searched; nothing is executed.
    """
    return {name: shutil.which(name) for name in names}


def inventory(history: bool = False, limit: int = 50) -> dict[str, Any]:
    """Inventory the local executables available to a worker.

    Args:
        history: include size and modification time, newest first
        limit: maximum number of entries to report
    """
    found = scan_path(metadata=history)
    if history:
        found.sort(key=lambda item: item.get("modified", 0.0), reverse=True)
    reported = found[:limit]
    result: dict[str, Any] = {
        "tools": reported,
        "count": len(found),
        "returned": len(reported),
        "truncated": len(found) > len(reported),
        "scan_capped": len(found) >= MAX_ENTRIES,
        "search_path": _path_entries(),
        "note": "Filesystem inventory only; no executable was run.",
    }
    if history:
        newest = reported[0]["modified"] if reported else 0.0
        result["history"] = {
            "newest_modified": newest,
            "newest_modified_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest)) if newest else None
            ),
            "ordered": "most recently modified first",
        }
    return result
