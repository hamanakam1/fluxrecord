#!/usr/bin/env python3
"""Publish the wire format as a JSON Schema.

`fluxrecord/wire.py` holds the shapes in the package's own terms, because
the package is stdlib-only and a schema is not worth a dependency. The
artefact other people read is `docs/wire-format.schema.json`, in the form a
JSON Schema reader wants.

Two documents describing one thing drift. So the second is *generated* from
the first, and a test checks that the file on disk is what this script
would write. Regenerate it whenever the shapes change:

    python3 scripts/publish_schema.py

    python3 scripts/publish_schema.py --check   # fail if it is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluxrecord import wire  # noqa: E402

TARGET = REPO / "docs" / "wire-format.schema.json"

#: The JSON Schema type for each kind the code's schema uses.
SCALARS = {"string": "string", "integer": "integer", "number": "number"}


def json_type(kind: str) -> dict:
    """The JSON Schema fragment for one of the code's type names.

    The code writes a union as "a|b", which JSON Schema spells as a list of
    types. Getting this wrong is how a published schema stops being a
    schema, so the translation lives in one place.
    """
    if "|" in kind:
        parts = kind.split("|")
        return {"type": [SCALARS.get(part, part) for part in parts]}
    if kind == "string[]":
        return {"type": "array", "items": {"type": "string"}}
    if kind == "object[]":
        return {"type": "array", "items": {"type": "object"}}
    if kind in SCALARS:
        return {"type": SCALARS[kind]}
    return {"type": kind}


def build() -> dict:
    """The published schema, from the code's shapes."""
    schema: dict = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://fluxrecord.dev/schema/wire-format-v1.json",
        "title": "fluxrecord wire format, version 1",
        "description": (
            "The shapes fluxrecord reads and writes. A third-party "
            "implementation that produces and consumes these shapes "
            "interoperates with fluxrecord. Adding a field is a minor "
            "version; removing one, renaming one, or changing its type is "
            "a major version."
        ),
        "x-wire-format": wire.WIRE_FORMAT,
        "$defs": {},
    }
    for shape, spec in wire.SCHEMA.items():
        if shape == "wire_format":
            continue
        definition: dict = {
            "type": "object",
            "required": sorted(spec["required"]),
            "properties": {},
        }
        for field, kind in spec["types"].items():
            definition["properties"][field] = json_type(kind)
        for field, allowed in spec.get("values", {}).items():
            definition["properties"].setdefault(field, {})["enum"] = list(allowed)
        if spec.get("frozen"):
            definition["additionalProperties"] = False
        schema["$defs"][shape] = definition
    return schema


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the published file is out of date",
    )
    args = parser.parse_args()

    text = render(build())
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET} is out of date; run scripts/publish_schema.py", file=sys.stderr)
            return 1
        print(f"{TARGET} is up to date")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
