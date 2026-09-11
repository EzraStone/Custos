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

`regions` is every region this agent has been seen in. It accumulates across
scans, because one scan covers one region and a role running in three is
discovered three times.

**The other figures cover all of them.** `est_monthly_spend_usd` is the sum
across regions and `tools` and `data_stores` are the union, both kept per
region and replaced per region — so a second scan of one region is a fresher
figure for that region rather than a correction of the whole. `blast_radius`
comes from IAM, which is account-wide by nature: a role that can delete a
bucket can delete it from anywhere it runs.

`region_reach` is the breakdown: what was resolved in each region. It is not
the union divided up. A region with empty lists is one where a scan saw this
agent and resolved no destinations at all — usually a flow log with no port
field — and a caller that renders that the same as a region the agent
genuinely touches nothing in is reporting a gap in the log as a fact about the
workload. Agents discovered before the breakdown existed have an empty one, so
an empty object means "not recorded" rather than "nothing anywhere".

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

Each scan also carries `scope_named` / `scope_total` and their ratio as
`scope_readable`: how many of the private destinations that scan saw could be
given a name rather than shown as an address. It is a different measure from
`coverage` and means a different thing. Low coverage says findings may be
missing; a low `scope_readable` says the findings are all present and the
approval decision on each one is a guess.

A scan that reached nothing internal reports `1.0`, not `0.0`. There is no
unreadable scope on a scan with no destinations.

`regions` is the region that scan covered. A scan is one region's window, so a
multi-region account's history is two interleaved series: rendered as one list
it looks like an account whose agent count halves and doubles every other week,
which is the shape of a real problem and here is the shape of two regions
taking turns. Both the console and `custos history` show the column only when
the list holds more than one region.

## `GET /v1/fleet`

One line per account this credential covers.

```json
{
  "accounts": [
    { "account_id": "447120043318", "agents": 12, "unsanctioned": 5,
      "destructive": 1, "last_scan": "2026-09-08T09:00:00+00:00",
      "coverage": 1.0, "scope_readable": 0.75, "reviews": 2,
      "gateway_questions": 0, "rates_verified": true },
    { "account_id": "209384756102", "agents": 0, "unsanctioned": 0,
      "destructive": 0, "last_scan": null, "coverage": null,
      "scope_readable": null, "reviews": 0, "gateway_questions": 0,
      "rates_verified": false }
  ]
}
```

A customer in the target profile runs five to fifty accounts. Somebody deciding
where to spend an afternoon needs to know which account has unsanctioned agents
that can destroy things and which has not been scanned in three weeks.

`destructive` is the number to triage by: twelve unsanctioned agents that can
only read is a different afternoon from one that can delete.

**Every account the credential covers appears, including unscanned ones.** A
`null` `last_scan` is the most important row in the response — an account
nobody has looked at is not the same as an account with nothing in it, and
omitting it would make the two identical.

## `GET /v1/rates`

What this account pays per provider, and when they last said so.

```json
{
  "account_id": "447120043318",
  "revision": "customer-supplied 2026-09-08",
  "verified": true,
  "current": { "anthropic": { "input_per_mtok": 1.5, "output_per_mtok": 7.5 } },
  "history": [ { "provider": "anthropic", "input_per_mtok": 1.5,
                 "output_per_mtok": 7.5, "supplied_by": "ezra@custos.dev",
                 "supplied_at": "2026-09-08T12:00:00+00:00" } ]
}
```

`verified` answers the question a reader with a budget asks first — is this our
rate — without making them interpret a revision string. An account that has
supplied nothing gets the built-in table and `"revision":
"unverified-placeholder"`.

`history` keeps every rate ever supplied. A figure in last month's report was
computed from the rate in effect then, and without the old row that report
cannot be explained.

## `POST /v1/rates`

```json
{ "provider": "anthropic", "input_per_mtok": 1.5, "output_per_mtok": 7.5,
  "operator": "ezra@custos.dev" }
```

Applies to the next scan. Existing figures are not recomputed: a report already
sent to somebody with a budget should still say what it said, and silently
restating last month's numbers at this month's rate would be worse than leaving
them alone.

A rate of zero is refused with `400`. It is far more likely an empty form field
than a free provider, and a zero would make every agent on that provider look
free — the one direction this figure must never be wrong in.

A provider with no supplied rate falls back to the built-in placeholder rather
than failing, so an account can price the provider it cares about and ignore
the rest.

