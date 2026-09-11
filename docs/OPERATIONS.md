# Running a scan

The whole loop, in the order it actually happens.

## 1. The customer applies the Terraform

One apply, read-only role, no compute created. `collector/README.md` is the page
they read; it targets thirty minutes without a meeting.

They send back the role ARN. That is the only thing needed from them.

## 2. Run the collector

```
export CUSTOS_ROLE_ARN=arn:aws:iam::447120043318:role/custos-discovery
export CUSTOS_EXTERNAL_ID=<the value you gave them>
export AWS_REGION=us-east-1
export CUSTOS_ACCOUNT_ID=447120043318
export CUSTOS_FLOW_LOGS=/aws/vpc/flowlogs          # or s3://bucket/prefix
export CUSTOS_REGIONS=eu-west-1,ap-south-1         # every region they run in
# With more than one region, point at the bucket alone: both log types are
# delivered per region and the per-region path is derived.
export CUSTOS_ACCESS_LOGS=s3://their-alb-logs
export CUSTOS_ACCESS_LOGS=s3://their-alb-logs/AWSLogs/...   # worth asking for
# Only if they pointed us at a log they already had, and only for CloudWatch:
export CUSTOS_FLOW_LOG_FORMAT="${version} ${account-id} ${interface-id} ..."

CUSTOS_DRY_RUN=1 ./custos-collector > batch.json
```

Dry run first, always. It prints the literal batch and sends nothing, and
walking a customer through that output is the fastest way through a security
conversation.

**Ask for the access logs.** Without them recall drops from 100% to 60% on our
corpus, and the agents missed are the low-volume ones — which are usually the
ones they most want to know about. The ask is easier with that number attached.

**Ask which regions they run in, and read the `other regions` line from
`--check`.** An account is a region-by-region thing: a workload in eu-west-1
has its own flow logs and no representation at all in a scan of us-east-1. One
collector covers the list, shipping a batch per region, and the report names
the region it covered.

The failure to avoid is the reassuring one. A first report from one region of a
three-region estate finds two agents, says nothing alarming, and is correct
about a third of the account.

**Do not insist on our flow log.** The Terraform module creates one in the
format Custos was built around, and it is the best case. It is also a second
copy of their traffic billed per gigabyte and a second change request, and
either is a reason to stall that has nothing to do with the product. If they
already have flow logs, point at those.

For S3 that needs nothing: AWS writes the field names at the top of every
object. For CloudWatch, ask them for the format string they gave AWS and set
`CUSTOS_FLOW_LOG_FORMAT`.

Then read the `flow log fields` line from `--check`. It says exactly what their
format costs — no ports means no MCP servers in the register, no direction
field means it is inferred from each interface's address. Say those out loud in
the first call rather than letting them read it off a thin report a week later.
A gap in their log that looks like a finding about their account is the one
mistake this product cannot recover from.

## 3. Scan it

```
custos --db acme.db scan batch.json --out acme-report.html
```

Exits non-zero when unsanctioned agents were found, so this composes with a
cron that should page someone.

No server needed for a first scan. Deploy the API when a customer wants
continuous monitoring, not before.

## 4. Read the report before they do

Check three things:

**Coverage.** If the banner is present, the scan did not see the whole account
and the findings mean less. Fix that before sending it — usually a wider window
or a flow log group that was not the one carrying the traffic.

**Attribution.** Findings in the "unattributed" section have no owner and
nobody will action them. If that section is large, the customer's tag hygiene
is the problem to solve first, and saying so is more useful than sending a list
they cannot route.

**The headline.** If it says "0 unsanctioned agents", stop and work out why
before concluding the account is clean. A misconfigured flow log group produces
exactly that.

## 5. Scan again next week

The second scan is where the subscription argument lives:

```
custos --db acme.db scan batch-2.json --out acme-report-2.html
custos --db acme.db diff --account 447120043318
```

The report leads with what changed. A report that repeats last week's findings
verbatim gets skimmed the second time and deleted the third.

---

## Continuous operation

### Deploy the control plane

See `deploy/README.md`. One process, one SQLite file, one token per account.

### Schedule the collector

Whatever the customer already uses — an ECS scheduled task, a Lambda on a rule,
a cron on a bastion. It needs the role and an endpoint, and it ships one window
per run — one batch per region it covers.

Set `CUSTOS_REGIONS` to every region `--check` reported flow logs in. One
process covering three regions ships three batches per window and holds its
cursor if any of them fails, so a region that never shipped is retried rather
than skipped. Running one process per region works too and is what a customer
with separate deployment pipelines will do; the control plane keys a batch on
(account, region, window) either way.

### Upgrading the control plane

Stop it, replace the binary or image, start it. The schema migration runs on
open: additive columns are applied to the existing tables, and the one
structural change so far — region becoming part of a batch's key — rebuilds
that table in a transaction and keeps the row ids, so historical scans still
resolve. Take a copy of the SQLite file first anyway; it is one file.

