"""
One observation-to-publication cycle.

Reads a JSON config, observes the configured repositories, reconciles the
outbox, and returns a summary. Nothing is published unless the config asks
for it, and a failed repository never stops the others from being observed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .github_sync import GitHubSync, Runner

__all__ = ["cycle"]


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"config not found: {path}")
    try:
        config = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"config is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    return config


def _repo_of(entry: Any) -> str:
    if isinstance(entry, dict):
        repo = entry.get("repo")
    else:
        repo = entry
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("every repository entry needs a repo name")
    return repo.strip()


def _boolean(config: dict[str, Any], name: str) -> bool:
    value = config.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def cycle(config_path: str | Path, runner: Runner | None = None) -> dict[str, Any]:
    """Run one cycle described by a JSON config file.

    Config shape:
        {
          "repos": [{"repo": "owner/repo", "max_pages": 5}],
          "kinds": ["issues", "runs"],
          "sync_db": "~/.fluxrecord/github.sqlite",
          "publish": false
        }

    The optional ``publish`` and ``intake`` flags must be JSON booleans;
    only an explicit ``true`` enables either action.

    Args:
        config_path: path to the JSON config
        runner: optional gh transport, injected by tests
    """
    path = Path(config_path).expanduser()
    config = _load(path)

    repos = config.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ValueError('config must list at least one repository under "repos"')
    publish = _boolean(config, "publish")
    intake = _boolean(config, "intake")
    kinds = config.get("kinds")
    if kinds is not None and not isinstance(kinds, list):
        raise ValueError("kinds must be a list when present")
    default_db = str(Path.home() / ".fluxrecord" / "github.sqlite")
    sync_db = str(config.get("sync_db") or default_db)
    ledger_db = str(
        config.get("ledger_db")
        or os.environ.get("FLUX_DB")
        or str(Path.home() / ".fluxrecord" / "ledger.sqlite")
    )

    results: list[dict[str, Any]] = []
    observed = 0
    changed = 0
    sent: list[str] = []
    confirmed: list[str] = []
    ticketed = 0
    with GitHubSync(sync_db, runner=runner) as sync:
        for entry in repos:
            repo = _repo_of(entry)
            entry_config = entry if isinstance(entry, dict) else {}
            max_pages = int(entry_config.get("max_pages", config.get("max_pages", 20)))
            try:
                polled = sync.poll(repo, max_pages, kinds)
            except (OSError, ValueError) as exc:
                results.append({"repo": repo, "error": str(exc)})
                continue
            observed += polled["observed"]
            changed += polled["changed"]
            record: dict[str, Any] = {
                "repo": repo,
                "observed": polled["observed"],
                "changed": polled["changed"],
            }
            try:
                reconciled = sync.reconcile(repo)
                record["confirmed"] = reconciled["confirmed"]
                confirmed.extend(reconciled["confirmed"])
                if publish:
                    published = sync.publish(repo)
                    record["sent"] = published["sent"]
                    record["failed"] = published["failed"]
                    sent.extend(published["sent"])
                    confirmed.extend(published["confirmed"])
            except (OSError, ValueError) as exc:
                record["error"] = str(exc)
            if intake:
                try:
                    from .core import Ledger
                    from .intake import intake as take

                    with Ledger(ledger_db) as book:
                        taken = take(book, sync, repo, kinds)
                    record["ticketed"] = taken["created_count"]
                    record["attached"] = taken["attached_count"]
                    ticketed += taken["created_count"]
                except (OSError, ValueError) as exc:
                    record["intake_error"] = str(exc)
            results.append(record)

    return {
        "config": str(path),
        "repos": results,
        "observed": observed,
        "changed": changed,
        "published": len(sent),
        "confirmed": len(confirmed),
        "ticketed": ticketed,
        "publish_requested": publish,
        "intake_requested": intake,
        "note": "One cycle of observation and publication. Evidence, not authority.",
    }
