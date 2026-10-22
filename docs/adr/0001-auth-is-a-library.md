# ADR 0001 — The auth module is a library, not a service

**Status:** accepted
**Date:** 2026-10-22
**Context:** `fluxrecord.auth` implements JWT issuance, refresh rotation,
revocation and introspection. Nothing in the package calls it except the
`flux token` commands, which exist for an operator. The question this
record answers is whether the package should grow a service of its own.

## The decision

`fluxrecord.auth` stays a **library**. fluxrecord does not ship a server.

An embedder that wants a protected service writes one, using the
`AuthRoutes` dispatcher, and keeps it inside its own boundary. The
package ships the parts that are hard to get right — the token rules, the
rotation, the revocation, the RFC 7662 and RFC 7807 shapes — and none of
the parts that belong to a deployment.

## Why

**A server contradicts the core's promise.** The core ledger is stdlib
only: no network, no shell, no telemetry. A service means a port, an
origin check, a TLS story, a process to supervise and a story about
authentication for the service itself. Every one of those is a decision
fluxrecord should not be making on the operator's behalf.

**The service would need its own boundary, and the boundary is the
product.** The package's value is that each part says what it will and
will not do. A bundled service would be the one part with no declared
boundary, and it would be the part most likely to be exposed.

**The callers are not us.** The dashboard, the GitHub sync and the tooling
inventory are all adapters with their own boundaries. None of them needs
JWT from fluxrecord — the dashboard adapter can carry a bearer token the
operator supplies, and the sync uses the `gh` CLI, which has its own
authentication. The auth module exists for *embedders*: people putting a
ledger inside a service of their own.

**A library can be wrong in fewer ways.** The failure modes of a token
implementation are subtle — `alg: none`, algorithm confusion, rotation
that does not actually rotate, introspection that lies about an expired
token. Those are worth getting right once, in a module with no
deployment story attached to it.

## What this costs

- An embedder writes the service. That is a real cost, and it is the
  point: the deployment belongs to whoever owns the deployment.
- There is no built-in way to protect a ledger file. The answer today is
  the **signed event chain** (`flux verify`), which detects an edit after
  the fact rather than preventing it. That is a weaker guarantee than
  access control, and this record says so plainly.
- There is no multi-user ledger yet. When there is one, it will be a
  separate package that depends on this one, not a mode of this one.

## What would change this

If a service turns out to be the common case — if most embedders are
writing the same loopback server — then the honest move is to publish
that server as its own package with its own boundary, and to keep
`fluxrecord.auth` as the library it depends on. The decision to keep the
library pure would not change; the packaging would.

## What it looks like in the code

- `fluxrecord/auth/jwt.py` — issue, verify, introspect. No framework.
- `fluxrecord/auth/middleware.py` — bearer extraction, scope checks.
- `fluxrecord/auth/routes.py` — a dispatcher over
  `(method, path, body, headers)` that returns a response object.
- `fluxrecord/tokens.py` — the shell's way in: `flux token issue|verify|introspect`.
- `fluxrecord/secrets.py` — where the signing secret comes from, so that
  no secret ever appears as an argument.
