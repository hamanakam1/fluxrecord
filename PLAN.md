# fluxrecord — the long-term plan

> **Less repeated context. More accepted work. Visible failures.**

This document says how fluxrecord should be built, in what order, and what
"finished" means at each step. It is written to be argued with: every claim
below is either a rule the code already follows, a gap the code has today,
or a decision that still has to be made.

**Snapshot:** 2026-10-22, package version 0.2.0, schema version 3.

**Status of this document:** living. It changes through the same ticket
lifecycle the software enforces on itself — a claim in here is a ticket, a
ticket is bounded context, and a plan nobody accepted is just prose.

---

## 1. What is being built

fluxrecord is a **standard first** and a **tool second**.

The standard is the shape of a ticket: a bounded context packet, an explicit
owner, evidence at every transition, a failure taxonomy, and a gate that only
a person can open. The tool is the reference implementation of that standard:
a local SQLite ledger, a JSON-first CLI, and a set of adapters that each
declare their own boundary.

That ordering matters. If the tool came first, the format would drift to
whatever the tool happened to do, and the tool's own bugs would become the
format. Building the standard first means the tool can be rewritten — and it
probably will be — without breaking anyone's workflow.

### The three promises, and what would falsify each

| Promise | What it means | What would falsify it |
|---|---|---|
| **Less repeated context** | A worker resumes a ticket without re-reading the repository or re-reading the conversation | The packet is larger than the thing it summarises, or there is no way to measure the saving |
| **More accepted work** | Work reaches DONE because a person accepted it, not because a timer expired | A ticket can reach DONE without a human decision, or the acceptance gate is routinely bypassed |
| **Visible failures** | A failure is a first-class record with a stable fingerprint, visible to the next worker | Failures live only in a log nobody reads, or their category is a free-text string |

Every phase below is judged against these three, not against feature count.

---

## 2. Architecture: the boundary is the product

The existing layering is right and should be defended, because it is the
thing that makes the three promises checkable.

```
┌──────────────────────────────────────────────────────────────┐
│ fluxrecord/__main__.py     JSON-first CLI. One verb per verb. │
│ stdout is data, stderr is error, exit code is the contract.   │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ Adapters — each declares its own boundary                     │
│   dashboard   loopback only, GET only, no redirects, 8 MiB, 10s│
│   github_sync gh CLI only, no token read or stored, 30s       │
│   tools       filesystem read only, never executes anything    │
│   auth        HS256 by hand, stdlib only, framework free     │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ fluxrecord/core.py — the Ledger                              │
│ stdlib only. No network. No shell. No telemetry.             │
│ SQLite + WAL, transactions, additive migrations, event log.  │
└──────────────────────────────────────────────────────────────┘
```

### The three rules that keep this honest

1. **The core never imports an adapter.** `core.py` may not import `github_sync`,
   `dashboard`, `tools` or `auth`. If it needs something from one of them, the
   adapter passes it in.
2. **An adapter never writes to the ledger except through the public
   `Ledger` API.** No direct SQL outside `core.py`, ever.
3. **The core is stdlib-only and stays that way.** `dependencies = []` is a
   feature, not an accident. A worker running `flux` on an air-gapped machine
   must not need to install anything.

Rule 3 is the one most likely to be broken by accident, because a JSON
Schema validator or a tokenizer is exactly the kind of dependency that seems
harmless. The resolution in §8 is a **plug-in boundary**, not a dependency:
optional, degrading to a documented default when absent.

### Where the boundary is currently blurry

| Blur | Why it matters | Proposed resolution |
|---|---|---|
| `core.py` is 750+ lines and holds the schema, the state machine, the query layer and the packet builder | Every new feature touches the same file, so every feature risks the core's guarantees | Split the *packet builder* and the *query layer* into their own modules; keep one writer to the database |
| `auth` has no consumer | A JWT library that nothing calls is a liability, not a feature | Decide in Phase 4: wire it to a bounded local service, or publish it explicitly as an embedder library |
| `files.py` writes markdown that a human reads, but the ledger is the truth | Two renderers of the same state can disagree | The projection must be reproducible from the ledger alone, and a test must assert that |

