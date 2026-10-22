"""
The wire format.

The JSON this package emits is a contract with other tools, so it is
versioned and frozen: from `WIRE_FORMAT` 1 onwards, changing the name of
a field, or removing one, is a breaking change and needs a major version.

The schema is written down here rather than pulled from a library,
because the package is stdlib-only and a schema is not worth a
dependency. `docs/wire-format.schema.json` is the same document in the
form a JSON Schema reader wants.
"""

from __future__ import annotations

from typing import Any

__all__ = ["WIRE_FORMAT", "SCHEMA", "validate", "problems"]

#: The version of the wire format this package speaks.
WIRE_FORMAT = 1

#: The shapes other tools may rely on. "required" lists the fields that
#: must be there; "types" says what each one holds; "frozen" means the
#: shape may not grow a field without a version bump.
SCHEMA: dict[str, Any] = {
    "wire_format": WIRE_FORMAT,
    "ticket": {
        "required": [
            "id",
            "objective",
            "acceptance",
            "kind",
            "scope",
            "status",
            "token_budget",
            "created",
            "title",
            "priority",
            "tags",
            "depends_on",
            "blocks",
            "owner",
            "seed_key",
            "updated_at",
        ],
        "types": {
            "id": "string",
            "objective": "string",
            "acceptance": "string",
            "kind": "string",
            "scope": "string[]",
            "status": "string",
            "token_budget": "integer",
            "created": "number",
            "title": "string",
            "priority": "integer",
            "tags": "string[]",
            "depends_on": "string[]",
            "blocks": "string[]",
            "owner": "string",
            "seed_key": "string",
            "updated_at": "number",
        },
        "frozen": True,
    },
    "packet": {
        "required": [
            "task_id",
            "title",
            "objective",
            "acceptance",
            "kind",
            "scope",
            "status",
            "priority",
            "tags",
            "depends_on",
            "owner",
            "token_budget",
            "failures",
            "dependencies",
            "scope_conflicts",
            "trust",
            "context",
        ],
        "types": {
            "task_id": "string",
            "title": "string",
            "objective": "string",
            "acceptance": "string",
            "kind": "string",
            "scope": "string[]",
            "status": "string",
            "priority": "integer",
            "tags": "string[]",
            "depends_on": "string[]",
            "owner": "string",
            "token_budget": "integer",
            "failures": "object[]",
            "dependencies": "object[]",
            "scope_conflicts": "object[]",
            "trust": "string",
            "context": "object[]",
            "checkpoint": "object",
        },
        "frozen": True,
    },
    # A session and a board row are views, not interchange formats: they
    # carry what a person needs to see, and they may grow a field.
    "session": {
        "required": ["id", "task_id", "owner", "status", "lease_until", "tokens"],
        "types": {
            "id": "string",
            "task_id": "string",
            "owner": "string",
            "status": "string",
            "lease_until": "number",
            "tokens": "integer",
            "created": "number",
            "progress": "string",
            "stagnant": "integer",
            "lease_remaining": "number",
            "expired": "boolean",
        },
    },
    "board_row": {
        "required": [
            "id",
            "title",
            "status",
            "priority",
            "owner",
            "age_seconds",
            "tokens",
            "last_evidence",
            "failures",
        ],
        "types": {
            "id": "string",
            "title": "string",
            "status": "string",
            "priority": "integer",
            "owner": "string",
            "age_seconds": "number",
            "tokens": "integer",
            "last_evidence": "string",
            "last_evidence_at": "number",
            "failures": "integer",
            "lease_remaining": "number",
        },
    },
}

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "string[]": (list,),
    "object[]": (list,),
}


def _matches(value: Any, kind: str) -> bool:
    expected = _TYPES.get(kind)
    if expected is None:
        return True
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind in ("string[]", "object[]"):
        return isinstance(value, list) and all(
            isinstance(item, str) if kind == "string[]" else isinstance(item, dict)
            for item in value
        )
    return isinstance(value, expected)


def problems(shape: str, value: Any) -> list[str]:
    """What is wrong with a value, against a named shape."""
    schema = SCHEMA.get(shape)
    if schema is None:
        return [f"unknown shape {shape!r}"]
    if not isinstance(value, dict):
        return [f"{shape} is not an object"]
    found: list[str] = []
    for field in schema["required"]:
        if field not in value:
            found.append(f"{shape}.{field} is missing")
    types = schema["types"]
    for field, kind in types.items():
        if field in value and not _matches(value[field], kind):
            found.append(f"{shape}.{field} is not {kind}")
    if schema.get("frozen"):
        for field in value:
            if field not in types:
                found.append(f"{shape}.{field} is not part of the frozen shape")
    return found


def validate(shape: str, value: Any) -> bool:
    """Whether a value matches a named shape."""
    return not problems(shape, value)
