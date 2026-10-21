# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

---

## Security Boundaries

### Core (stdlib-only, no network)
- No network calls
- No shell execution
- No telemetry
- No provider credentials
- SQLite WAL mode with `foreign_keys=ON`

### Adapters (network allowed, declared)
- Declare supported versions
- Declare permission model
- Declare cancellation semantics
- Declare unverified assumptions
- No credentials in fixtures

### Dashboard Preview (local-only)
- HTTP only, localhost/127.0.0.1/::1
- No redirects
- 8 MiB response limit
- 10-second timeout
- No credentials in URL

### GitHub Sync
- Uses `gh` CLI only
- No token in code
- 30-second timeout
- 16 MiB response limit

### Secrets
- Never in repo
- Injected via env/CSI at deploy
- `FLUX_DB`, `FLUX_GITHUB_DB` env vars

---

## Reporting a Vulnerability

Email: security@fluxrecord.dev (or GitHub Security Advisories)

Include:
- Affected component
- Reproduction steps
- Impact assessment
- Suggested fix (if any)

We aim to acknowledge within 48 hours.