---

## 3. The build sequence

Six phases. Each has a goal, exit criteria that can be checked, and an
explicit list of what gets cut. A phase that cannot state its exit criteria
is not ready to start.

### Phase 1 — One truth · 0.2 → 0.3

**Goal:** the ledger is the only source of truth, and the specification and
the code say the same thing.

Today these disagree in four places, and every one of them is a promise the
project makes in public:

- **`accepted` never becomes `done`.** `decide(accept)` writes the status
  `accepted` and emits a `task.done` event, but the stored status never
  becomes `done`. The specification says acceptance auto-transitions to DONE
  and that DONE is immutable. Either the code transitions, or the
  specification stops promising it. The brand promise — *accepted work only* —
  lives in this transition, so it cannot stay ambiguous.
- **`INBOX` does not exist in code.** The specification's lifecycle starts at
  INBOX; `task-add` creates a ticket directly in `ready`. A station that
  nothing can enter is decoration.
- **Two metric keys are permanently null.** `measured_cash_cost` and
  `measured_token_savings` are always `null`. A key that is always null is
  worse than an absent key: it promises a number that will never arrive.
- **`flux status` has no summary.** It returns every ticket in full, which is
  a machine view. A human needs a board.

**Exit criteria**

- [ ] One status vocabulary, asserted by a test that reads the specification
      and the code and fails when they diverge.
- [ ] Every key in every JSON output is either populated or removed.
- [ ] `flux status --summary` (or a `flux board`) renders a compact table:
      one line per ticket, with status, owner, age, tokens and last evidence.
- [ ] A `SPEC.md` conformance test exists and runs in CI.

**Cut:** no new adapters in this phase. The GitHub cycle, the dashboard
preview and the auth routes are all feature-complete enough; the problem this
phase fixes is that the *promises* are not yet true.

---

### Phase 2 — Context economics · 0.3 → 0.4

**Goal:** make the first promise measurable. Today "less repeated context" is
a slogan; after this phase it is a number the tool reports.

The packet (`flux pack`) is the product. It already sizes itself exactly in
bytes and refuses to exceed the budget. What it cannot do is report a *token*
count, and it cannot tell you whether the packet you are about to send is
different from the one you sent last time.

**What gets built**

- **Content addressing.** Every packet gets a stable hash. The same ticket at
  the same state produces the same hash, so an unchanged packet can be reused
  instead of re-sent.
- **A delta view.** `flux pack --since <hash>` returns only what changed
  since that packet — the checkpoint that landed, the failure that was
  recorded. This is where the saving is actually made.
- **An optional tokenizer.** A plug-in interface, not a dependency: if a
  tokenizer is registered, `token_count` is populated; if not, the field
  stays absent and the byte budget remains authoritative. The core stays
  stdlib-only (§2, rule 3).
- **A budget policy.** Documented defaults per ticket kind, so `--max-bytes`
  stops being something every caller has to remember.

**Exit criteria**

- [ ] `flux pack` reports a packet hash, and re-packing an unchanged ticket
      yields the same hash.
- [ ] `flux pack --since` returns a delta and says so when there is none.
- [ ] A reproducible benchmark exists: the bytes (and tokens, when a
      tokenizer is present) a worker spends on a ticket today versus the
      bytes it would spend re-reading the repository. The number goes in the
      README, because an unmeasured claim is a marketing claim.
- [ ] The tokenizer plug-in degrades cleanly when no tokenizer is installed.

**Cut:** no model gateway, no prompt assembly library, no inference of any
kind. fluxrecord does not call models. That is what makes it usable as the
ledger *underneath* a model.

---

### Phase 3 — Coordination · 0.4 → 0.5

**Goal:** many workers, one ledger, no silent collisions.

Scope reservation already works: `start()` refuses a ticket whose scope
overlaps a ticket that is `in_progress`, `blocked` or `review`. That is the
mechanism that lets two agents work in the same repository without
overwriting each other. It stops at the edge of one ledger file.

