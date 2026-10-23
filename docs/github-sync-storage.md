# GitHub observation storage

The sync database keeps distinct historical revisions in `observations`.
`observations(repo)` continues to return that history. The optional
`latest_only=True` view joins it to `observation_heads`, keyed by repository,
kind and object identity. Intake uses this view so superseded open issues or
failed runs cannot create work after a newer observed resolution.

Each observation inserts history and updates its current pointer in one
`BEGIN IMMEDIATE` transaction. Repeated identical revisions do not count as
changes. Returning to an old revision updates the pointer and its transition
time without replacing the original observation. Limits apply after selecting
current revisions, ordered by the head's transition time. This is a collection
of distinct revisions and a current view, not a complete transition journal.

## Additive migration

Opening an older sync database creates `observation_heads` if absent. It does
not rewrite observations or outbox entries. The new table intentionally starts
empty: earlier storage cannot reliably reconstruct a reversion to an old
revision, because it recorded each distinct revision only once.

Run `flux github-poll --repo owner/repo` before intake after upgrading. Only
objects actually observed by the new adapter enter the current view. The first
observation establishes a baseline and is not reported as a change; this also
works when its revision already exists in history. Reopening the database
preserves existing heads and does not reconstruct them from historical dates.
For databases that predate the `status` column, a fresh matching observation
fills an empty status from the fetched record. The original observation time,
summary and revision remain unchanged, and nonempty statuses are preserved.
No core-ledger or frozen wire-format migration is required.

## Freshness limits

Issue and pull polling includes open and closed states, ordered by update time.
Polling remains bounded by the selected kinds and page cap. Missing objects
are not assumed closed, and a head means latest locally observed state, not
proof of current upstream state. Overlapping polls can receive snapshots in a
different order from upstream changes. Older adapter versions do not maintain
heads; avoid mixed-version writers and poll with the new version again after
any older writer has used the database. No network calls occur during migration.
