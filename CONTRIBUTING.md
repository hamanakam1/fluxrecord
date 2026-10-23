# Contributing

Start with a focused issue or pull request describing an **observable user outcome**.

Use Python 3.11+ and run `python3 -m unittest discover -s tests -v` before proposing a change.

**Do not conflate:**
- eligible routing with authorization
- a finished session with accepted work
- declared usage with measured billing
- ledger checks with process isolation

**Stage only the paths you own.** Never add provider credentials or private transcripts.

**Keep private** deployment names, internal architecture, operational screenshots, local installation paths, and real ticket data out of public source, docs, and issues.

**Use** generic integration names, explicitly configured origins, and synthetic fixtures.

**Preserve** the distinction between recommendation, permission, execution, verification, and acceptance.

**Add meaningful tests** for concurrency, failure recovery, and budget boundaries when changing them.

**Do not** add network calls or telemetry to read commands.

**Do not** publish real transcripts, credentials, account details, or private tickets in fixtures. Keep examples synthetic.

**Document** schema changes and a migration plan.

**Changes to adapters** must state their supported versions, permission model, usage provenance, cancellation semantics, and unverified assumptions. Prefer a small qualified adapter over a large list of nominal integrations.

---

## Development

```bash
# Run tests
python3 -m unittest discover -s tests -v

# Run specific test
python3 -m unittest tests.test_core.LedgerTest.test_concurrent_claim_has_one_winner -v

# Type checking
mypy fluxrecord/

# Lint and format
ruff check fluxrecord/
ruff format --check fluxrecord/

# Regenerate the README screenshots (needs Chrome)
python3 scripts/capture_screenshots.py

# Regenerate the published wire-format schema from the code's shapes
python3 scripts/publish_schema.py

# Check an implementation against the standard
python3 conformance/run.py
```

CI runs all four on Python 3.11 and 3.12. The suite covers the ledger, the
adapters, the authentication module, the documented interface, and the rules
that must hold for any input.

### Screenshots

The images in `docs/screenshots/` are **generated**, not drawn.
`scripts/capture_screenshots.py` runs the real CLI against a throwaway
ledger, renders the output through a headless browser, and writes the PNGs.
Regenerate them in the same commit as any change to what the CLI prints, so
the documentation never describes a build that does not exist.

The script redacts the machine's home directory and any temporary build
path before rendering, so a screenshot never carries a real local path. Keep
it that way: use synthetic ticket data, and keep private deployment names
and real ticket data out of the rendered output.

---

## Code Style

- **Type hints** on all public functions (see `core.py`, `tools.py`, `dashboard.py`, `github_sync.py`, `auth/`)
- **No runtime dependencies** in core (stdlib only)
- **No network** in core or read commands
- **No telemetry** in core or read commands
- **Synthetic fixtures only** in tests

CLI subprocess tests should use `tests.cli_support.isolated_cli_environment`
with a temporary fixture directory. It supplies temporary ledger/sync paths,
removes inherited FluxRecord settings, and resolves this checkout explicitly.
Run project-creating commands from the fixture directory. Keep real `gh`
off a transport fixture's PATH even when its process launcher is mocked,
so changing subprocess APIs cannot accidentally reach an authenticated CLI.

### The boundary is checked

`tests/test_boundaries.py` reads the source with `ast` and refuses the core
ledger any import that can reach the network or start a process, any import
of an adapter, any dynamic import or `exec`, and any write to stdout. It
runs as a named step in CI.

If you need to add a module to the core, edit `CORE_ALLOWED` in that test
and write the reason next to it — the default is no, and the change is
visible in the diff. If you need the core to reach outside, that is a
change to the architecture, and it belongs in an ADR before it belongs in
an import statement.

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| SQLite + WAL | Local-first, no external deps, ACID |
| JSON-first CLI | Machine-readable, dashboard-friendly |
| Lease-based ownership | Prevents conflicts, visible expiry |
| Cumulative tokens | Budget survives retries |
| Evidence hashes | Detects repeated evidence (stagnation) |
| Acceptance gate | Separates execution from acceptance |
| Failure fingerprints | Stable grouping of recurrences |
| Additive migrations | Schema changes never rewrite history |
| Seed keys | Seeding is idempotent, so an import can be repeated |
| Wordmark as code | The mark is generated, so it never drifts between styles |
