"""
Watch the ledger.

The roadmap asked for WebSocket updates. A socket means a server, a port,
an origin check and a deployment story — all of which contradict a
local-first CLI whose core promises no network.

The event log is already a table in the same file the reader has open, so
watching it needs nothing new: no socket, no server, no new boundary, and
the same "the board changed" value.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import Any

__all__ = ["watch", "format_event"]


def format_event(event: dict[str, Any]) -> str:
    """One event as a line a person can read."""
    stamp = time.strftime("%H:%M:%S", time.localtime(float(event.get("created", 0))))
    task = event.get("task_id") or "-"
    data = event.get("data") or {}
    detail = ""
    for field in ("owner", "reason", "summary", "evidence", "previous_owner"):
        if field in data:
            detail = f"{field}={data[field]}"
            break
    line = f"{stamp}  {event.get('seq', '?'):>5}  {task:<10}  {event.get('type', '')}"
    return f"{line}  {detail}".rstrip()


def watch(
    ledger: Any,
    after: int = 0,
    interval: float = 1.0,
    limit: int | None = None,
    once: bool = False,
    emit: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Print new events as they land.

    Args:
        ledger: the ledger to watch
        after: the sequence to start after
        interval: how long to wait between polls
        limit: stop after this many events
        once: print what is there and stop
        emit: where lines go; stdout by default
        sleep: how to wait; time.sleep by default

    Returns:
        the sequence the watch stopped at
    """
    write = emit if emit is not None else (lambda line: print(line))
    cursor = after
    seen = 0
    while True:
        for event in ledger.events(after=cursor):
            cursor = int(event["seq"])
            seen += 1
            write(format_event(event))
            if limit is not None and seen >= limit:
                return cursor
        if once:
            return cursor
        sleep(interval)


def watch_json(
    ledger: Any,
    after: int = 0,
    interval: float = 1.0,
    limit: int | None = None,
    once: bool = False,
    emit: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Watch, printing each event as JSON."""
    write = emit if emit is not None else (lambda line: sys.stdout.write(line + "\n"))
    cursor = after
    seen = 0
    while True:
        for event in ledger.events(after=cursor):
            cursor = int(event["seq"])
            seen += 1
            write(json.dumps(event, ensure_ascii=False))
            if limit is not None and seen >= limit:
                return cursor
        if once:
            return cursor
        sleep(interval)