**What gets built**

- **Lease handoff and recovery.** `flux adopt` lets a second worker take over
  a lease whose owner has gone away, and `flux handoff` lets a worker hand a
  lease to another deliberately. The current `reconcile()` expires a lease
  and blocks the ticket; that is the crash case, and it needs a deliberate
  counterpart.
- **A shared ledger.** The ledger is a file, so a shared volume already
  works. What is missing is a documented answer to the hard question: what
  happens when two machines write to the same SQLite file over a network
  filesystem. Either the answer is "it works, with WAL caveats, documented"
  or the answer is "it does not, and here is the supported alternative".
- **`flux watch` instead of WebSockets.** The roadmap currently lists
  "WebSocket real-time updates". That idea needs a server, a port, an
  origin check and a deployment story — all of which contradict a local-first
  CLI whose core promises no network. The event log is already a table in the
  same file the reader has open. `flux watch` tails that table: no socket, no
  server, no new boundary, and it delivers the same "the board changed" value.
  **Recommendation: replace the WebSocket item with `flux watch`.**

**Exit criteria**

- [ ] Two processes contending for the same scope: exactly one wins, and the
      loser is told which ticket holds the reservation and until when.
- [ ] A lease can be handed over deliberately and adopted after a crash, and
      both are recorded in the event log.
- [ ] `flux watch` shows a ticket moving, with no server process.
- [ ] The shared-ledger question has a written answer and a test.

**Cut:** no web dashboard, no hosted service, no multi-tenant anything.

---

### Phase 4 — Identity and tamper-evidence · 0.5 → 0.6

**Goal:** a ledger can be shared without being trusted.

Two distinct problems arrive with a shared ledger, and the current code
answers neither.

1. **Who is allowed to write?** `auth` implements JWT issuance, refresh
   rotation, revocation and introspection, and nothing calls it. The
   decision is whether the ledger gets a bounded local read/write service
   (loopback only, like `dashboard.py`) or whether `auth` stays a library
   for people embedding the ledger in their own service. Writing the ADR is
   the first task; implementing either side is the second.
2. **How would anyone know the ledger was edited?** The ledger is a plain
   SQLite file; anyone with write access can change a status. A signed event
   log — an HMAC chain over the events table, where each event carries the
   previous event's digest — makes a silent edit detectable. The core can do
   this with `hmac` and `hashlib`, which are already imported for failure
   fingerprints.

**Exit criteria**

- [x] An ADR choosing between the bounded local service and the library-only
      position, with the reasoning written down.
- [x] A threat model that names the assets, the actors and the mitigations —
      including the ones that are accepted rather than solved.
- [x] Optional event-chain signing, off by default, with a `flux verify`
      command that detects a modified event. The chain covers every event
      with an HMAC of the one before it, the head of the chain is recorded
      beside the log so a truncation is visible too, and
      `tests/test_chain.py` tampers with a ledger four different ways and
      checks that the chain notices each of them.
- [x] A secret-handling rule that survives contact with contributors: no
      credential in the repository, no credential in a fixture, every
      credential injected at run time.

**Cut:** no OAuth provider, no external identity service, no user accounts.
fluxrecord is not an identity system; it is a ledger that can tell you when
it has been tampered with.

---

### Phase 5 — The standard · 0.6 → 1.0

**Goal:** someone else can build a tool that interoperates, without reading
the source of this one.

This is the phase that makes the project a standard rather than a tool. It is
also the phase that most needs discipline, because the easiest thing in the
world is to reach 1.0 by renaming things and calling it a day.

**What gets built**

- **A frozen, versioned wire format.** The JSON shapes the CLI emits, plus
  the ticket file layout, published as a specification with a JSON Schema.
  From here, changing a field name is a breaking change.
- **A conformance suite.** A separate package, `fluxrecord-conformance`,
  that any implementation can run against its own ledger and pass. The suite
  is the standard's teeth.