### Hand them the console

The control plane serves it at `/`. Give the operator their account's token and
their own name — the name is not a credential, it is what goes in the audit
trail against every decision they make.

What they can do there:

- **Read the register**, ordered by what each agent could destroy rather than by
  how confident the classifier is. Filter by blast radius, or search for a role,
  a team, or something an agent reaches.
- **Read the evidence** behind any finding. The grant control stays disabled
  until they have opened it, deliberately.
- **Grant imprimatur**, which is the only action in the system that confers
  authority. The scope is shown before it is granted.
- **Retire an agent** that no longer exists, with a reason. This is the one that
  keeps the queue readable: a decommissioned workload nobody retires keeps
  surfacing as a finding forever, and a queue full of dead roles is a queue
  nobody reads.
- **See what changed** since the last scan, which is the whole argument for
  scanning twice.

What they cannot do there: start a scan, change a token, or move an agent
directly to sanctioned. The console reads the register and records decisions;
it does not drive collection.

### Schedule pruning

```
custos --db acme.db prune          # weekly is plenty
```

Never touches agents or audit entries, so it is safe unattended.

### Back up the register

```
sqlite3 acme.db ".backup '/backups/acme-$(date +%F).db'"
```

Not `cp` — WAL means a live copy can be inconsistent. The register holds every
sanction decision the customer has made, and asking them to re-review forty
agents is a conversation that ends a pilot.

---

## When something looks wrong

**"0 agents found" on an account that definitely runs agents.**
Check coverage first. Then check that `CUSTOS_FLOW_LOGS` points at the group
carrying the traffic — an account can have several and the empty one still
parses cleanly. Then check the catalogue revision: an agent using a provider we
do not recognise is invisible.

**The agent count halves and doubles every other week.**
Two regions taking turns. A scan is one region's window, so an account
collected in two regions produces two interleaved series of scans — and a
history read as one list looks like an estate that keeps losing half its
workloads. `custos history` and the console's scan list show a region column
when there is more than one; check that first, before looking for the outage.

Everything downstream of a scan is per region for the same reason: an agent's
reach is the union across regions, its behavioural baseline is built per
region, the scan diff compares a region against its own previous scan, and the
gateway questions and review band carry every region's. If something looks
like it is alternating, that is the bug, and `CONTRIBUTING.md` has the pattern
under "An account-scoped answer from a per-region row".

**A region is named in the report but nothing was collected from it.**
Expected after ninety days. Telemetry is pruned on that schedule and the
register is not, because an agent discovered last year is still running — so
the report lists its agents and says, in the limitations, that the figures
beside them come from a scan that no longer exists. Collect that region again
and the sentence goes away.

**Everything lands in the review band.**
Almost always missing access logs. The decoupling signal is unavailable, so
confidence drops across the board.

**Findings with no owner.**
Tag hygiene. The Attributor tries resource tags, role tags, IAM path, then a
name heuristic, and reports which one it used. If everything resolves by name
heuristic, the confidence is low for a reason.

**The approval scope is a list of IP addresses.**
`custos-collector --check` reports this before the first scan, and the console
warns when fewer than half a scan's internal destinations could be named. It
means the findings are right and nobody can act on them: an operator cannot
approve `10.0.4.23`. Names come from an ENI's `Name` tag or from AWS's own
description for a managed service, so the remedy is tagging the ENIs behind
those services. Nothing about the classifier changes either way.

**The customer asks whether the dollar figures are their rate.**
They are not, until they say. The built-in table is order-of-magnitude
placeholder pricing, good for ranking agents against each other and nothing
else, and every surface labels it. Ask them for their actual rate — they have
the contract — and:

```
custos --db acme.db set-rate anthropic --account 447120043318 \
  --input 3.00 --output 15.00 --operator you@example.com
```

It applies to the next scan and does not recompute existing figures. A report
already sent to somebody with a budget should still say what it said.

**The account runs a model gateway.**
The most likely reason a scan comes back emptier than expected. An agent whose
model calls go through an internal endpoint has no model traffic we can see, so
it is not a low-confidence finding — it is absent. Three places will tell you:

```
./custos-collector --check                      # before the first scan
custos --db acme.db gateways --account 447120043318   # after any scan
```

and the console asks above the register. When one is theirs:

```
custos --db acme.db declare 10.0.7.0/24 \
  --account 447120043318 --operator you@example.com --note llm-gateway
```

It takes effect on the next scan. Existing findings are not reclassified,
deliberately — a register whose past changes underneath an operator is one they
cannot reason about.

**A finding the customer disputes.**
Good — that is what the evidence sentences are for. Every finding carries the
byte ratios and coupling figures behind it. If they are right and we are wrong,
that is a classifier bug worth a test in the corpus, not an argument.