## `GET /v1/reviews`

Workloads the classifier was unsure about in the last scan.

```json
{
  "account_id": "447120043318",
  "reviews": [
    { "principal": "arn:aws:iam::447120043318:role/nightly-doc-summariser",
      "confidence": 0.69,
      "evidence": ["Sent 2.1MB and received 890.0KB, a ratio of 2.4:1."],
      "unavailable": ["decoupling"],
      "scan_id": 12, "seen_in_scans": 7 }
  ]
}
```

Not agents and not findings. SEC-17 keeps them out of the register: the
classifier saying "this might be an agent and I am not confident enough to say
so" is not a claim anything downstream should act on.

**There is no path from here into the register.** Promoting a maybe by hand is
what the register is not for, and a route that allowed it would make every
guarantee about how an agent got there conditional on nobody having used it.

`seen_in_scans` is what makes this worth reading. A workload uncertain once is
one uncertain window; one uncertain in every scan for a month is a different
thing, and the count is the only way to tell them apart.

A workload with no recognised model traffic at all does not appear here. Its
model-traffic signals are unavailable rather than zero, so it is not scored —
see `GET /v1/gateway-candidates`, which names the workloads reaching each
undeclared address. That is the surface for a workload we cannot see, and it
asks a question rather than reporting a low confidence.

`unavailable` names the signals that could not be evaluated — usually the
decoupling signal, when the account has no load balancer access logs. A
workload in the review band for that reason is one we could have classified
with better input, which is a different problem from one that is genuinely
ambiguous.

## `GET /v1/gateway-candidates`

Internal destinations that behave like model endpoints.

```json
{
  "account_id": "447120043318",
  "candidates": [
    { "address": "10.0.7.40", "egress": 54400000, "ingress": 12800000,
      "principals": ["arn:aws:iam::447120043318:role/agent-via-gateway"],
      "blind_principals": ["arn:aws:iam::447120043318:role/agent-via-gateway"],
      "question": "10.0.7.40 received 54.4MB from a workload that never reaches a model provider we recognise, and returned 12.8MB — a ratio of 4.3:1. Is it a model gateway?",
      "interleave": 0.98,
      "scan_id": 12 }
  ],
  "declined": 5
}
```

**Questions, not findings.** Every entry is an address a workload sends far
more to than it gets back, reached by workloads that never talk to a model
provider we recognise — which is either a gateway nobody mentioned or an
unusually chatty internal API. A person decides which, and their answer goes
to `POST /v1/endpoints`.

Nothing here is classified as anything. A heuristic that promoted an internal
address to a model endpoint on its own would manufacture agents out of any busy
internal service, and the first false positive of that kind costs more trust
than every true one earns.

Candidates already covered by a declaration are omitted: somebody who answered
last week should not be asked again on every scan.

Drawn from the most recent scan that produced any, not the most recent scan. A
gateway that was quiet for an hour is still a gateway, and an empty list
because nothing used it reads as "we looked and there is nothing" — a different
and much more reassuring claim.

`interleave` is how often the workloads reaching an address also reached
another internal service in the same minute. It is the half of the evidence
that decides: "sends far more than it receives" describes a backup service
too, and what makes a gateway the likely answer is that the workloads using it
are running a loop.

`declined` counts internal destinations that had the same traffic shape and
were **not** asked about, because the workloads reaching them reach nothing
else — a log collector, a backup agent, a metrics pusher. The rule can be
wrong: a gateway that also proxies its workload's tool calls would be the only
destination that workload reaches. So an empty `candidates` list with a
non-zero `declined` is a different claim from an empty list with zero, and a
client that renders both as nothing is reproducing the exact silence this
endpoint exists to break.

## `GET /v1/endpoints`

Model endpoints this account has declared.

```json
{
  "account_id": "447120043318",
  "endpoints": [
    { "id": 1, "value": "10.0.7.0/24", "kind": "range", "note": "llm-gateway",
      "region": "us-east-1", "declared_by": "ezra@custos.dev",
      "declared_at": "2026-09-02T10:00:00+00:00",
      "active": true, "withdrawn_by": "", "withdrawn_at": null }
  ]
}
```

`?include_withdrawn=true` returns the ones no longer in effect as well.

A customer running every model call through an internal gateway has agents we
cannot see: their model traffic looks like traffic to an internal API, so the
workload has no model traffic at all and is not a finding of any kind. This is
how they tell us.