- **An interop marker.** The idempotency marker
  `<!-- fluxrecord:{key}:{digest} -->` is an interop point, and it used to
  say `taskrivet`. The rename is done, with a dual-read window: the old
  namespace is still read, so a comment published by an earlier version is
  recognised and never sent twice.
- **The identifier registry.** The `M-` ranges in `SPEC.md` are a registry
  and should be published as one, with the allocation rule.
- **A written breaking-change policy.** What a breaking change is, how it is
  announced, how long the old form is read, and what "deprecated" means.

**Exit criteria**

- [x] `fluxrecord-conformance` exists and passes against the reference
      implementation. `conformance/run.py` runs a fixed sequence of commands
      against an implementation — this one or anybody else's — and checks
      every report against the published schema. It knows nothing about
      fluxrecord's internals, which is what makes it a conformance suite
      rather than a test suite.
- [x] The wire format has a JSON Schema and a version number, and the CLI
      reports which version it speaks. The schema is *generated* from the
      code's shapes by `scripts/publish_schema.py` and checked against the
      real output with a real JSON Schema validator, so the artefact other
      people read cannot quietly describe the previous release.
- [x] The marker rename ships with a dual-read window and a migration note.
- [x] The breaking-change policy is published and has been followed at least
      once.

**Cut:** no plugin marketplace, no hosted API, no web anything. A standard
that requires a server to participate is a product, not a standard.

---

### Phase 6 — Beyond 1.0

Only once 1.0 is honestly reached. These are directions, not commitments:

- **Federation.** Several ledgers, one ticket id space, deterministic conflict
  resolution. The hard part is not the protocol, it is deciding what a
  conflict *means* for an acceptance gate.
- **A render plug-in surface.** Read-only output renderers — a table view, a
  kanban view — behind a documented, capability-limited contract. The
  constraint is the interesting part: a plug-in may read the ledger and
  print, and may do nothing else. No imports of the core's internals, no
  network, no execution of other plug-ins.
- **A served mode, if and only if Phase 4 chose one.** Not before.
- **Kubernetes and Helm** — only if a server component exists to deploy. As
  of this snapshot there is none, and a local-first CLI with no server has
  nothing to put in a chart. This item should be removed from the roadmap
  rather than deferred.

---

## 4. Workstreams

The phases are the order; the workstreams are the lanes. Several run at
once, and the constraint is that **a workstream may not widen a boundary**
without changing §2 first.

| # | Workstream | Now | Next | Later |
|---|---|---|---|---|
| A | **The ledger and the schema** | SQLite + WAL, additive migrations, event log, scope reservations | Settle the status vocabulary; `board` summary view; migrations for every schema change | Signed event chain (Phase 4) |
| B | **The packet** | Exact byte budget, incremental sizing, failures and the last checkpoint carried | Content addressing, `--since` deltas, optional tokenizer plug-in, budget policy | Cost accounting per ticket |
| C | **Routing and profiles** | Eligibility is computed; ranking is deliberately absent; execution is never authorised | Outcome evidence recorded per profile so ranking stops being a guess | Recommendation with a stated confidence |
| D | **Adapters** | GitHub observations + idempotent outbox; loopback dashboard preview; tool inventory | Declare a single adapter contract (capabilities, permissions, cancellation, versions) and write it down | A third-party adapter that works without patching fluxrecord |
| E | **Identity** | JWT by hand, stdlib only, framework free, RFC 7807 errors | The ADR: bounded local service, or library only | Tamper-evident event log |
| F | **The CLI** | JSON-first, one command per verb, usage errors are JSON, exit code is the contract | `flux watch`; a human-readable `--format table` alongside JSON | Shell completions; a `flux` man page |
| G | **The standard** | `SPEC.md`, a changelog, a ticket lifecycle that the project uses on itself | JSON Schema, conformance suite, published identifier registry | A version-negotiation handshake between implementations |
| H | **Quality and delivery** | Unit, property and integration tests; mypy strict; ruff; CI on 3.11/3.12; coverage reporting | Widen the matrix; pin the tool versions; publish signed artefacts; a release checklist that is a document, not a habit | Reproducible builds; an SBOM |

---

## 5. Quality gates

