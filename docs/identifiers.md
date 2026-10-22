# The identifier registry

Ticket ids are `M-<six digits>`, and the ranges below are a registry:
they say what a number is *for*, so two people working in the same
namespace do not both take the same slice of it.

| Range | Meaning |
|-------|---------|
| `M-000001` – `M-009999` | Core infrastructure |
| `M-010000` – `M-099999` | Feature work |
| `M-100000` – `M-199999` | Security and authorization |
| `M-200000` – `M-299999` | Observability and monitoring |
| `M-300000` – `M-399999` | Testing and quality |
| `M-400000` – `M-499999` | Developer experience |
| `M-500000` – `M-599999` | Interface and interaction |
| `M-600000` – `M-699999` | Enhancements |
| `M-700000` – `M-799999` | Data and analysis |
| `M-800000` – `M-899999` | Infrastructure and deployment |
| `M-900000` – `M-999999` | Experimental and archive |

## The allocation rule

1. **Ids are never reused.** A number that has been used stays used. An
   id that is cancelled is still spent: it is a record of something that
   happened.
2. **Ids are allocated in order.** The next id for a piece of work is the
   lowest unused number in the range for its kind of work. Allocation is
   `Ledger.next_ticket_id()`, which takes the highest id in the ledger
   and adds one.
3. **A range is a claim about the work, not about the person.** The
   number says what the work is about, so that a reader can tell a
   security ticket from a test ticket without opening it.
4. **An imported id keeps its number.** A backlog that arrives from a
   CSV keeps the ids it already has, so links to it keep working. Where
   two rows claim the same id, the importer reports the collision
   instead of resolving it silently — see `flux import`.
5. **The id is an identifier, not a capability.** Nothing in the package
   treats knowledge of an id as permission to act on the ticket.

## Where the numbers live

The allocation rule above is implemented by:

- `Ledger.next_ticket_id()` — the next free sequential id
- `Ledger.add_task()` — accepts an id, and refuses one that is taken
- `fluxrecord/importer.py` — keeps the ids a CSV already has, and
  reports the ones that collide
- `fluxrecord/intake.py` — allocates ids for work that came from a
  repository or a dashboard, and seeds them so an intake can be repeated
