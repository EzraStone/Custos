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
export CUSTOS_ACCESS_LOGS=s3://their-alb-logs/AWSLogs/...   # worth asking for

CUSTOS_DRY_RUN=1 ./custos-collector > batch.json
```

Dry run first, always. It prints the literal batch and sends nothing, and
walking a customer through that output is the fastest way through a security
conversation.

**Ask for the access logs.** Without them recall drops from 100% to 60% on our
corpus, and the agents missed are the low-volume ones — which are usually the
ones they most want to know about. The ask is easier with that number attached.

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
per run.

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

**A finding the customer disputes.**
Good — that is what the evidence sentences are for. Every finding carries the
byte ratios and coupling figures behind it. If they are right and we are wrong,
that is a classifier bug worth a test in the corpus, not an argument.
