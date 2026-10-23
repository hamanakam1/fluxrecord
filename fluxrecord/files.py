"""
Ticket file storage.

Mirrors the ledger into the on-disk layout described in SPEC.md, so tickets
can be read, diffed and reviewed without opening the database:

    .fluxrecord/
    ├── tickets/
    │   └── M-397001.md
    ├── index.json          # { "M-397001": { "title": ..., "status": ... } }
    └── evidence/
        └── M-397001/
            ├── checkpoint-1.json
            └── finish.json

The files are a projection of the ledger, never a second source of truth:
the ledger decides, these files explain.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

__all__ = ["scaffold", "export", "read_ticket", "render_ticket", "TICKET_FIELDS"]

#: Front matter fields, in the order the specification lists them.
TICKET_FIELDS = (
    "id",
    "title",
    "status",
    "priority",
    "kind",
    "scope",
    "token_budget",
    "tags",
    "depends_on",
    "blocks",
    "owner",
    "seed_key",
    "created_at",
    "updated_at",
)

_LIST_FIELDS = ("scope", "tags", "depends_on", "blocks")


def _inside(directory: Path, name: str) -> Path:
    """Resolve a name inside a directory, refusing anything that escapes it."""
    candidate = (directory / name).resolve()
    base = directory.resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"refusing to write outside {base}: {name}")
    return candidate


def _stamp(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def _value(field: str, value: Any) -> str:
    if field in _LIST_FIELDS:
        items = value if isinstance(value, list) else []
        return "[" + ", ".join(json.dumps(item) for item in items) + "]"
    if field in ("created_at", "updated_at"):
        return f'"{_stamp(value)}"'
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return json.dumps("" if value is None else value)


def read_ticket(path: str | Path) -> dict[str, Any]:
    """Read an exported ticket file back into the ticket it describes.

    The file is a projection of the ledger, and this is the other
    direction: a reader that has only the file can say what the ticket
    says. A tool that does not share the ledger can still read a ticket.

    The front matter is the frozen shape; the body is prose, and what can
    be read from it is read.
    """
    text = Path(path).expanduser().read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("the file has no front matter")
    end = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if end is None:
        raise ValueError("the file has no front matter")
    fields = _front_matter("".join(lines[1:end]))
    body = "".join(lines[end + 1 :])
    sections = _sections(body)
    ticket: dict[str, Any] = dict(fields)
    ticket["description"] = sections.get("Description", "").strip()
    acceptance = sections.get("Acceptance Criteria", "").strip()
    ticket["acceptance"] = acceptance.removeprefix("- [ ]").strip()
    ticket["dependencies"] = _dependencies(sections.get("Dependencies", ""))
    ticket["failures"] = [
        line.strip().lstrip("- ").strip()
        for line in sections.get("Failures", "").splitlines()
        if line.strip().startswith("- ") and line.strip() != "- none"
    ]
    ticket["evidence_log"] = _evidence_log(sections.get("Evidence Log", ""))
    return ticket


def _front_matter(block: str) -> dict[str, Any]:
    """The front matter of a ticket file, as values."""
    fields: dict[str, Any] = {}
    for line in block.strip().splitlines():
        name, _, raw = line.partition(":")
        name = name.strip()
        raw = raw.strip()
        if not name:
            continue
        if raw.startswith("[") and raw.endswith("]"):
            # The writer emits JSON, so the reader reads JSON: a value with
            # a comma in it survives the round trip.
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = [part.strip().strip('"') for part in raw[1:-1].split(",") if part.strip()]
            fields[name] = parsed
        elif raw.startswith('"') and raw.endswith('"'):
            try:
                fields[name] = json.loads(raw)
            except json.JSONDecodeError:
                fields[name] = raw[1:-1]
        elif raw.lstrip("-").isdigit():
            fields[name] = int(raw)
        else:
            fields[name] = raw
    return fields


def _sections(body: str) -> dict[str, str]:
    """The markdown sections of a ticket file."""
    sections: dict[str, str] = {}
    name = ""
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if name:
                sections[name] = "\n".join(lines)
            name = line[3:].strip()
            lines = []
            continue
        lines.append(line)
    if name:
        sections[name] = "\n".join(lines)
    return sections


def _dependencies(section: str) -> list[str]:
    """The ids a ticket depends on, from the dependencies section."""
    found: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- depends on:"):
            found.extend(part.strip() for part in line.split(":", 1)[1].split(",") if part.strip())
    return [item for item in found if item != "(none)"]


def _evidence_log(section: str) -> list[dict[str, str]]:
    """The evidence log of a ticket file, as rows."""
    rows: list[dict[str, str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower() == "timestamp":
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the separator row of the table
        rows.append(
            {
                "at": cells[0],
                "actor": cells[1],
                "action": cells[2],
                "evidence": cells[3],
            }
        )
    return rows


def render_ticket(task: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Render one ticket as the markdown file the specification describes."""
    lines = ["---"]
    for field in TICKET_FIELDS:
        if field == "created_at":
            if task.get("created"):
                lines.append(f'created_at: "{_stamp(task["created"])}"')
            continue
        if field not in task:
            continue
        lines.append(f"{field}: {_value(field, task[field])}")
    lines.append("---")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append(str(task.get("objective", "")))
    lines.append("")
    lines.append("## Acceptance Criteria")
    lines.append("")
    lines.append(f"- [ ] {task.get('acceptance', '')}")
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    lines.append("- [ ]")
    lines.append("")
    lines.append("## Dependencies")
    lines.append("")
    depends = task.get("depends_on") or []
    blocks = task.get("blocks") or []
    lines.append(f"- depends on: {', '.join(depends) if depends else '(none)'}")
    lines.append(f"- blocks: {', '.join(blocks) if blocks else '(none)'}")
    lines.append("")
    lines.append("## Files Created")
    lines.append("")
    lines.append("-")
    lines.append("")
    lines.append("## Evidence Log")
    lines.append("")
    lines.append("| Timestamp | Actor | Action | Evidence |")
    lines.append("|-----------|-------|--------|----------|")
    for event in evidence.get("events", []):
        data = event.get("data", {})
        actor = data.get("owner") or data.get("actor") or "operator"
        summary = data.get("reason") or data.get("summary") or data.get("evidence") or ""
        lines.append(
            f"| {_stamp(event.get('created'))} | {actor} | {event.get('type')} | {summary} |"
        )
    for checkpoint in evidence.get("checkpoints", []):
        detail = str(checkpoint.get("evidence", "")).replace("|", "\\|")
        lines.append(
            f"| {_stamp(checkpoint.get('created'))} | {checkpoint.get('owner', 'worker')} "
            f"| checkpoint | {checkpoint.get('summary', '')} — {detail} |"
        )
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    failures = evidence.get("failures", [])
    if failures:
        for failure in failures:
            lines.append(
                f"- `{failure.get('category')}` — {failure.get('summary')} "
                f"(fingerprint {failure.get('fingerprint')})"
            )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def scaffold(project_dir: str | Path) -> dict[str, Any]:
    """Create the project layout described in SPEC.md."""
    root = Path(project_dir).expanduser()
    tickets = root / "tickets"
    evidence = root / "evidence"
    tickets.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    index = root / "index.json"
    created = not index.exists()
    if created:
        index.write_text("{}\n")
    return {
        "project": str(root),
        "tickets": str(tickets),
        "evidence": str(evidence),
        "index": str(index),
        "created": created,
    }


