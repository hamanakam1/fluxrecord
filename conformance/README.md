# The conformance suite

The standard is the shapes in
[`docs/wire-format.schema.json`](../docs/wire-format.schema.json). This
directory checks that an implementation of the standard produces those
shapes.

It is deliberately not a test suite for fluxrecord. It knows nothing about
fluxrecord's internals — only about the wire format — so it means the same
thing pointed at somebody else's implementation. That is the point of a
conformance suite: it is what makes "fluxrecord-compatible" mean something
a third party can check.

## Running it

```bash
# Check this implementation.
python3 conformance/run.py

# Check another one.
python3 conformance/run.py --command "my-ledger --format json"

# A report a build can consume.
python3 conformance/run.py --json
```

The report is on stdout, one line per check, and the exit code is the
verdict: zero when every check passed, one otherwise. `--json` prints the
same thing as a JSON document.

## What it does

It runs a fixed sequence of commands against the implementation — create a
project, add two tickets, claim one, check point it, finish it, cancel the
other — and checks every report against the published schema.

The sequence is fixed on purpose. A conformance suite that let the
implementation choose what to run would check nothing.

## What it needs

`jsonschema`, which is a development dependency of this project and not a
runtime one. The package itself stays stdlib-only.

## What it does not do

It does not check behaviour, only shapes. That is a real limit, and it is
the difference between an interchange format and a specification: this
suite will tell you that your `list` command emits the right fields with
the right types, and it will not tell you whether your ledger refuses two
workers holding the same ticket. Behaviour is specified in
[`SPEC.md`](../SPEC.md) and checked by the reference implementation's own
tests.

Adding behaviour to the suite means adding a command to the sequence and a
shape to the published schema. Both are breaking changes to the standard
under the policy in [`docs/breaking-changes.md`](../docs/breaking-changes.md).
