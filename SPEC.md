# fluxrecord — Ticket Standard Specification

**Version:** 1.0  
**Author:** Michael Hamanaka  
**License:** MIT  

---

## Philosophy

> **Less repeated context. More accepted work. Visible failures.**

fluxrecord is a ticket standard built for **local-first, evidence-driven workflows** where:
- Every ticket is a bounded context packet with explicit ownership
- Work flows through visible stations with explicit gates
- Evidence is required at each transition
- Failures are first-class citizens with stable fingerprints
- Acceptance is separate from execution

---

## Ticket Identifier Format

```
M-<6-digit-sequential>  (e.g., M-397001, M-397142, M-989898)
```

- **Prefix:** `M-` (for "masterboard" / "manifest" / "mission")
- **Number:** 6-digit zero-padded sequential from 000001
- **Ranges:**
  - `000001–009999` — Core infrastructure
  - `010000–099999` — Feature work
  - `100000–199999` — Security/Authorization
  - `200000–299999` — Observability/Monitoring
  - `300000–399999` — Testing/Quality
  - `400000–499999` — Developer Experience
  - `500000–599999` — UI/UX
  - `600000–699999` — Features/Enhancements
  - `700000–799999` — Data/Analytics
  - `800000–899999` — Infrastructure/Deployment
  - `900000–999999` — Experimental/Archive

---

## Ticket Schema (Markdown Front-Matter + Body)

```markdown
---
id: "M-397001"
title: "Implement JWT-based authentication for API endpoints"
status: "IN_PROGRESS"  # INBOX | READY | IN_PROGRESS | BLOCKED | REVIEW | ACCEPTED | CANCELLED
priority: 2  # 1=P1-Now | 2=P2-Next | 3=P3-Later
kind: "code"  # code | fix | closure | planning | product (refused otherwise)
scope: ["repo/api", "repo/auth"]  # resource keys for conflict detection
token_budget: 8000
tags: ["security", "auth", "api"]
depends_on: ["M-397006"]  # prerequisite ticket IDs
blocks: ["M-397015"]  # tickets blocked by this one
owner: "terminal-1"  # current worker
seed_key: "auth-jwt-v1"  # idempotent seeding key
created_at: "2026-10-21T14:30:00Z"
updated_at: "2026-10-21T15:45:00Z"
---

## Description
Implement proper JWT-based authentication for API endpoints with token issuance,
refresh rotation, revocation, and introspection (RFC 7662).

## Tasks
- [x] Design JWT token structure and claims
- [x] Implement token issuance endpoint
- [x] Add token validation middleware
- [x] Implement refresh token rotation
- [x] Add token revocation/blacklist mechanism
- [ ] Integrate with existing session management
- [ ] Add JWT configuration options (expiry, algorithm, issuer)

## Dependencies
- M-397001 (core infrastructure - completed)
- M-397006 (middleware framework - completed)

## Files Created
- `auth/jwt.py` — JWT token management
- `auth/middleware.py` — Validation middleware
- `auth/routes.py` — Route handlers (login, refresh, revoke, introspect, logout)
- `tests/test_auth.py` — Unit and integration tests

## Evidence Log
| Timestamp | Actor | Action | Evidence |
|-----------|-------|--------|----------|
| 2026-10-21T14:30:00Z | terminal-1 | started | Created JWT module with HS256/RS256 support |
| 2026-10-21T15:00:00Z | terminal-1 | checkpoint | Implemented token issuance + refresh rotation |
| 2026-10-21T15:30:00Z | terminal-1 | finished | All endpoints functional, tests pass |

## Acceptance Criteria
- [ ] POST /api/auth/login returns access+refresh token pair
- [ ] POST /api/auth/refresh rotates access token
- [ ] POST /api/auth/revoke invalidates token
- [ ] POST /api/auth/introspect returns RFC 7662 response
- [ ] All endpoints return proper RFC 7807 error responses
- [ ] 100% unit test coverage on auth module
```

---

## Ticket Lifecycle Stations

```
INBOX → READY → IN_PROGRESS → BLOCKED? → REVIEW → ACCEPTED → DONE
                    ↑           ↓
                    └───────────┘ (retry)
```

| Station | Entry Criteria | Exit Criteria | Gate |
|---------|---------------|---------------|------|
| **ready** | Ticket created, `scope` defined, `token_budget` set, dependencies met | `owner` assigned | — |
| **in_progress** | `start()` called, lease acquired | `checkpoint()` with evidence or `fail()` | Lease expiry auto-blocks |
| **blocked** | Lease expired, budget exhausted, or `fail()` called | `decide(retry)` or reconciliation | Lease renewal |
| **review** | `finish()` called with evidence | `decide(accept)` or `decide(retry)` | Human/operator gate |
| **accepted** | `decide(accept)` | Auto-transition to `done` | — |
| **done** | Immutable, shipped | — | — |
| **cancelled** | Operator cancelled the ticket | — | — |