def export(ledger: Any, project_dir: str | Path) -> dict[str, Any]:
    """Write every ticket and its evidence into the project directory."""
    root = Path(project_dir).expanduser()
    layout = scaffold(root)
    tasks = ledger.status()["tasks"]
    index: dict[str, Any] = {}
    written: list[str] = []
    for task in tasks:
        task_id = str(task["id"])
        evidence = ledger.evidence(task_id)
        ticket_path = _inside(root / "tickets", f"{task_id}.md")
        ticket_path.write_text(render_ticket(task, evidence))
        written.append(str(ticket_path))

        folder = _inside(root / "evidence", task_id)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for number, checkpoint in enumerate(evidence["checkpoints"], 1):
            payload = {
                "session_id": checkpoint["session_id"],
                "sequence": number,
                "summary": checkpoint["summary"],
                "evidence": checkpoint["evidence"],
                "next_step": checkpoint["next_step"],
                "tokens": checkpoint["tokens"],
                "created_at": _stamp(checkpoint["created"]),
                "evidence_hash": "sha256:"
                + hashlib.sha256(str(checkpoint["evidence"]).encode()).hexdigest(),
            }
            (folder / f"checkpoint-{number}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            )
        # Events are in ledger order. A retry can finish again, so project
        # the latest completion while retaining every attempt in the ledger.
        for event in reversed(evidence["events"]):
            if event.get("type") == "session.finished":
                (folder / "finish.json").write_text(
                    json.dumps(event.get("data", {}), indent=2, ensure_ascii=False) + "\n"
                )
                break
        index[task_id] = {
            "title": task.get("title", ""),
            "status": task.get("status", ""),
            "priority": task.get("priority", 2),
            "owner": task.get("owner", ""),
            "updated_at": _stamp(task.get("updated_at")),
        }
    (root / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    return {
        "project": layout["project"],
        "tasks": len(tasks),
        "files": written,
        "index": layout["index"],
        "note": "Files are a projection of the ledger; the ledger remains the source of truth.",
    }
