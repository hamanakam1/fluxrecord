"""JSON-first CLI for fluxrecord."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, NoReturn


class JsonParser(argparse.ArgumentParser):
    """An argument parser that reports usage errors as JSON.

    The CLI is JSON-first, so an error a user can trigger by typing should
    look like every other error the CLI produces.
    """

    def error(self, message: str) -> NoReturn:
        payload = {"error": message, "type": "UsageError"}
        sys.stderr.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        raise SystemExit(2)


def parser() -> argparse.ArgumentParser:
    p = JsonParser(
        prog="flux",
        description="fluxrecord: purposeful agent work, bounded context, visible failures.",
    )
    p.add_argument(
        "--db",
        default=os.environ.get("FLUX_DB", str(Path.home() / ".fluxrecord" / "ledger.sqlite")),
    )
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the ledger and the project layout")
    init.add_argument("--project", default=".fluxrecord", help="project directory for ticket files")

    sub.add_parser("status")

    lst = sub.add_parser("list", help="list tickets with filters")
    lst.add_argument("--status")
    lst.add_argument("--priority", type=int)
    lst.add_argument("--kind")
    lst.add_argument("--owner")
    lst.add_argument("--tag")
    lst.add_argument("--scope")
    lst.add_argument("--limit", type=int, default=100)

    sub.add_parser("version", help="print the package version")

    token = sub.add_parser("token", help="issue and check tokens")
    token_sub = token.add_subparsers(dest="token_command", required=True)

    issue = token_sub.add_parser("issue", help="issue a signed token")
    issue.add_argument("--subject", required=True)
    issue.add_argument("--ttl", type=int, default=900)
    issue.add_argument("--scope", dest="scopes", action="append", default=None)
    issue.add_argument("--issuer", default="")
    issue.add_argument("--audience", default="")
    issue.add_argument("--type", dest="token_type", default="access", choices=["access", "refresh"])

    check = token_sub.add_parser("verify", help="verify a token")
    check.add_argument("token")
    check.add_argument("--issuer", default="")
    check.add_argument("--audience", default="")

    look = token_sub.add_parser("introspect", help="describe a token (RFC 7662)")
    look.add_argument("token")
    look.add_argument("--issuer", default="")
    look.add_argument("--audience", default="")

    doc = sub.add_parser("doctor", help="report on the ledger, the project and the adapters")
    doc.add_argument("--project", default=".fluxrecord")
    doc.add_argument("--sync-db", default=str(Path.home() / ".fluxrecord" / "github.sqlite"))
    doc.add_argument("--dashboard", default=None, help="probe a local dashboard origin")

    show = sub.add_parser("show", help="show one ticket and everything recorded about it")
    show.add_argument("task")

    exp = sub.add_parser("export", help="write tickets to the project directory")
    exp.add_argument("--project", default=".fluxrecord")

    imp = sub.add_parser("import", help="import a CSV backlog into the ledger")
    imp.add_argument("--file", required=True)
    imp.add_argument("--kind", default="code")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument(
        "--reassign",
        action="store_true",
        help="give a fresh id to rows whose id is already used by another ticket",
    )

    art = sub.add_parser("art", help="print the fluxrecord wordmark")
    art.add_argument(
        "--style", choices=["large", "small", "mini", "minimal", "ascii"], default="large"
    )
    art.add_argument("--color", action="store_true", help="ANSI colour (off when NO_COLOR is set)")

    for command in ("github-poll", "github-status"):
        g = sub.add_parser(command)
        g.add_argument("--repo", required=True)
        g.add_argument(
            "--sync-db",
            default=os.environ.get(
                "FLUX_GITHUB_DB", str(Path.home() / ".fluxrecord" / "github.sqlite")
            ),
        )

    g = sub.add_parser("github-publish", help="queue and publish an idempotent comment")
    g.add_argument("--repo", required=True)
    g.add_argument("--issue", type=int, required=True)
    g.add_argument("--key", required=True, help="idempotency key (max 100 characters)")
    g.add_argument("--body", required=True)
    g.add_argument(
        "--sync-db",
        default=os.environ.get(
            "FLUX_GITHUB_DB", str(Path.home() / ".fluxrecord" / "github.sqlite")
        ),
    )

    g = sub.add_parser("github-reconcile", help="confirm sent comments against the issue")
    g.add_argument("--repo", required=True)
    g.add_argument(
        "--sync-db",
        default=os.environ.get(
            "FLUX_GITHUB_DB", str(Path.home() / ".fluxrecord" / "github.sqlite")
        ),
    )

    g = sub.add_parser("github-cycle")
    g.add_argument("--config", required=True)

    g = sub.add_parser("github-intake", help="turn observations into tickets")
    g.add_argument("--repo", required=True)
    g.add_argument("--sync-db", default=str(Path.home() / ".fluxrecord" / "github.sqlite"))
    g.add_argument("--kind", dest="kinds", action="append", default=None)
    g.add_argument("--limit-per-kind", type=int, default=25)
    g.add_argument(
        "--include-informational",
        action="store_true",
        help="also ticket observations that imply no work",
    )

    d = sub.add_parser("dashboard-preview")
    d.add_argument("--url", required=True, help="Explicit local ticket-service origin")
    d.add_argument(
        "--status",
        choices=["planned", "backlog", "progress", "blocked", "show_tell"],
        default="planned",
    )
    d.add_argument("--token", default=None, help="bearer token, when the dashboard is protected")
    d.add_argument("--offset", type=int, default=0)

    din = sub.add_parser("dashboard-intake", help="turn dashboard tickets into ledger tickets")
    din.add_argument("--url", required=True, help="Explicit local ticket-service origin")
    din.add_argument("--status", default="planned")
    din.add_argument("--offset", type=int, default=0)
    din.add_argument("--token", default=None, help="bearer token, when the dashboard is protected")

    b = sub.add_parser("tools")
    b.add_argument("--history", action="store_true")
    b.add_argument("--limit", type=int, default=50)

    e = sub.add_parser("events")
    e.add_argument("--after", type=int, default=0)
    e.add_argument("--type", dest="kind", default=None)
    e.add_argument("--task", dest="task_id", default=None)
    e.add_argument("--limit", type=int, default=None)

    ses = sub.add_parser("sessions", help="who is holding what, and for how long")
    ses.add_argument("--active", action="store_true", help="only the sessions still holding work")

    a = sub.add_parser("task-add")
    a.add_argument("id")
    a.add_argument("--objective", required=True)
    a.add_argument("--acceptance", required=True)
    a.add_argument("--kind", default="code")
    a.add_argument("--scope", action="append", required=True)
    a.add_argument("--token-budget", "--budget", dest="token_budget", type=int, default=8000)
    a.add_argument("--title", default="")
    a.add_argument("--priority", type=int, default=2, help="1=P1-Now, 2=P2-Next, 3=P3-Later")
    a.add_argument("--tag", dest="tags", action="append", default=None)
    a.add_argument("--depends-on", dest="depends_on", action="append", default=None)
    a.add_argument("--blocks", action="append", default=None)
    a.add_argument("--owner", default="")
    a.add_argument("--seed-key", dest="seed_key", default="", help="idempotent seeding key")

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

    rn = sub.add_parser("renew", help="extend the lease on an active session")
    rn.add_argument("session")
    rn.add_argument("--lease", type=int, default=900)

    sub.add_parser("reconcile", help="expire leases that have run out")

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

    d = sub.add_parser("decide", help="operator decision on a task")
    d.add_argument("task")
    d.add_argument("decision", choices=["accept", "retry", "cancel"])
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
    c.add_argument(
        "--requires",
        dest="requires",
        action="append",
        default=None,
        help="a local tool this profile needs",
    )

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

        if a.command == "dashboard-intake":
            from .core import Ledger
            from .dashboard import preview
            from .intake import intake_dashboard

            seen = preview(a.url, a.status, a.offset, a.token)
            ledger = Ledger(a.db)
            try:
                print(
                    json.dumps(
                        intake_dashboard(ledger, seen, a.url),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            finally:
                ledger.close()
            return

        if a.command == "dashboard-preview":
            from .dashboard import preview

            print(json.dumps(preview(a.url, a.status, a.offset, a.token), indent=2))
            return

        if a.command == "tools":
            from .tools import inventory

            print(json.dumps(inventory(a.history, a.limit), indent=2))
            return

        from .core import Ledger

        if a.command == "events":
            ledger = Ledger(a.db)
            print(
                json.dumps(
                    ledger.events(a.after, a.kind, a.task_id, a.limit),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        if a.command == "token":
            from .tokens import introspect_token, issue_token, secret_from_environment, verify_token

            secret = secret_from_environment()
            if a.token_command == "issue":
                print(
                    json.dumps(
                        issue_token(
                            a.subject,
                            secret,
                            a.ttl,
                            a.issuer,
                            a.audience,
                            tuple(a.scopes or ()),
                            a.token_type,
                        ),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return
            if a.token_command == "verify":
                report = verify_token(a.token, secret, a.issuer, a.audience)
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return
            print(
                json.dumps(
                    introspect_token(a.token, secret, a.issuer, a.audience),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        if a.command == "doctor":
            from .doctor import diagnose

            print(
                json.dumps(
                    diagnose(a.db, a.project, a.sync_db, a.dashboard),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        if a.command == "version":
            from . import __version__

            print(json.dumps({"name": "fluxrecord", "version": __version__}, indent=2))
            return

        if a.command == "github-intake":
            from .core import Ledger
            from .github_sync import GitHubSync
            from .intake import intake

            sync = GitHubSync(a.sync_db)
            ledger = Ledger(a.db)
            try:
                intake_result = intake(
                    ledger,
                    sync,
                    a.repo,
                    a.kinds,
                    a.limit_per_kind,
                    a.include_informational,
                )
            finally:
                ledger.close()
                sync.close()
            print(json.dumps(intake_result, indent=2, ensure_ascii=False))
            return

        if a.command == "art":
            from .ascii_art import get_art

            paint = a.color and sys.stdout.isatty()
            sys.stdout.write(get_art(a.style, color=paint) + "\n")
            return

        if a.command == "init":
            from .ascii_art import get_art
            from .core import Ledger
            from .files import scaffold

            layout = scaffold(a.project)
            ledger = Ledger(a.db)
            try:
                schema = ledger.schema_version()
            finally:
                ledger.close()
            payload = {
                "schema": schema,
                "status": "ready",
                "dispatch_enabled": False,
                "ledger": str(a.db),
                "project": layout,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            print(get_art("small", color=sys.stderr.isatty()), file=sys.stderr)
            return

        if a.command == "export":
            from .core import Ledger
            from .files import export

            ledger = Ledger(a.db)
            try:
                print(json.dumps(export(ledger, a.project), indent=2, ensure_ascii=False))
            finally:
                ledger.close()
            return

        if a.command == "import":
            from .core import Ledger
            from .importer import import_csv

            ledger = Ledger(a.db)
            try:
                print(
                    json.dumps(
                        import_csv(ledger, a.file, a.kind, a.dry_run, a.reassign),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            finally:
                ledger.close()
            return

        ledger = Ledger(a.db)
        cmd = a.command
        result: dict[str, Any]

        if cmd == "status":
            result = ledger.status()
        elif cmd == "show":
            result = ledger.show(a.task)
        elif cmd == "sessions":
            sessions = ledger.sessions(a.active)
            result = {
                "sessions": sessions,
                "count": len(sessions),
                "active": sum(1 for s in sessions if s["status"] == "active"),
                "note": "A lease is a claim on work, not a guarantee of progress.",
            }
        elif cmd == "list":
            tickets = ledger.list_tasks(
                a.status,
                a.priority,
                a.kind,
                a.owner,
                a.tag,
                a.scope,
                a.limit,
            )
            result = {
                "tickets": tickets,
                "count": len(tickets),
                "filters": {
                    "status": a.status,
                    "priority": a.priority,
                    "kind": a.kind,
                    "owner": a.owner,
                    "tag": a.tag,
                    "scope": a.scope,
                },
            }
        elif cmd == "task-add":
            result = ledger.add_task(
                a.id,
                a.objective,
                a.acceptance,
                a.kind,
                a.scope,
                a.token_budget,
                title=a.title,
                priority=a.priority,
                tags=a.tags,
                depends_on=a.depends_on,
                blocks=a.blocks,
                owner=a.owner,
                seed_key=a.seed_key,
            )
        elif cmd == "start":
            result = ledger.start(a.task, a.owner, a.lease)
        elif cmd == "checkpoint":
            result = ledger.checkpoint(a.session, a.summary, a.evidence, a.next, a.tokens, a.lease)
        elif cmd == "renew":
            result = ledger.renew(a.session, a.lease)
        elif cmd == "reconcile":
            result = ledger.reconcile()
        elif cmd == "fail":
            result = ledger.fail(a.session, a.category, a.summary)
        elif cmd == "finish":
            result = ledger.finish(a.session, a.evidence)
        elif cmd in ("accept", "retry", "cancel"):
            result = ledger.decide(a.task, cmd, a.reason)
        elif cmd == "decide":
            result = ledger.decide(a.task, a.decision, a.reason)
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
            result = ledger.profile(a.id, a.kind, a.context_limit, a.cost, a.ttl, a.requires)
        elif cmd == "route":
            from .tools import available as tools_available

            needed = {
                tool
                for profile in ledger.status()["profiles"]
                for tool in profile.get("requires", [])
            }
            present = {name for name, path in tools_available(needed).items() if path}
            result = ledger.route(a.task, a.required_context, present)
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