---

## Status Values

The ledger stores the status in lowercase, as the table below names them.
A ticket is created `ready`: the ledger has no triage station, and a
station nothing can enter is decoration. Acceptance is the moment of the
decision rather than a state the ledger holds, so the stored status after
`decide(accept)` is `done`.

| Status | Meaning | Next Valid Transitions |
|--------|---------|------------------------|
| `ready` | Ready for work | → `in_progress` |
| `in_progress` | Active lease, work happening | → `blocked`, `review` |
| `blocked` | Lease expired, budget exhausted, or failed | → `in_progress` (retry), `cancelled` |
| `review` | Work finished, awaiting acceptance | → `accepted`, `in_progress` (retry) |
| `accepted` | Accepted by operator | → `done` |
| `done` | Immutable, shipped | — |
| `cancelled` | Explicitly cancelled | — |

---

## Evidence Requirements

| Station | Required Evidence |
|---------|-------------------|
| `checkpoint` | `summary`, `evidence` (diff/link), `next_step`, cumulative `tokens` |
| `finish` | Cumulative `evidence` (all checkpoints), final `summary` |
| `fail` | `category` (lease_expired\|budget\|tool\|dependency\|no_progress), `summary` |
| `decide(accept)` | Operator review of all evidence |
| `decide(retry)` | `reason` for retry (what changed) |

---

## Resource Scope & Conflict Detection

```yaml
scope: ["repo/api", "repo/auth"]  # Resource keys
```

- Scopes are **resource keys** (not descriptions)
- Two tickets with overlapping scopes **cannot** be `in_progress` simultaneously
- `blocked` tickets retain reservations until `reconcile()` or `decide(retry)`
- `decide(retry)` releases old reservation before new `start()`

---

## Token Budget Model

```yaml
token_budget: 8000  # cumulative across all attempts
```

- `tokens` in `checkpoint()` is **cumulative session count** (never decreases)
- Budget exhausted → `blocked` (cannot `start` new session)
- `decide(retry)` preserves cumulative tokens across retries
- Three consecutive checkpoints with **identical evidence hash** → `blocked` (no_progress)

---

## Failure Model

```yaml
category: lease_expired | budget | tool | dependency | no_progress
fingerprint: sha256(category:summary)[:16]  # stable grouping
```

- Failures auto-group by fingerprint
- Three consecutive identical evidence → `no_progress` failure
- Budget exhausted → `budget` failure (cannot `start` until `retry`)
- Lease expiry → `lease_expired` (preserves scope reservation)

---

## Acceptance Protocol

```
finish() → status=review
    ↓
operator reviews evidence
    ↓
decide(accept) → status=done      → DONE
decide(retry)  → status=ready     → IN_PROGRESS (new session)
decide(cancel) → status=cancelled
```

**Never** bypass the acceptance gate. `finish()` only creates `review`; only `decide(accept)` creates `done`.

`accepted` is the moment of the decision, not a state the ledger holds:
the gate opens, the work is accepted, and the ticket is stored as `done`.
Both the decision and the transition are recorded in the event log, as
`operator.accept` and `task.done`.

---

## CLI Interface (JSON-First)

