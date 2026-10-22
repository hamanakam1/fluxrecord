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
- Idempotency keys are plain tokens, so a key cannot break the marker

### Authentication
- HS256 only, and the algorithm is read from the header, never trusted from it
- Signatures compared with `hmac.compare_digest`
- `exp`, `nbf`, `iss` and `aud` checked on every verification
- Scopes are granted by the credential verifier, never by the request
- Refresh tokens rotate and are spent once
- Every token carries a `jti`, so it can be revoked

### Ticket Files
- Ticket ids are plain identifiers, so an id cannot walk out of the project
- Every exported path is resolved and checked against its directory

### Secrets
- Never in repo
- Injected via env/CSI at deploy
- `FLUX_DB`, `FLUX_GITHUB_DB` env vars
- `NO_COLOR` disables ANSI colour in the wordmark

---

## Reporting a Vulnerability

Email: security@fluxrecord.dev (or GitHub Security Advisories)

Include:
- Affected component
- Reproduction steps
- Impact assessment
- Suggested fix (if any)

We aim to acknowledge within 48 hours.