A phase is done when the gate below passes. The gate is deliberately
checkable by a machine, because a gate that needs judgement gets skipped.

| Layer | Target | Tool | Gate |
|---|---|---|---|
| Unit (pure functions) | 100% | `unittest` / `pytest` | blocks the merge |
| Integration (CLI, adapters) | 90% | `pytest` + subprocess harness | blocks the merge |
| Contract (external services) | 80% | Schema validation | blocks the release |
| Property-based | key rules | `hypothesis` | blocks the merge |
| Chaos | worker failure | custom harness | blocks the release |
| Visual regression | UI tabs | Playwright | only where a UI exists |
| Types | strict | `mypy --strict` | blocks the merge |
| Style | — | `ruff check` + `ruff format --check` | blocks the merge |
| Boundary | — | `ast` import-graph check | blocks the merge |
| Specification | — | the spec parsed and compared with the code | blocks the merge |
| Output shape | — | no key that is always null | blocks the merge |
| Documentation | — | a test that every command appears in the README | blocks the merge |

The property-based tests are the interesting ones, because they cover the
rules a worker depends on rather than the lines a developer typed: cumulative
tokens never decrease, the budget is enforced across retries, three
identical checkpoints are a failure, the packet never exceeds its byte
budget, and a ticket id can never walk out of the project directory.

**Two gaps, both closed.**

- **The matrix was too narrow.** CI ran 3.11 and 3.12 while the package
  supports `>=3.11` and the local interpreter is 3.14. The matrix now runs
  3.11, 3.12, 3.13 and 3.14, which is the range the metadata claims.
- **The tools were not pinned.** `mypy>=1.8` and `ruff>=0.1.0` moved under
  the build, and a formatter that reformats the tree between two runs turns
  every pull request red for a change nobody made. The development
  dependencies are now pinned exactly.

**And one gap that has been closed.** The specification and the code
drifted apart — the status vocabulary, the transition table, the failure
taxonomy, the ticket schema and the command list each drifted at least
once, and each drift was a case where the published standard described a
tool that did not exist. `tests/test_spec_conformance.py` now parses
`SPEC.md` and compares it with the code: the statuses the ledger can hold,
the statuses the specification defines, the transitions it names, the status
each operator decision produces, the failure taxonomy, the front-matter
fields, and the command list in both directions. The specification is the
product, and a product that is not tested is only a document.

**A rule about tests and screenshots.** The screenshots in `docs/screenshots`
are generated by `scripts/capture_screenshots.py`, which runs the real CLI
against a throwaway ledger and renders the output through a headless
browser. They are never hand-edited. If the CLI's output changes, the
screenshots are regenerated in the same commit — the same rule the ledger
enforces on work: evidence travels with the change, or it did not happen.

---

## 6. Versioning and release

- **Semver for the package, a separate counter for the schema.** The schema
  version moves when the database layout moves; the package version moves
  when the behaviour moves. A minor release may bump the schema; a schema
  bump never happens without a minor release.
- **Migrations are additive and idempotent.** Never a drop, never a rewrite
  in place. A migration is a file, it runs once, and running it twice is a
  no-op. This is already true and must stay true: it is what lets a user
  upgrade a ledger that a colleague opened last week.
- **A breaking change is a major version, and it is announced.** The
  `CHANGELOG.md` follows Keep a Changelog. The format is already in place.
- **A release is a checklist, not a habit.** Tag, build, publish to PyPI,
  verify the artefact installs, regenerate the screenshots, update the
  changelog, tag the specification if it changed. The current CI publishes on
  a tag push; the missing part is the verification step after it.
- **The specification versions with the software.** `SPEC.md` carries a
  version. A change to the wire format is a change to the specification and
  needs a migration ticket, as the specification already says of itself.

---

## 7. Security

### The threat model in one paragraph

The ledger is a local file. The realistic adversaries are a malicious
process on the same machine, a leaked credential, a hostile repository whose
content ends up in a packet, and a colleague who edits the ledger by hand.
Remote network attackers are mostly out of scope *because* the core makes no
network calls — which is a security property, not a limitation.

