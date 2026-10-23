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
import os
import secrets
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


# os.replace accepts directory descriptors wherever os.rename does, but is
# not itself listed in supports_dir_fd on every supported Python platform.
_SECURE_WRITES = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.stat, os.rename, os.unlink))
    and os.stat in os.supports_follow_symlinks
)


def _component(name: str) -> None:
    if not name or name in (".", "..") or any(char in name for char in ("/", "\\", "\0")):
        raise ValueError(f"refusing unsafe output path component: {name!r}")


@contextmanager
def _project(root: Path) -> Iterator[int]:
    if not _SECURE_WRITES:
        raise OSError(
            "safe exports require directory-relative filesystem operations with no-follow support"
        )
    # The caller resolves the explicitly selected root once. Everything below
    # that boundary is opened through directory descriptors without following links.
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _directory(parent: int, name: str) -> Iterator[int]:
    _component(name)
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    # Check for a useful error, then enforce the same rule atomically at open.
    if not stat.S_ISDIR(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
        raise ValueError(f"refusing non-directory or symlink output directory: {name}")
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _regular_file(directory: int, name: str) -> bool:
    _component(name)
    try:
        mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError(f"refusing non-regular or symlink output file: {name}")
    return True


def _write_text(directory: int, name: str, text: str) -> None:
    """Replace one complete UTF-8 file without writing through any existing inode."""
    _regular_file(directory, name)
    temporary = f".fluxrecord-{secrets.token_hex(16)}.tmp"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        try:
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _regular_file(directory, name)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


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
    if not text.startswith("---"):
        raise ValueError("the file has no front matter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("the file has no front matter")
    fields = _front_matter(parts[1])
    body = parts[2]
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
    index = root / "index.json"
    with _project(root.resolve()) as directory:
        with _directory(directory, "tickets"), _directory(directory, "evidence"):
            created = not _regular_file(directory, "index.json")
            if created:
                _write_text(directory, "index.json", "{}\n")
    return {
        "project": str(root),
        "tickets": str(tickets),
        "evidence": str(evidence),
        "index": str(index),
        "created": created,
    }


def _evidence_files(evidence: dict[str, Any]) -> Iterator[tuple[str, str]]:
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
        yield f"checkpoint-{number}.json", json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    # Ledger evidence is ordered by event sequence. A retry can finish again;
    # the projection describes the newest completion while history stays intact.
    for event in reversed(evidence["events"]):
        if event.get("type") == "session.finished":
            yield (
                "finish.json",
                json.dumps(event.get("data", {}), indent=2, ensure_ascii=False) + "\n",
            )
            break


def export(ledger: Any, project_dir: str | Path) -> dict[str, Any]:
    """Write every ticket and its evidence into the project directory.

    Each file is replaced atomically; the full export is not a transaction.
    Existing output symlinks are refused and hardlinked inodes are never changed.
    """
    root = Path(project_dir).expanduser()
    resolved_root = root.resolve()
    index: dict[str, Any] = {}
    written: list[str] = []
    with _project(resolved_root) as directory:
        tasks = ledger.status()["tasks"]
        with (
            _directory(directory, "tickets") as tickets,
            _directory(directory, "evidence") as evidence_dir,
        ):
            # Refuse all existing unsafe paths before replacing any projection file.
            _regular_file(directory, "index.json")
            for task in tasks:
                task_id = str(task["id"])
                _component(task_id)
                evidence = ledger.evidence(task_id)
                _regular_file(tickets, f"{task_id}.md")
                with _directory(evidence_dir, task_id) as folder:
                    for number in range(1, len(evidence["checkpoints"]) + 1):
                        _regular_file(folder, f"checkpoint-{number}.json")
                    if any(event.get("type") == "session.finished" for event in evidence["events"]):
                        _regular_file(folder, "finish.json")
            # Keep only one task's evidence in memory. Every actual write checks
            # its destination again, including evidence added since preflight.
            for task in tasks:
                task_id = str(task["id"])
                evidence = ledger.evidence(task_id)
                _write_text(tickets, f"{task_id}.md", render_ticket(task, evidence))
                written.append(str(resolved_root / "tickets" / f"{task_id}.md"))
                with _directory(evidence_dir, task_id) as folder:
                    for name, text in _evidence_files(evidence):
                        _write_text(folder, name, text)
                index[task_id] = {
                    "title": task.get("title", ""),
                    "status": task.get("status", ""),
                    "priority": task.get("priority", 2),
                    "owner": task.get("owner", ""),
                    "updated_at": _stamp(task.get("updated_at")),
                }
            _write_text(
                directory, "index.json", json.dumps(index, indent=2, ensure_ascii=False) + "\n"
            )
    return {
        "project": str(root),
        "tasks": len(tasks),
        "files": written,
        "index": str(root / "index.json"),
        "note": "Files are a projection of the ledger; the ledger remains the source of truth.",
    }
