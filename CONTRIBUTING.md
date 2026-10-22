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
```

CI runs all four on Python 3.11 and 3.12. The suite covers the ledger, the
adapters, the authentication module, the documented interface, and the rules
that must hold for any input.

---

## Code Style

- **Type hints** on all public functions (see `core.py`, `tools.py`, `dashboard.py`, `github_sync.py`, `auth/`)
- **No runtime dependencies** in core (stdlib only)
- **No network** in core or read commands
- **No telemetry** in core or read commands
- **Synthetic fixtures only** in tests

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