```bash
# Task management
flux task-add M-397001 --objective "..." --acceptance "..." --scope repo/api --scope repo/auth --budget 8000
flux start M-397001 --owner terminal-1 --lease 900
flux checkpoint <sid> --summary "..." --evidence "..." --next "..." --tokens 1200
flux renew <sid> --lease 900
flux handoff <sid> --to terminal-2 --reason "going home"
flux adopt <sid> --owner terminal-2 --reason "the old worker is gone"
flux reconcile
flux fail <sid> --category tool --summary "Compiler missing"
flux finish <sid> --evidence "Reviewable diff abc123 and test output"
flux decide M-397001 accept --reason "Reviewed diff and acceptance evidence"
flux accept M-397001 --reason "..."
flux retry M-397001 --reason "..."
flux cancel M-397001 --reason "..."

# Context & packets
flux context-add M-397001 --file ./README.md --source "repo readme" --priority 10
flux pack M-397001 --max-bytes 12000 --packet-only
flux pack M-397001 --since sha256:<hash of a packet sent before>
flux benchmark M-397001 --root .

# Routing & profiles
flux profile-add local-coder --kind code --context-limit 16000 --cost zero_incremental --requires git
flux route M-397001 --required-context 8000

# Reading the ledger
flux status
flux list --status ready --compact
flux show M-397001
flux sessions --active
flux events --after 0 --type task.created
flux watch --after 0 --once
flux verify --signed

# GitHub sync
flux github-poll --repo owner/repo --sync-db ~/.flux/github.sqlite
flux github-status --repo owner/repo
flux github-publish --repo owner/repo --issue 123 --key summary --body "..."
flux github-reconcile --repo owner/repo
flux github-intake --repo owner/repo
flux github-cycle --config cycle.json

# Dashboard preview (local-only)
flux dashboard-preview --url http://127.0.0.1:8080 --status planned --offset 0
flux dashboard-intake --url http://127.0.0.1:8080

# Project files, adapters and the wordmark
flux init --project .fluxrecord
flux export --project .fluxrecord
flux read .fluxrecord/tickets/M-397001.md
flux import --file tickets.csv
flux tools --history
flux doctor --project .fluxrecord
flux token issue --subject terminal-1 --scope read
flux token verify <token>
flux token introspect <token>
flux art --style large --color
flux version
```

---

## Packet Addressing

Every packet carries `packet_hash`: the SHA-256 of its canonical JSON. The
same ticket at the same state always produces the same hash, so an
unchanged packet can be reused instead of sent again.

`flux pack <id> --since <hash>` returns only what changed since the
packet with that address — the checkpoints that landed, the failures that
were recorded, the context that was attached — and says so when nothing
changed. The full packet is still the truth; the delta is what is new.

### Budget Policy

A ticket's context budget comes from a policy by kind unless the caller
says otherwise: `code` 12000 bytes, `fix` 8000, `planning` 6000,
`closure` 4000, `product` 8000, anything else 12000. `--max-bytes`
overrides it.

### Tokenizer

A tokenizer is a plug-in, never a dependency. The core imports nothing to
measure tokens; a caller registers one and passes it to the ledger. When
one is registered, packets carry a `token_count`; when none is, the field
is absent and the byte budget is the authority.

## Coordination

A scope is reserved by the ticket that holds it: `start()` refuses a
ticket whose scope overlaps one that is `in_progress`, `blocked` or in
`review`. Two workers cannot hold the same work.

A lease changes hands deliberately, and the reason is recorded in the
event log:

    handoff(session, owner, reason)   the owner changes, the lease continues
    adopt(session, owner, reason)     the owner changes, the lease extends

Real-time updates need no server. The event log is a table in the same
file the reader already has open, so `flux watch` polls it: no socket, no
port, no origin check, and no new boundary.

## Shared Ledgers

The ledger is a file, so a shared volume already works — for one writer at
a time. Two machines writing to the same SQLite file over a network
filesystem is a different question, and the honest answer is that it does
not work: WAL mode needs shared memory that network filesystems do not
provide reliably, and the file locking SQLite relies on is not dependable
across them either. The supported arrangements are:

- **One ledger per machine.** Each worker keeps its own ledger, and the
  repositories are shared through the GitHub sync: observations in, marked
  comments out.
- **One ledger on a shared volume, one writer at a time.** A volume that
  is mounted by one machine, or by several that take turns, is fine. The
  scope reservation is what keeps two workers from taking the same work;
  it is enforced inside the database, so it holds for any arrangement the
  database itself supports.
- **Not a network filesystem.** NFS, SMB and similar are not supported
  for concurrent writers, and no configuration of fluxrecord makes them
  so.

The rule underneath all three: the ledger is the source of truth for
*that* ledger, and anything shared between ledgers travels as
observations and comments — never as a shared database file.

## Tamper-Evidence

The ledger is a plain SQLite file, so anyone with write access can change
a status. With a chain key, every event carries an HMAC-SHA256 digest of
the one before it, so an edit made after the fact is detectable:

    verify_chain() -> {"signed": true, "ok": false, "broken_at": 42}

The chain is off by default and the key never appears as an argument: it
comes from `FLUX_EVENT_KEY`, or from a file `FLUX_EVENT_KEY_FILE` names.
A ledger nobody signed is still a ledger, and `flux verify` says it cannot
be checked rather than implying that it was.

### What the chain proves, and what it does not

