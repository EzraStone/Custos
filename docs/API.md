# Control plane API

Three consumers: the collector, the CLI, and eventually the console. Small
enough to read in full, and deliberately not self-documenting — the OpenAPI
schema and interactive docs are disabled, because a public schema browser on a
security product is an invitation nobody asked for.

Base URL is whatever you deployed. All requests need `Authorization: Bearer
<token>`.

## Authentication

A token names exactly one account. It is not a user identity: there are no
roles, no sessions, and no notion of a person.

That distinction matters for one endpoint. Granting imprimatur takes an
`operator` in the request body rather than reading it from the token, because
the token authenticates a machine and SEC-17 requires that a person granted the
authority.

Missing and wrong credentials return identical `401` responses. Telling them
apart helps an attacker enumerate and helps a legitimate operator not at all.

---

## `GET /healthz`

No authentication. Returns liveness plus the two revisions that decide what a
finding means:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "catalogue_revision": "2026-08-18",
  "prices_revision": "unverified-placeholder"
}
```

`catalogue_revision` is the model-endpoint list. A provider added after that
date is not recognised, and an agent using only that provider will not appear
in any finding. `prices_revision` reading `unverified-placeholder` means every
spend figure is order-of-magnitude only.

## `POST /v1/batches`

Ship one collection window. This is the only endpoint the collector calls.

Idempotent on `(account_id, window_start, window_end)`. The collector retries
with bounded backoff, so the same window genuinely arrives twice; a redelivery
replaces the batch rather than adding traffic, and the response says
`duplicate: true`.

**Refuses:**

| Condition | Status |
|---|---|
| Token names a different account than the batch | `403` |
| More than 2,000,000 flow records | `413` |
| `window_end` at or before `window_start` | `422` |
| Any field not in the schema | `422` |

That last one is deliberate. Ignoring unknown fields would let a modified
collector ship anything and have it silently dropped, which reads as compliance
while being nothing of the kind.

```json
{
  "batch_id": 12,
  "scan_id": 12,
  "duplicate": false,
  "agents_found": 5,
  "review_candidates": 2,
  "delivered": 3,
  "coverage_note": "no load balancer access logs, so low-volume agents surface for review rather than as findings"
}
```

`delivered` counts findings sent to configured channels. Zero is normal — no
channels configured, or nothing new to say since the last scan. A delivery
failure never affects the `202`: the batch was accepted and the findings are in
the register regardless of whether anyone was told.

`coverage_note` names what the scan could not see. A scan that found nothing
because it was blind is not a clean account, and the response has to say which.

## `GET /v1/register`

Takes `?account=<id>` when the token covers several accounts, and refuses with
`400` rather than guessing. Defaulting to one of them would attribute one
account's findings to another — quietly, and in the direction that makes a
report wrong rather than empty. A requested account the token does not cover is
`404`, not `403`, so a credential cannot be used to enumerate which accounts
exist.

The register for the account, worst first — by blast radius, then
reach surface, then confidence. An unsanctioned agent that can write to
production outranks a dozen read-only ones regardless of classifier confidence.

`?unsanctioned_only=true` returns the set that regenerates on every scan.

Each agent carries its `evidence`: the sentences the classifier produced. A
finding without them is a score, and a score is what the workload's owner will
argue with instead of the facts.

## `GET /v1/accounts`

The accounts this credential covers.

```json
{ "accounts": ["111111111111", "222222222222"] }
```

A fleet token names several, and every other route then requires `?account=`
to say which. Without this endpoint a client could only discover that fact by
making a request that fails, and could only discover *which* accounts by
parsing them out of the prose in the resulting `400` — which would make
rewording an error message a breaking change.

Scoped to the credential, not global. A token covering one account does not
learn that another exists.

## `GET /v1/scans`

Scan history, newest first, with coverage and truncation per scan.

## `GET /v1/diff`

What changed between the two most recent scans.

```json
{
  "account_id": "447120043318",
  "previous_scan_id": 11, "current_scan_id": 12,
  "headline": "2 new agents since the last scan; 1 gained permissions that increase what it could destroy.",
  "changes": [
    { "kind": "blast_radius_increased", "agent_id": "agt_...",
      "principal": "arn:aws:iam::447120043318:role/finance-close",
      "detail": "finance-close can now delete objects it could previously only read",
      "owner_team": "finance", "blast_radius": "destructive" }
  ]
}
```

Ordered by consequence, not recency. Unchanged agents are omitted: they exist
in the comparison so every agent is accounted for, and shipping them would make
the caller filter out the majority of a large response to find the few that
moved.

One scan is not an error. It is the normal state of a new account and returns
an empty `changes` with a headline saying so, rather than a `404` every client
has to special-case in its first week.

## `POST /v1/agents/{id}/imprimatur`

Sanction an agent. **The only path to `sanctioned` in the entire system.**

```json
{ "operator": "ezra@custos.dev", "approved_tools": ["billing-api"] }
```

`operator` is required and must be a human identity. Omitting `approved_tools`
or `approved_data` scopes the grant to what was observed — an operator
approving an agent is approving what it was seen doing, and widening that is a
separate deliberate act.

Returns `409` if the agent is retired, `404` if it belongs to another account.

## `POST /v1/agents/{id}/status`

Move an agent between `discovered`, `pending_review`, and `retired`.

Returns `409` for `sanctioned` with a message pointing at the imprimatur
endpoint. There is one door and this is not it.

Retiring revokes any existing grant.

## `GET /v1/agents/{id}/audit`

Every status change with the actor who made it, oldest first. This is the
answer to "why is this agent sanctioned", and it has no retention window.

---

## Errors

Standard FastAPI shape: `{"detail": "..."}`. Failures are deliberately
uninformative about credentials and specific about everything else — a reviewer
debugging a misconfigured collector should learn what is wrong, and someone
probing for valid tokens should learn nothing.