### Invariants that must never regress

| Invariant | Where it lives | How it is defended |
|---|---|---|
| The core opens no socket and runs no subprocess | `core.py` | An import check in CI: the core may not import `subprocess`, `socket`, `urllib` |
| JWT `alg` is read from the header, never trusted from it | `auth/jwt.py` | `alg: none` and algorithm confusion are refused; the algorithm is fixed to HS256 |
| Signature comparison is constant time | `auth/jwt.py` | `hmac.compare_digest` |
| `exp`, `nbf`, `iss` and `aud` are checked on every verification | `auth/jwt.py` | Property tests, not just unit tests |
| Every token carries a `jti` and can be revoked | `auth/jwt.py` | A revocation list; refresh tokens rotate and are spent once |
| A ticket id can never escape the project directory | `core.py`, `files.py` | The id is restricted by pattern; a test walks out of the directory |
| The dashboard reads loopback only, GET only, refuses redirects, caps at 8 MiB, times out at 10 s | `dashboard.py` | A test per boundary |
| The GitHub sync talks to the `gh` CLI only, never reads a token, times out at 30 s | `github_sync.py` | A test with a real subprocess |
| The tool inventory never executes anything | `tools.py` | It only stats the filesystem |
| No credential in the repository or in a fixture | everywhere | A secret scan in CI |

The one invariant that was stated but not mechanically enforced — "the core
opens no socket and runs no subprocess" — is now **checked**:
`tests/test_boundaries.py` builds the core's import graph from the source
with `ast`, walks the closure, and refuses a network or process module, an
adapter import, a dynamic import or an `exec`, and a write to stdout. The
check has its own tests, so a gate that cannot fail is caught before it
starts passing everything. It is a named step in CI, and the default is
"no": allowing a new module in the core means editing an allowlist in that
test, where the reason has to be written next to it.

### Disclosure

`SECURITY.md` already names the process. It needs one addition: a
**supported-versions** table, so that a report against an old version has a
clear answer.

---

## 8. Performance and budgets

Every budget is a number, a measurement point, and a place the number is
reported. A budget with no measurement point is a wish.

| Budget | Limit | Measured by | Reported in |
|---|---|---|---|
| Packet size | exact, never over `--max-bytes` | `flux pack` | `packet_bytes` |
| Packet growth | linear, not quadratic in packet size | benchmark in CI | benchmark output |
| Ledger write latency | < 50 ms for a checkpoint | benchmark | `flux status` |
| CLI startup | < 150 ms on a warm database | benchmark | — |
| Dashboard read | 8 MiB, 10 s, loopback only | `dashboard.py` | `boundary` block |
| GitHub call | 30 s, `gh` CLI only | `github_sync.py` | — |
| Tool scan | 500 entries, no execution | `tools.py` | `scan_capped` |

The two new measurements this plan adds are the ones that matter for the
brand: **bytes spent per ticket** and **bytes saved by the delta packet**.
Without them, "less repeated context" is unfalsifiable.

---

## 9. Interoperability

The ticket file layout is the interop contract today, and it is a good one:
plain markdown with front matter, diffable, reviewable, no proprietary
container. The plan adds three things to it:

1. **The ticket file is a complete projection.** Any reader should be able to
   reconstruct the ticket's state from the file alone, and the ledger should
   be able to reconstruct the file. There must be a test that both hold.
2. **The JSON shapes get a schema.** So a third-party implementation can
   validate its output against the standard rather than against this source.
3. **The id space is published.** `M-` plus the ranges in `SPEC.md` become a
   registry, so a third party can allocate ids that will not collide.

---

## 10. What this project will deliberately not build

A long-term plan is mostly a list of things not to build. Each of these has
been proposed, by someone reasonable, and each has a reason to be refused.