## `POST /v1/endpoints`

Declare a model endpoint.

```json
{ "value": "10.0.7.0/24", "kind": "range", "operator": "ezra@custos.dev",
  "note": "llm-gateway", "region": "us-east-1" }
```

`kind` is `range` — a CIDR or a single address — or `aws_service`. `operator`
is required and must be a human identity: declaring an endpoint changes what
the classifier considers an agent, which makes it the second decision in this
system with that property.

**Takes effect on the next scan, not retroactively.** Reclassifying stored
telemetry would rewrite the history of what was found when, and a register
whose past changes underneath an operator is one they cannot reason about. The
response says `"effective": "next scan"` rather than leaving anyone to wonder
why the list did not move.

Declarations are scoped to the account. 10.0.0.0/8 is where every customer's
internal services live, and one that leaked between accounts would manufacture
agents out of unrelated traffic on a coincidental collision.

**A private range is scoped to a region as well, and is refused without one.**
The same collision happens inside a single account: 10.0.7.40 is the model
gateway in us-east-1 and, in eu-west-1, whatever that account runs at that
address. Declaring it everywhere turns ordinary internal traffic into model
traffic — which does not hide agents, it invents them.

A public provider range needs no region and applies everywhere, because it
means the same thing everywhere. A gateway candidate carries the region it was
asked about, which is what a caller answering one should send back.

Returns `400` for an unparseable range or a missing operator. Validated on the
way in rather than at the next scan, because a declaration a customer believes
is in effect while their agents stay invisible is the exact failure this exists
to prevent.

## `DELETE /v1/endpoints/{id}`

Withdraw a declaration. Requires `?operator=`.

The row is marked, never deleted. Declaring can only make more traffic classify
as model traffic; **withdrawing can make a finding disappear**, which is why
the record of who did it survives — and why that matters most in precisely the
case where someone withdraws one to make a finding go away.

Returns `404` if there is no active declaration with that id in this account.

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

## `GET /v1/agents/{id}/drift`

How one agent's behaviour compares with its own history.

```json
{
  "agent_id": "agt_...",
  "observations": 14,
  "drift": [
    { "kind": "new_tool", "observed_at": "2026-08-20T09:00:00+00:00",
      "question": "finance-close reached rds 10.0.9.45 for the first time. Is that expected?",
      "detail": "finance-close reached rds 10.0.9.45 for the first time",
      "region": "us-east-1" }
  ],
  "baseline": { "tools": ["billing-api 10.0.4.21"], "observations": 13, "established": true }
}
```

Per agent rather than per account. Drift is a question put to one workload's
owner, and an account-wide list of those is a list nobody owns.

`question` is the phrasing to show. Every drift finding is put as a question
because a question gets answered and an accusation gets argued with.

`region` is which deployment drifted, and there is one baseline per region. An
agent's behaviour in us-east-1 is a trend; its observations in two regions
interleaved are two trends sampled alternately, and the step between them reads
as a change in the workload — most visibly on the first scan of a second
region, where every internal service that region uses is one the agent has
never been seen reaching. A client that renders the finding without the region
sends its owner looking in every deployment they run.

`baseline.tools` is the union across regions and `baseline.established` is true
when any one region has enough history. Findings are reported only from the
regions that do, so a region scanned twice contributes none.

`baseline.established` says whether there is enough history for any of this to
mean something. A client that renders drift from an unestablished baseline is
showing noise with a confident label on it. An agent seen once has no baseline
and returns an empty list — the normal state of a new finding, not an error.

## `GET /v1/agents/{id}/audit`

Every status change with the actor who made it, oldest first. This is the
answer to "why is this agent sanctioned", and it has no retention window.

---

## `GET /v1/report`

The current register as the HTML report a customer reads. Same document
`custos scan --out` writes, rendered from what is in the database now rather
than from one scan.

Served rather than only written to a file because from the second scan onward
someone wants a link rather than an attachment, and an attachment that has to
be re-sent every week is a report that stops being sent.

It needs the credential in a header like every other route, so it cannot be
opened by pasting the URL into a browser. The console fetches it and opens the
result; a client wanting a shareable artefact should save the response.

## Errors

Standard FastAPI shape: `{"detail": "..."}`. Failures are deliberately
uninformative about credentials and specific about everything else — a reviewer
debugging a misconfigured collector should learn what is wrong, and someone
probing for valid tokens should learn nothing.
