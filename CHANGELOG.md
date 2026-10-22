# Changelog

All notable changes to fluxrecord are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The long-term plan.** `PLAN.md` says how fluxrecord should be built: six
  phases with exit criteria that can be checked, the workstreams that run in
  parallel, the quality gates, the threat model, the risks, the open
  questions, and — deliberately — the list of things this project will not
  build.
- **Generated screenshots.** `docs/screenshots/` holds ten terminal captures
  of the real CLI, and `scripts/capture_screenshots.py` regenerates them by
  running the CLI against a throwaway ledger and rendering the output
  through a headless browser. The images are never hand-edited, and the
  script redacts the machine's home directory and any temporary build path
  before it renders anything.
- **The boundary is checked.** `tests/test_boundaries.py` reads the source
  with `ast` and refuses the core ledger any import that can reach the
  network or start a process, any import of an adapter, any dynamic import
  or `exec`, and any write to stdout. It also asserts that the package
  declares no runtime dependencies. The check has its own tests, so a gate
  that cannot fail is caught before it starts passing everything, and it
  runs as a named step in CI.
- **The specification is checked against the code.**
  `tests/test_spec_conformance.py` parses `SPEC.md` and compares it with
  the implementation: the statuses the ledger can hold, the statuses the
  specification defines, the transitions it names, the status each operator
  decision produces, the failure taxonomy, the ticket schema, and the
  command list in both directions. The standard is the product, and it had
  drifted from the code five separate times.
- **No key is always null.** `tests/test_no_null_keys.py` exercises the
  ledger in every state a ticket can be in, collects every value every
  report produces, and fails if any key is null in all of them. A key that
  is null sometimes is fine; one that is null always is a promise the tool
  cannot keep. `token_count` and `quality_estimate` are gone with it — they
  could never carry a value.
- **The event chain is reachable, and tested.** Every ledger the CLI opens
  is now chained when `FLUX_EVENT_KEY` is set, so the chain is no longer a
  capability that exists only inside the library. `tests/test_chain.py`
  tampers with a ledger — editing an event, deleting one, inserting one,
  truncating the end — and checks that the chain notices each of them.
- **A truncated log is detected.** A chain is a prefix, so dropping the
  last events used to leave a valid chain over what was left. The head of
  the chain is now recorded beside the log and compared on every check, so
  a truncation is visible too. The specification and the README say
  exactly what this does and does not prove: it is evidence against
  somebody who does not have the key, not protection against somebody who
  does.

### Changed

- **The idempotency marker is written in this project's own namespace.**
  A queued GitHub comment now carries `<!-- fluxrecord:{key}:{digest} -->`
  rather than `<!-- taskrivet:… -->`. The marker is a wire format, and
  another project's name in it is a name this project would have to break
  later. The old namespace is still **read**, so a comment published by an
  earlier version is recognised and confirmed rather than sent a second
  time; it is never written. Both the specification and the README say so.
- **The development tools are pinned.** `mypy`, `ruff`, `pytest`,
  `hypothesis` and `coverage` are pinned to exact versions. A gate that
  moves under the build is not a gate: a formatter that reformats the tree
  between two runs turns every pull request red for a change nobody made.
- **CI runs on Python 3.11, 3.12, 3.13 and 3.14**, which is the range the
  package metadata claims.
- **The acceptance gate now stores `done`.** `decide(accept)` leaves a
  ticket in `done` rather than `accepted`, which is what the specification
  has always promised — "only `decide(accept)` creates `done`". The
  acceptance itself is still recorded, as an `operator.accept` event
  followed by a `task.done` event, so nothing is lost from the log.
  `metrics.accepted` counts both spellings, so a ledger written before the
  change still reports the right number. **This is a behaviour change for
  anything that reads `status == "accepted"`.**
- `INBOX` is no longer a station. A ticket is created `ready`; a station
  that nothing can enter is decoration.
- The README now shows the ticket lifecycle and a gallery of the commands,
  and points at `PLAN.md` for the long-term view.
- `CONTRIBUTING.md` explains the boundary check and what to do when a change
  needs a module the core may not import.
- The documentation site has a "See it work" section that shows the real
  CLI: the whole ticket loop, a failure that is a record, and the bounded
  context packet.
- The screenshots are regenerated, so they show the current output.

## [0.2.0] — 2026-10-22

The ledger, the CLI and the specification were brought into agreement, and
the adapters the CLI promised were written.

### Added

- **Wordmark.** The mark is now one idea in five styles: a flux waveform
  riding above a half-block letterform. `flux art` prints it, with an
  optional ANSI gradient that honours `NO_COLOR`.
- **Adapters.** `fluxrecord.tools` (local executable inventory),
  `fluxrecord.dashboard` (bounded, GET-only local preview) and
  `fluxrecord.github_sync` with `fluxrecord.github_cycle` (durable GitHub
  observations and an idempotent outbox).
- **Authentication.** `fluxrecord.auth`: JWT issuance and verification,
  bearer middleware, and the login, refresh, revoke, introspect and
  logout routes. HS256 only, stdlib only, framework free.
- **Ticket files.** `flux export` writes the layout the specification
  describes: markdown tickets with front matter, an index, and one
  evidence file per checkpoint.
- **Import.** `flux import` reads a CSV backlog, mapping the file column
  onto scope so overlapping work is detectable. Rows whose id is already
  used by a different ticket are reported as conflicts rather than
  dropped.
- **Commands.** `flux list`, `flux show`, `flux version`, `flux renew`,
  `flux reconcile`, `flux art` and `flux import`.
- **Ticket model.** The full schema from the specification: title,
  priority, tags, dependencies, blocks, owner, seed key and updated time.
- **Migrations.** A `meta` table records the schema version, and
  migrations are additive and idempotent.
- **Property tests.** For the token rules, the token budget, the
  stagnation rule, the byte budget and scope conflicts.
- **Coverage.** CI measures the package, including the commands that run
  as subprocesses.

### Fixed

- The documented interface now exists: `flux task-add --budget` and
  `flux decide <id> <decision>` are accepted, and the acceptance gate
  records the move to `DONE`.
- A ticket id could walk out of the project directory when exported.
  Ids are now plain identifiers, and every exported path is checked.
- A client could name its own scopes at login. Scopes are granted by the
  credential verifier instead.
- A refreshed token lost its scopes, and extra claims could override the
  standard ones.
- The status vocabulary matches the specification: the working station is
  `in_progress`, and older ledgers are migrated in place.
- Usage errors are JSON, like every other error the CLI produces.
- The context packet is sized incrementally instead of being re-encoded
  for every candidate.
- Failures must name a category from the documented taxonomy.
- The GitHub response limit, the dashboard credential rule and
  `FLUX_GITHUB_DB` now exist as the security policy says they do.
- The blank ticket template and the rendered ticket can no longer drift.

### Changed

- The wordmark replaces the previous block lettering, and the README
  banner matches it.
- `mypy --strict` and `ruff` pass across the package; the type check step
  in CI had been failing since the first commit.

## [0.1.0] — 2026-10-21

The first release: a SQLite ledger with leases, checkpoints, contexts and
profiles, a JSON-first CLI, and the ticket specification.

[0.2.0]: https://github.com/hamanakam1/fluxrecord/releases/tag/v0.2.0
[0.1.0]: https://github.com/hamanakam1/fluxrecord/releases/tag/v0.1.0