| Not building | Why |
|---|---|
| A web dashboard | A separate product with a separate boundary. fluxrecord is the ledger underneath it. The `dashboard.py` preview exists to *observe* a dashboard, not to become one. |
| A model gateway or an inference client | fluxrecord never calls a model. A ledger that also talks to models cannot be used *underneath* a model. |
| A hosted or multi-tenant service | The whole value is that the ledger is a file you own. |
| An identity system | `auth` exists to let an embedder protect *their* service, not to become one. |
| Plugin execution | A plug-in may read and print. It may not import core internals, reach the network, or run other plug-ins. |
| A ticket tracker with sprints, backlogs and burndown charts | Those are project-management features. A ticket here is a bounded context packet for one worker. |
| Cross-ledger federation before 1.0 | Federation before the format is frozen means federating two wrong shapes. |
| A CLI rewrite in Rust or Go | The core is stdlib-only Python because it must run anywhere a worker runs. Rewriting it for speed is optimising the wrong property: the ledger is not the bottleneck, the *context* is. |

---

## 11. Risks

| Risk | Likelihood | Impact | Mitigation | Early warning |
|---|---|---|---|---|
| The specification and the code drift apart again | High — it already has, three times in two weeks | High — the standard is the product | The conformance test from Phase 1, and a rule that a spec change needs a migration ticket | A spec change that lands without a test change |
| The event log grows without bound | Medium | Medium — the ledger gets slow and the files get large | A retention policy and a `flux compact` that folds events into a summary | A ledger that exceeds a few megabytes in normal use |
| The adapter set grows and the boundary blurs | Medium | High — the core's guarantees are the product | The import check in CI; one adapter contract in workstream D | An adapter importing `sqlite3` |
| Scope reservations turn out to be too coarse | Medium | Medium — two workers on the same file collide | Support hierarchical scope keys; document the convention | A real collision in the field |
| The project is used by one person and the standard is never exercised by another | Medium | High — a standard used by one person is a tool | The conformance suite forces a second implementation to exist | No third-party implementation in Phase 5 |
| Screenshot and documentation drift | Low now | Medium — trust | The screenshots are generated by a script in the same commit as the change | A screenshot that does not match the current output |
| Burnout: the project is a large surface for one maintainer | Medium | High — a stalled project is worse than a small one | Phases have exit criteria, not feature lists; a phase that is not next is not started | Two consecutive phases with no movement |

---

## 12. How this plan stays true

- **A claim in this document is a ticket.** Not a wish, not a note. It gets
  an id, an objective, an acceptance criterion and a budget, and it goes
  through the same gate as any other work. The project dogfoods the standard.
- **The plan is reviewed at the end of every phase**, and a phase review may
  cut, reorder or rewrite the phases that follow. Cutting is a success, not
  a failure: a plan that is never cut is not a plan.
- **The roadmap in the README is the summary; this document is the detail.**
  If they disagree, this document wins and the README is wrong.
- **Exit criteria are checked by machines wherever possible.** Where they
  cannot be, the check is written down as a question with a named answerer.

---

## 13. Open questions

These need a decision from the operator. Each is stated with a
recommendation, because a plan that only lists questions is a way of not
planning.

1. ~~**Does `accepted` become `done` in the database, or does the
   specification stop promising it?**~~
   **Settled: the code transitions.** `decide(accept)` stores `done`. The
   decision and the transition are both in the event log, as
   `operator.accept` and `task.done`, and `metrics.accepted` counts both
   spellings so a ledger written before the change still reports
   correctly. The specification already promised this; the code now keeps
   the promise.

2. ~~**Is `flux watch` better than WebSockets for real-time updates?**~~
   **Settled: `flux watch` replaces the WebSocket item.** The event log
   is already a table in the file the reader has open, so watching it
   needs no socket, no port, no origin check and no new boundary — and it
   delivers the same "the board changed" value. The WebSocket item is
   gone from the roadmap rather than deferred.

3. ~~**Should `auth` become a bounded local service, or stay a library?**~~
   **Settled: it stays a library.** ADR 0001 records the reasoning: a
   server contradicts the core's promise of a stdlib-only ledger with no
   network, and the package's value is that every part declares its own
   boundary. The auth module exists for embedders — people putting a
   ledger inside a service of their own — and the package ships the parts
   that are hard to get right, and none of the parts that belong to a
   deployment.

