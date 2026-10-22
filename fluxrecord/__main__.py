"""JSON-first CLI for fluxrecord."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="fluxrecord: purposeful agent work, bounded context, visible failures."
    )
    p.add_argument(
        "--db",
        default=os.environ.get("FLUX_DB", str(Path.home() / ".fluxrecord" / "ledger.sqlite")),
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("status")

    art = sub.add_parser("art", help="print the fluxrecord wordmark")
    art.add_argument(
        "--style", choices=["large", "small", "mini", "minimal", "ascii"], default="large"
    )
    art.add_argument("--color", action="store_true", help="ANSI colour (off when NO_COLOR is set)")

    for command in ("github-poll", "github-status"):
        g = sub.add_parser(command)
        g.add_argument("--repo", required=True)
        g.add_argument("--sync-db", default=str(Path.home() / ".fluxrecord" / "github.sqlite"))

    g = sub.add_parser("github-publish", help="queue and publish an idempotent comment")
    g.add_argument("--repo", required=True)
    g.add_argument("--issue", type=int, required=True)
    g.add_argument("--key", required=True, help="idempotency key (max 100 characters)")
    g.add_argument("--body", required=True)
    g.add_argument("--sync-db", default=str(Path.home() / ".fluxrecord" / "github.sqlite"))

    g = sub.add_parser("github-reconcile", help="confirm sent comments against the issue")
    g.add_argument("--repo", required=True)
    g.add_argument("--sync-db", default=str(Path.home() / ".fluxrecord" / "github.sqlite"))

    g = sub.add_parser("github-cycle")
    g.add_argument("--config", required=True)

    d = sub.add_parser("dashboard-preview")
    d.add_argument("--url", required=True, help="Explicit local ticket-service origin")
    d.add_argument(
        "--status",
        choices=["planned", "backlog", "progress", "blocked", "show_tell"],
        default="planned",
    )
    d.add_argument("--offset", type=int, default=0)

    b = sub.add_parser("tools")
    b.add_argument("--history", action="store_true")
    b.add_argument("--limit", type=int, default=50)

    e = sub.add_parser("events")
    e.add_argument("--after", type=int, default=0)

    a = sub.add_parser("task-add")
    a.add_argument("id")
    a.add_argument("--objective", required=True)
    a.add_argument("--acceptance", required=True)
    a.add_argument("--kind", default="code")
    a.add_argument("--scope", action="append", required=True)
    a.add_argument("--token-budget", type=int, default=8000)

    s = sub.add_parser("start")
    s.add_argument("task")
    s.add_argument("--owner", required=True)
    s.add_argument("--lease", type=int, default=900)

    c = sub.add_parser("checkpoint")
    c.add_argument("session")
    c.add_argument("--summary", required=True)
    c.add_argument("--evidence", required=True)
    c.add_argument("--next", required=True)
    c.add_argument("--tokens", type=int, required=True)
    c.add_argument("--lease", type=int, default=900)

    f = sub.add_parser("fail")
    f.add_argument("session")
    f.add_argument("--category", required=True)
    f.add_argument("--summary", required=True)

    f = sub.add_parser("finish")
    f.add_argument("session")
    f.add_argument("--evidence", required=True)

    for name in ("accept", "retry", "cancel"):
        d = sub.add_parser(name)
        d.add_argument("task")
        d.add_argument("--reason", required=True)

    c = sub.add_parser("context-add")
    c.add_argument("task")
    c.add_argument("--file", required=True)
    c.add_argument("--source", required=True)
    c.add_argument("--priority", type=int, default=0)
    c.add_argument("--ttl", type=int)

    c = sub.add_parser("pack")
    c.add_argument("task")
    c.add_argument("--max-bytes", type=int, default=12000)
    c.add_argument("--packet-only", action="store_true")

    c = sub.add_parser("profile-add")
    c.add_argument("id")
    c.add_argument("--kind", action="append", required=True)
    c.add_argument("--context-limit", type=int, required=True)
    c.add_argument("--cost", choices=["zero_incremental", "paid", "unknown"], required=True)
    c.add_argument("--ttl", type=int, default=3600)

    c = sub.add_parser("route")
    c.add_argument("task")
    c.add_argument("--required-context", type=int, default=4096)

    return p


def main() -> None:
    a = parser().parse_args()
    ledger = None
    try:
        if a.command == "github-cycle":
            from .github_cycle import cycle

            print(json.dumps(cycle(a.config)))
            return

        if a.command in ("github-poll", "github-status", "github-publish", "github-reconcile"):
            from .github_sync import GitHubSync

            sync = GitHubSync(a.sync_db)
            try:
                if a.command == "github-poll":
                    print(json.dumps(sync.poll(a.repo, max_pages=20), indent=2))
                elif a.command == "github-status":
                    print(json.dumps(sync.status(a.repo), indent=2))
                elif a.command == "github-publish":
                    queued = sync.enqueue(a.repo, a.issue, a.key, a.body)
                    published = sync.publish(a.repo)
                    print(json.dumps({"queued": queued, "published": published}, indent=2))
                else:
                    print(json.dumps(sync.reconcile(a.repo), indent=2))
            finally:
                sync.close()
            return

        if a.command == "dashboard-preview":
            from .dashboard import preview

            print(json.dumps(preview(a.url, a.status, a.offset), indent=2))
            return

        if a.command == "tools":
            from .tools import inventory

            print(json.dumps(inventory(a.history, a.limit), indent=2))
            return

        from .core import Ledger

        if a.command == "events":
            ledger = Ledger(a.db)
            print(json.dumps(ledger.events(a.after), indent=2, ensure_ascii=False))
            return

        if a.command == "art":
            from .ascii_art import get_art

            paint = a.color and sys.stdout.isatty()
            sys.stdout.write(get_art(a.style, color=paint) + "\n")
            return

        if a.command == "init":
            from .ascii_art import get_art

            payload = {"schema": 1, "status": "ready", "dispatch_enabled": False}
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            print(get_art("small", color=sys.stderr.isatty()), file=sys.stderr)
            return

        ledger = Ledger(a.db)
        cmd = a.command
        result: dict[str, Any]

        if cmd == "status":
            result = ledger.status()
        elif cmd == "task-add":
            result = ledger.add_task(
                a.id, a.objective, a.acceptance, a.kind, a.scope, a.token_budget
            )
        elif cmd == "start":
            result = ledger.start(a.task, a.owner, a.lease)
        elif cmd == "checkpoint":
            result = ledger.checkpoint(a.session, a.summary, a.evidence, a.next, a.tokens, a.lease)
        elif cmd == "fail":
            result = ledger.fail(a.session, a.category, a.summary)
        elif cmd == "finish":
            result = ledger.finish(a.session, a.evidence)
        elif cmd in ("accept", "retry", "cancel"):
            result = ledger.decide(a.task, cmd, a.reason)
        elif cmd == "context-add":
            result = ledger.add_context(
                a.task, Path(a.file).read_text(), a.source, a.priority, a.ttl
            )
        elif cmd == "pack":
            result = ledger.pack(a.task, a.max_bytes)
            if a.packet_only:
                sys.stdout.write(
                    json.dumps(result["packet"], ensure_ascii=False, separators=(",", ":"))
                )
                return
        elif cmd == "profile-add":
            result = ledger.profile(a.id, a.kind, a.context_limit, a.cost, a.ttl)
        elif cmd == "route":
            result = ledger.route(a.task, a.required_context)
        else:
            print(json.dumps({"error": f"unknown command: {cmd}"}), file=sys.stderr)
            raise SystemExit(2)

        print(json.dumps(result, indent=2, ensure_ascii=False))

    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    main()
