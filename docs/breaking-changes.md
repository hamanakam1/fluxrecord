# Breaking changes

What counts as a breaking change, how it is announced, and how long the
old form is read.

The rule exists so that a tool built against fluxrecord keeps working.
The standard is the product; a standard that breaks without saying so is
not one.

## What is a breaking change

**The wire format.** Renaming a field, removing one, or changing the
type of one is a breaking change. The frozen shapes are the `ticket` and
the `packet` — see [`wire-format.schema.json`](wire-format.schema.json).
The wire format has its own counter, separate from the package version.

**The ticket file layout.** The front matter of an exported ticket is a
format other tools read. A field that disappears, or is renamed, breaks
them.

**The idempotency marker.** The marker identifies a published comment.
Changing it means the old comments are no longer recognised, so they
would be published again.

**A status value.** The status vocabulary is in the specification. A
status that is removed, or renamed, breaks anything that filters on it.

**The failure taxonomy.** The categories group failures by fingerprint.
A category that disappears changes what a recurrence means.

## What is not a breaking change

- **A new optional field** on a shape that is not frozen, such as a
  board row or a session view.
- **A new command**, or a new flag on an existing one.
- **A new status**, as long as the old ones keep working and the
  specification says which are reachable.
- **A new failure category**, as long as the existing ones keep their
  meaning.
- **A new field on a ticket**, which is additive: the ticket file gains a
  field, and a reader that does not know it ignores it.
- **The database schema**, as long as the migration is additive and
  idempotent. The schema has its own counter, and a ledger written by an
  older version keeps working.

## How it is announced

1. The change is written in `CHANGELOG.md` under the version that
   carries it, in the form
   [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) asks for.
2. The specification changes with it, and carries its own version.
3. The major version of the package goes up: 0.x to 1.0, 1.0 to 2.0.
4. The wire format counter goes up, if the wire format changed.

## How long the old form is read

**The old form keeps working until the next major version, and for one
more.** In practice:

| Change | The old form is read for |
|--------|--------------------------|
| A renamed wire field | Until the next major version |
| A renamed marker namespace | One release, with both namespaces read — see `markers_for()` |
| A removed status | Until the next major version; the migration maps it |
| A removed failure category | Until the next major version |

The marker rename is the worked example. The namespace changed from
`taskrivet` to `fluxrecord`, and the old one is still read: a comment
published by an earlier version is recognised, and confirmed rather than
sent again. Both namespaces live in `fluxrecord.github_sync`, and
`markers_for()` returns the ones that count.

## What happens to a ledger that has not been migrated

Nothing. Migrations are additive and idempotent, and they run when the
ledger is opened. A ledger written by version 0.2 opens in 0.3 and keeps
its tickets, its sessions and its evidence. A ledger written by a version
whose schema was never released into the wild is not a thing.