4. ~~**Is the `taskrivet` marker renamed to `fluxrecord` before 1.0 or
   after?**~~
   **Settled: before, and it is done.** A 1.0 that ships with a
   competitor's name in its wire format is a 1.0 that has to break later.
   The marker is now written as `<!-- fluxrecord:{key}:{digest} -->`, and
   the old namespace is still read, so a comment published by an earlier
   version is recognised and confirmed rather than sent twice. The rename
   has a dual-read window, which is what makes it safe to do before 1.0
   rather than after.

5. **What is the support window for Python versions?**
   *Recommendation:* 3.11–3.13, with 3.14 added to CI once it is the local
   default. The support range in `pyproject.toml` should match CI exactly.

6. ~~**Should the ticket file format or the JSON wire format be the frozen
   contract?**~~
   **Settled: the ticket file format, with the wire format alongside
   it.** The file is what a human reviews, it is already diffable, and it
   is the thing a third party would read. Its front matter is now a frozen
   shape of its own (`ticket_file`), validated against the real exported
   file, with its times as strings where the ledger keeps them as numbers
   — the file is a document, not a record. The JSON wire format is frozen
   alongside it, published as a generated JSON Schema, so the two cannot
   drift apart.

---

## Appendix A — Phase summary

| Phase | Version | Goal | Exit criteria in one line |
|---|---|---|---|
| 1 | 0.2 → 0.3 | One truth | The spec and the code agree, and every key is real |
| 2 | 0.3 → 0.4 | Context economics | The saving is measured and reported |
| 3 | 0.4 → 0.5 | Coordination | Two workers contend, one wins, both know why |
| 4 | 0.5 → 0.6 | Identity and tamper-evidence | The threat model is written and the log can detect an edit |
| 5 | 0.6 → 1.0 | The standard | A third-party implementation passes the conformance suite |
| 6 | 1.0 → | Directions | Federation, renderers, and a server only if earned |

## Appendix B — The first three tickets

Not a backlog. A starting point, in the order they should be taken.

**Done:** the first and the third.

1. ~~**Settle the status vocabulary.**~~
   Objective: `SPEC.md` and `core.py` agree on every status name, and the
   acceptance gate has a terminal state. Acceptance: a test reads the
   specification's status table and asserts it against the code; `done` is
   stored, immutable, and reachable only through `decide(accept)`.
   Scope: `repo/core`, `repo/docs`. Budget: 4000 tokens.
   **Shipped.** `INBOX` is gone — a station nothing can enter is
   decoration. `accepted` is the moment of the decision, not a state the
   ledger holds, so the stored status of accepted work is `done`. The
   specification says it, the code now does it, and
   `tests/test_spec_conformance.py` keeps them together.

2. ~~**Remove the keys that are always null.**~~
   Objective: every JSON key is either populated or gone. Acceptance: no
   output contains a hardcoded `null` that no code path can change; the
   metrics section either computes cash cost from the profiles or is removed.
   Scope: `repo/core`. Budget: 2000 tokens.
   **Shipped.** `measured_cash_cost`, `measured_token_savings`, `token_count`
   and `quality_estimate` are gone — none of them could ever carry a
   value. `tests/test_no_null_keys.py` exercises the ledger in every state a
   ticket can be in, collects every value every report produces, and fails
   if any key is null in all of them. A key that is null *sometimes* is
   fine; one that is null always is a promise the tool cannot keep.

3. ~~**Enforce the core's boundary in CI.**~~
   Objective: "the core opens no socket and runs no subprocess" is checked,
   not promised. Acceptance: a CI job fails if `fluxrecord/core.py` gains an
   import of `socket`, `subprocess`, `http`, `urllib` or `os.system`; the
   job is red when the import is added deliberately.
   Scope: `repo/core`, `repo/ci`. Budget: 2000 tokens.
   **Shipped** in `tests/test_boundaries.py`, with a named CI step.

---

*Less repeated context. More accepted work. Visible failures.*