A chain is a prefix, so it proves the log from its first event up to the
last one it holds. The head of the chain is recorded beside the log and
compared on every check, so a log that has been truncated at the end is
detected as well:

    verify_chain() -> {"signed": true, "ok": false, "broken_at": null,
                       "head": "…", "note": "…no longer ends where it was
                       recorded to end…"}

What it proves is that the log has not been edited *by somebody who does
not have the key*. Without the key, an editor cannot compute a valid
digest for a changed event, so an edit in the middle, a deletion in the
middle, an insertion and a truncation are all visible.

What it does not prove is anything against somebody who has the key, and
the recorded head is in the same file as the log, so an editor who can
write to one can write to the other. The chain catches accidents and
casual edits. It is evidence, not protection.

---

## GitHub Sync Contract

### Observations (Read-Only)
```sql
observations(repo, kind, identity, revision, observed, summary, action, url)
```
- `kind`: `issues`, `comments`, `pulls`, `review_comments`, `releases`, `runs`, `commits`, `events`
- `action`: `investigate_failed_check`, `review_code_change`, `reconcile_ticket_change`, `review_external_feedback`, `verify_release_artifacts`, `inspect_repository_activity`

### Outbox (Write Path)
```sql
outbox(repo, issue, key, body_hash, state, url)
```
- `key`: Idempotency key (max 100 chars)
- `body_hash`: SHA-256 of body
- `marker`: `<!-- fluxrecord:{key}:{digest} -->` appended to body
- The namespace was `taskrivet` before the rename. That form is still
  read, so a comment published by an earlier version of this tool is
  recognised and never sent twice; it is never written.
- Reconciliation: reads comments, matches marker + actor, never blindly resends

---

## Ticket File Storage

`flux export` writes this layout. The ledger remains the source of truth;
these files are a projection of it, not a second copy of the state.

```
.fluxrecord/
├── tickets/
│   ├── M-397001.md
│   ├── M-397002.md
│   └── ...
├── index.json          # { "M-397001": { "title": "...", "status": "..." }, ... }
└── evidence/
    └── M-397001/
        ├── checkpoint-1.json
        ├── checkpoint-2.json
        └── finish.json
```

---

## Testing Requirements

| Layer | Coverage Target | Tools |
|-------|----------------|-------|
| Unit (pure functions) | 100% | `pytest` / `unittest` |
| Integration (API routes) | 90% | `pytest` + test HTTP client |
| Contract (external services) | 80% | Schema validation |
| Property-based | Key functions | `hypothesis` |
| Chaos | Worker failures | Custom harness |
| Visual regression | UI tabs | Playwright |

---

## Security Boundaries

| Boundary | Rule |
|----------|------|
| **Core** | No network, no shell exec, no telemetry, stdlib-only |
| **Adapters** | Declare supported versions, permission model, cancellation semantics |
| **Adapters** | No credentials in fixtures, synthetic only |
| **Dashboard** | Local HTTP only, no redirects, 8 MiB limit, 10s timeout |
| **GitHub** | `gh` CLI only, no token in code, 30s timeout |
| **Secrets** | Never in repo, injected via env/CSI at deploy |

---

## File Templates

### New Ticket Template
```markdown
---
id: "M-XXXXXX"
title: ""
status: "INBOX"
priority: 2
kind: "code"
scope: []
token_budget: 8000
tags: []
depends_on: []
blocks: []
owner: ""
seed_key: ""
created_at: "2026-10-21T00:00:00Z"
updated_at: "2026-10-21T00:00:00Z"
---

## Description


## Tasks
- [ ] 

## Dependencies
- 

## Files Created


## Acceptance Criteria
- [ ] 
```

### Checkpoint Evidence File
```json
{
  "session_id": "abc123",
  "sequence": 1,
  "summary": "Implemented token issuance endpoint",
  "evidence": "Diff abc123; POST /api/auth/login returns access+refresh pair",
  "next_step": "Implement refresh rotation",
  "tokens": 1200,
  "created_at": "2026-10-21T15:00:00Z",
  "evidence_hash": "sha256:..."
}
```

---

## The Wire Format

The JSON this package emits is versioned separately from the package, and
version 1 is frozen: the `ticket` and the `packet` may not change shape
without a wire format bump and a major version.
`docs/wire-format.schema.json` is the schema; `docs/identifiers.md` is
the identifier registry; `docs/breaking-changes.md` is the policy.

---

## Threat Model

`docs/threat-model.md` names the assets, the actors, the boundaries that
are enforced and checked, and the edges that are accepted rather than
solved. `docs/adr/0001-auth-is-a-library.md` records why the auth module
is a library and not a service.

---

## Governance

- **Standard evolves** via tickets against this repo
- **Breaking changes** require `M-XXXXXX` migration ticket
- **Schema version** in `meta` table
- **Migrations** are additive + idempotent

---

## License

MIT License — see [LICENSE](LICENSE)