# Threat model

What fluxrecord protects, from whom, and what it has decided not to solve.

This is written to be argued with. Every mitigation here is either
implemented and checked by a test, or accepted and named as accepted.

## Assets

| Asset | Why it matters |
|-------|----------------|
| The ledger file | Holds every ticket, every piece of evidence, every failure |
| The event log | The history of what was decided, and by whom |
| The scope reservations | The thing that stops two agents overwriting each other |
| The acceptance gate | The record of what was actually accepted |
| The GitHub outbox | Comments written to a repository, on the operator's behalf |
| The signing secret | Lets anyone mint a token the package will accept |
| The chain key | Makes the event log tamper-evident |

## Actors

| Actor | What they want |
|-------|----------------|
| The operator | Their tickets, their evidence, and a ledger they can trust |
| A worker | The bounded context of one ticket, and nothing else |
| Another worker on the same ledger | A different ticket, and a different resource |
| A reader of the ledger | To know what was decided, and whether it was changed |
| Someone with the file | To read it, and possibly to change it |

## Boundaries that are enforced, not promised

These are checked by tests, not by documentation alone.

| Boundary | How it is enforced |
|----------|--------------------|
| The core never reaches the network | The core may not import a module that can; the import graph is checked |
| The core never runs a shell | The core may not call `os.system`, `exec`, `eval` or friends |
| The core never imports an adapter | An adapter passes what the core needs in, rather than the core reaching out |
| The dashboard reads loopback only | Non-loopback origins are refused before a request is made |
| The dashboard refuses redirects | A redirect is an error, not a follow |
| The dashboard reads at most 8 MiB | A larger response is clipped and reported as clipped |
| The GitHub sync talks to `gh` only | Fixed argv, no shell, 30 second timeout, 16 MiB response ceiling |
| No token is read or stored by the sync | The `gh` CLI holds the credentials; fluxrecord never sees them |
| A ticket id cannot walk out of a directory | Ids are plain identifiers, and every exported path is checked |
| Nothing is executed by the tooling inventory | The filesystem is read; nothing is run |
| An outbox comment is sent once | The idempotency marker is matched before anything is posted |
| A key never appears as an argument | Secrets come from the environment, or from a file it names |
| The auth module accepts one algorithm | HS256, read from the header and never trusted from it |
| Scopes come from the server | The credential verifier grants them; the request cannot ask |
| A refresh token is spent once | Rotation revokes the old one as it issues the new pair |

## Accepted, not solved

These are known and named. They are not bugs waiting to be fixed; they
are the edges of what a local-first ledger can promise.

**Anyone with write access can change the ledger.** File permissions are
the only access control, and on a shared volume that is not much. The
signed event chain (`flux verify`) makes an edit *detectable* after the
fact, not impossible. Preventing it needs a service, and
[ADR 0001](adr/0001-auth-is-a-library.md) explains why there is not one.

**The chain cannot prove a ledger was not truncated.** Deleting the last
event leaves a chain that verifies. A chain proves that what is there was
not changed; it does not prove that nothing is missing. The count of
events is the ledger's own claim, and a ledger that lies about it can lie
about that too.

**A worker with the ledger file can read every ticket.** There is no
per-ticket access control. The ledger is one file, and it is one
operator's work.

**The GitHub sync can publish anything it is asked to publish.** It holds
no credential of its own — `gh` holds that — but it will write a comment
with the operator's authority. The marker and the review step are the
controls; a determined operator can still publish something wrong.

**The dashboard preview reads whatever the dashboard serves.** The
boundary is on the request, not on the answer. The preview treats the
response as untrusted reference data, and says so in every packet that
carries it.

**A ticket id is not a secret.** The ids are sequential and guessable.
They are identifiers, not capabilities, and nothing in the package treats
them as secrets.

**Token revocation is only as good as the denylist.** The revocation list
is in memory in the current implementation. A process that restarts
forgets it; a ledger that stores revoked ids is the answer, and it is not
written yet.

**The budget is exact in bytes, not in tokens.** Without a registered
tokenizer, a packet's cost is known in bytes and only estimated in
tokens. The byte budget is the authority, and the packet says so.

## What is deliberately not modelled

- **Physical access to the machine.** Out of scope, and unfixable in
  software.
- **A malicious operator.** The operator can already read everything and
  can delete the ledger. The threat model protects the *record*, not the
  operator from themselves.
- **Side channels in the token implementation.** The tokens are signed
  with HMAC and compared with `compare_digest`; anything subtler than
  that is a research project, not a ledger's job.
- **The other tools on the machine.** fluxrecord checks that a tool
  exists. It does not check what the tool does.
