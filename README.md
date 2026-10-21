# fluxrecord

```text
FFFFF  L      U   U  XX  XX RRRR   EEEEE CCCC  OOO  RRRR   DDDD
F      L      U   U   XXXX   R   R  E     C     O   O R   R  D   D
FFF    L      U   U    XX    RRRR   EEE   C     O   O RRRR   D   D
F      L      U   U    XX    R  R   E     C     O   O R  R   D   D
F      LLLLL  UUU     XX     R   R  EEEEE  CCCC  OOO  R   R  DDDD
```

**Less repeated context. More accepted work. Visible failures.**

---

fluxrecord is a **ticket standard** for local-first, evidence-driven coordination — built for people working across AI coding agents, CLIs, and terminal sessions.

> **Less repeated context. More accepted work. Visible failures.**

---

## Quick Start

```bash
# Initialize a fluxrecord project
flux init

# Create a ticket
flux task-add M-397001 \
  --objective "Implement JWT auth" \
  --acceptance "Tokens issued + refresh + revoke work" \
  --scope repo/api --scope repo/auth \
  --budget 8000

# Start work (acquires lease)
flux start M-397001 --owner terminal-1 --lease 900

# Record progress (cumulative tokens)
flux checkpoint <session_id> \
  --summary "Implemented token issuance" \
  --evidence "Diff abc123; POST /api/auth/login works" \
  --next "Refresh rotation" \
  --tokens 1200

# Finish (requests review)
flux finish <session_id> --evidence "All endpoints tested; diff abc123"

# Operator accepts → DONE
flux decide M-397001 accept --reason "Reviewed diff and tests"
```

---

## Core Principles

| Principle | Practice |
|-----------|----------|
| **Bounded context** | Every ticket carries objective, acceptance, scope, budget |
| **Explicit ownership** | `start()` acquires lease; expiry blocks until reconciliation |
| **Evidence at every step** | `checkpoint()` requires summary + evidence + next step + cumulative tokens |
| **Acceptance ≠ execution** | `finish()` → REVIEW → operator `decide(accept)` → DONE |
| **Failures are first-class** | Categories, stable fingerprints, recurrence tracking |
| **Accepted work only** | `decide(accept)` → DONE; nothing ships without review |

---

## Quick Reference

### Ticket Lifecycle
```
INBOX → READY → IN_PROGRESS ↔ BLOCKED → REVIEW → ACCEPTED → DONE
                              ↑    ↓
                              └─── retry (preserves tokens)
```

### Key Commands
| Command | Purpose |
|---------|---------|
| `flux task-add <id> --objective "..." --acceptance "..." --scope repo/x` | Create ticket |
| `flux start <id> --owner <name> --lease 900` | Start work (acquire lease) |
| `flux checkpoint <sid> --summary "..." --evidence "..." --next "..." --tokens <n>` | Record progress |
| `flux fail <sid> --category tool --summary "..."` | Record failure |
| `flux finish <sid> --evidence "..."` | Request review |
| `flux decide <id> accept\|retry\|cancel --reason "..."` | Operator decision |

---

## Installation

```bash
pip install fluxrecord
# or
pip install git+https://github.com/hamanakam1/fluxrecord.git
```

Requires: **Python 3.11+**, `sqlite3` (stdlib)

---

## Architecture

```
fluxrecord/
├── fluxrecord/
│   ├── __init__.py
│   ├── core.py          # Ledger: tasks, sessions, checkpoints, contexts
│   ├── cli.py           # JSON-first CLI
│   ├── tools.py         # Local executable inventory
│   ├── dashboard.py     # Bounded GET-only ticket preview
│   ├── github_sync.py   # Durable GitHub observations + outbox
│   └── auth/            # JWT auth (routes, middleware, routes)
├── tests/
├── templates/           # Ticket templates
└── SPEC.md              # Full specification
```

---

## Core Concepts

### Ticket = Bounded Context
```yaml
id: "M-397001"
objective: "Implement JWT auth"
acceptance: "Tokens issued + refresh + revoke work"
scope: ["repo/api", "repo/auth"]
token_budget: 8000
```

### Session = Leased Work
```bash
flux start M-397001 --owner terminal-1 --lease 900
# → {session_id, lease_until}
```

### Checkpoint = Evidence + Progress
```bash
flux checkpoint <sid> \
  --summary "Implemented token issuance" \
  --evidence "Diff abc123; POST /api/auth/login returns pair" \
  --next "Refresh rotation" \
  --tokens 1200
```

### Finish → Review → Accept
```bash
flux finish <sid> --evidence "All endpoints tested; diff abc123"
# → status: review

flux decide M-397001 accept --reason "Reviewed diff and tests"
# → status: accepted → DONE
```

---

## Roadmap

- [ ] Core ledger (SQLite + WAL, transactions)
- [ ] JSON-first CLI (`flux`)
- [ ] GitHub sync (observations + outbox)
- [ ] Dashboard preview (bounded GET-only)
- [ ] JWT auth routes (login, refresh, revoke, introspect)
- [ ] WebSocket real-time updates
- [ ] Plugin system for custom tabs
- [ ] Kubernetes manifests + Helm chart

---

## Contributing

```bash
python3 -m unittest discover -s tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT — see [LICENSE](LICENSE)