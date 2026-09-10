# Where this stands

A single page for someone picking this up cold — a design partner asking what
is real, or a second engineer arriving.

## What runs today

The full discovery loop, end to end.

```
Terraform apply (customer)  →  role ARN
custos-collector            →  batch.json          (dry run prints it first)
custos scan batch.json      →  report.html
custos diff                 →  what changed since last week
```

| Capability | State |
|---|---|
| Classify agents from flow log metadata | Works. G0 passed, margin 0.26 |
| Read flow logs from CloudWatch or S3 | Works |
| Collect several regions | Works. `CUSTOS_REGIONS`, one batch per region, named in the report |
| Read the flow log format the account already has | Works. Fields located by name; S3 objects name their own |
| IPv6 model endpoints | **Blind.** The catalogue is IPv4 only. Counted and disclosed, not guessed |
| Read ALB access logs | Works. Four fields taken, the rest discarded at parse |
| Resolve interface → principal, EC2 | Works |
| Resolve interface → principal, Lambda / ECS | Works |
| Resolve interface → principal, EKS | Node level, or exact via CloudTrail for Bedrock traffic |
| Blast radius from IAM policy | Works |
| Attribution to a team | Works, four methods with stated confidence |
| Persistent register with SEC-17 state machine | Works |
| Scan comparison | Works |
| Behavioural baselines and drift | Works |
| HTTP API, container image, retention | Works |
| Delivery to Slack and SIEM | Works, with per-channel suppression |
| Scheduled collection | Works, cursor-tracked, no gaps on restart |
| Multi-account tokens | Works. One token covers a named set of accounts |
| Onboarding and preflight | Works. `custos onboard`, `custos-collector --check` |
| Enforcement checkpoint | **Not started.** §12: not before a paying customer |
| Operator console | Works. Read, filter, sanction, retire, and see what changed. Served by the control plane |
| Destination naming | Works where an ENI, an AWS description, or a port says what something is |
| Customer-supplied pricing | Works. Per account, dated, superseded rather than overwritten |
| Gateway questions in the report | Works. Their own section, with the workloads reaching each address |
| Review band, kept and readable | Works. In the console, the CLI, and both reports, with evidence and recurrence |
| Model gateway declaration | Works. Private ranges scoped to a region (SEC-24) |
| Fleet view across accounts | Works. One line per account, unscanned and destructive first |
| Scope readability, measured | Works. Reported by `--check`, the report, the console, and `custos history` |

## The one number that matters

**G0 passed: 1.00 recall, 1.00 precision, 0.26 separation margin, identical at
60s and 600s flow log aggregation.**

Against a harder corpus added afterwards — agents that pause for human
approval, agents on batch schedules, chatbots with function calling — every
verdict is still correct and there are still no false positives, but the margin
falls to **0.14**. Quote that number, not the first one, wherever it would be
doing work.

Reproduce with `make experiment`. CI fails the build if it stops holding.

The finding underneath it is the interesting part: the signal the specification
leads with — burst timing and per-call payload growth — is not implementable,
because aggregation plus TLS connection reuse leaves under 0.5 flow records per
model call. What replaced it, cumulative egress-to-ingress asymmetry, is a
property of summed bytes and survives aggregation intact.

Full result and its limitations: `docs/A0-FINDINGS.md`.

## What is still unproven

**Headroom is thinner than the headline number.** 0.14 on the stress corpus
against a 0.15 durability bar. Every verdict is correct; there is simply less
room before one is not.

**A model endpoint we do not recognise is an agent we cannot see** — but the
account can now be asked, and told. An agent behind a self-hosted gateway has
no model traffic we can observe, so it is not a low-confidence finding, it is
nothing. That is still true and still the mechanism.

What changed is that it is no longer silent. `custos-collector --check` names
internal addresses that send far more than they receive before a byte is sent;
every scan produces the same list as questions with the numbers behind them;
the console asks them above the register; and a declaration takes effect on the
next scan. The report says whether an account declared anything, because a
report with no findings and no declarations is the exact artefact a hidden
gateway produces.

`custos-a0 stress --hide-gateway` still reproduces the miss — recall 0.88 — and
now exercises the same per-account declaration the product ships rather than a
global one nobody can use.

**What is unmeasured is whether the questions are the right ones.** The
detector finds the real gateway first on the stress corpus and asks nothing at
all on the base corpus, which is the result that matters. Neither of those is
a real account. The failure to watch for is a customer who gets three questions
a week about ordinary internal APIs and stops reading them, which is how they
miss the one that matters.

**The byte ratios have only been measured against synthetic traffic.** The
weights were fitted on the A0 corpus. What A0 establishes is that a separating
signal exists and which features carry it — not that these weights generalise.
One real scan answers it, and the thresholds sit in measured empty space so
there is room to move them.

**Destination naming is partial, and how partial is unmeasured.** The scope
reads `billing-api 10.0.4.21`, `rds 10.0.9.45`, `s3` — from the ENI behind an
address, from AWS's own description, and from the flow log's service
annotation. What none of those cover stays a bare address. In the corpus that
is one endpoint out of seven; in a real account the ratio depends entirely on
whether the customer tags ENIs, and nobody has measured it. A scan whose scope
is mostly addresses is a scan whose approvals are mostly guesses, so the
collector reports the count and it belongs next to coverage in the first
design-partner conversation.

**The console was built ahead of the schedule the specification set.** §12
puts it after a paying customer, and that ordering was right: a UI built before
anyone has used the product is a guess about what an operator wants to see. It
exists because it was asked for.

Treat its layout as provisional. Four decisions in it are worth defending and
should be re-argued against a real operator rather than assumed: ordering by
consequence rather than confidence, the evidence gate on the grant control, a
filtered list that always shows the total, and `sanctioned` being absent from
the status control. Everything else about it is a guess.

Nothing depends on it. The CLI and the HTML report still do everything it does.

**An agent reaching a provider over IPv6 is invisible.** Every range in the
model endpoint catalogue is IPv4, the providers are reachable over IPv6, and
there are no published v6 ranges we can verify. Guessing one would be worse
than having none: a false positive manufactures an agent out of unrelated
traffic, which is why the catalogue is narrow in the first place.

It has the same shape as the gateway problem and gets the same treatment —
`--check` counts an account's public IPv6 destinations before the scan and the
report says a model endpoint among them would not appear at all. It is not
fixed, it is stated. It matters more each year: AWS began charging for public
IPv4 addresses in 2024 and dual-stack VPCs are the response.

**A scan covered one region and reported on the account.** The collector took
a single AWS_REGION, nothing anywhere named it, and "No unsanctioned agents
found." was a claim about a third of an estate printed as a claim about all of
it. Worse, the batch key was (account, window), so a customer running one
collector per region — which is what preflight told them to do — had every
region after the first swallowed as a duplicate.

Fixed end to end: `--check` names the regions with flow logs, `CUSTOS_REGIONS`
covers them in one run, a batch is now one region's window, the register
records every region an agent has been seen in, and the report names the region
it covered. What is not fixed is that an agent's spend and reach still come
from the scan that last saw it — one region's traffic — and every surface says
so rather than presenting a fraction as a total. Doing better needs the latest
observation per region rather than a merged blob.

**The IAM policy asked for thirteen permissions nothing used, and I nearly
kept them.** The same test that found the missing S3 grant flagged thirteen
granted actions no code calls — and the first thing I did was write a
justification map with a plausible sentence beside each one, none of which was
true. That is the failure the test exists to prevent, committed inside the test.
The policy lost all thirteen, among them `iam:ListRoles` and `ec2:DescribeTags`
on `*`. Twenty-one actions remain.

**The IAM policy did not grant the S3 reads the collector makes.** Flow logs
delivered to S3 and load balancer access logs are both read with ListObjectsV2
and GetObject, and the Terraform granted neither — so a customer who took a
change request through their org, applied the role and ran the collector got
AccessDenied on every object. S3 is the cheaper flow log destination and the
only place access logs go, so this was the path the target profile was most
likely to take.

Fixed, scoped to named buckets. What matters more is that a test now connects
the two artefacts: the interfaces in `awsread/api.go` are the complete set of
operations the collector can perform, and every one has to appear in the
Terraform. It runs the other way too, because a permission granted and never
used is what a security review finds.

**A first scan of a large account was throttled into looking like a clean
one.** The AWS SDK's default retry budget is three attempts; the collector makes
thousands of calls in a burst, so EC2 throttling spent it inside a second and
the describe calls failed. The result was a report full of unattributed
findings — the same shape an untagged account produces — so the failure read as
a fact about the customer. Eight attempts in adaptive mode now, and the report
says how many reads failed so the two can be told apart.

**Ingestion is serialised, and until today two accounts shipping on the hour
dropped one of them.** One process, one connection, one SQLite file, and
FastAPI routes in a thread pool: the second `BEGIN` failed and that window was
gone. Fixed with a process-wide write lock, so the second account waits. The
ceiling that replaces it is arithmetic rather than a crash — total ingest
seconds per collection interval, bracketed at 0.3s for a quiet account's window
and 25s for one at the collector's record limit, and written down in
`deploy/README.md`.

**A full window is 225MB of JSON, and that was not survivable until today.**
Measured: 500,000 flow records validate in 20.6 seconds and 2.7GB of resident
memory. The shipper was posting that uncompressed under a fixed thirty-second
timeout, which does not complete on any real egress path — the corpus never
showed it because 33,000 records is 40KB. Batches now go compressed (32x), the
deadline scales with the body, the container is sized from the measurement, and
a batch too large to validate is refused with a message naming the remedy
rather than being killed mid-request.

**How much a real account's flow log format costs is unmeasured.** Reading a
log a customer already keeps is now supported, and the AWS default format is
usable — but it has no ports, no service annotations and no direction field.
What that does to recall on real traffic is a question only a real account
answers. The costs are stated in `--check` and in the report, so the failure
mode is a stated gap rather than a silent one, which is the most that can be
arranged from here.

**Nobody has run this against an account we did not build.** Tag hygiene,
unanticipated workload shapes, and provider endpoints outside our catalogue are
all real and all unmeasured.

**Spend figures are placeholders until an account says otherwise.** The
built-in table still reads `unverified-placeholder` and a test still pins it
there. What changed is that verifying it is no longer our job: an account can
supply the rates it actually pays — enterprise agreement, committed use,
provisioned throughput — and every surface then says the figures are theirs and
when they said so.

That is the honest resolution rather than the one this entry originally
anticipated. We were never going to be able to verify a customer's rate; they
have the contract and we do not. What we can do is stop presenting our guess as
though it were theirs, and make supplying the real number a two-minute job.

Still true whichever rates are used: the figures come from wire bytes, not
token counts. Good for ranking agents against each other, not for reconciling
against an invoice, and labelled that way everywhere.

**A signal computed over an empty set is not zero, and it took a screenshot to
notice.** Four of the five classifier signals are ratios and fractions over the
intervals containing model traffic. On a workload with none, they evaluated
over an empty set and came out maximally incriminating rather than neutral: the
heaviest signal in the system fired at full weight and printed "100% of the
intervals containing model traffic had no request arriving at the load
balancer" about a workload with no such intervals. That sentence was reaching
reports.

It is fixed — those signals are now unavailable rather than zero, which was
already the rule for load balancer logs — and the gates did not move, because
no workload in the base corpus has zero model traffic. What moved is the stress
corpus's agent-behind-a-gateway: 0.77 in the review band, now 0.17 and not
scored at all. That is the honest answer, and it is why the report grew a
Questions section: a workload we cannot see making model calls is an unanswered
question, not a low-confidence finding.

Worth recording how it was found. It was not found by a test. It was found by
looking at a screenshot of the review band and reading a row that said "Sent
0.0B to model endpoints and received 0.0B back, a ratio of 0.0:1" as though
that were evidence of something.

**A corpus that was more informative than production.** The A0 corpus
annotated both ends of every AWS conversation with the peer's service. Real
flow logs annotate one end — the destination of a request, the source of its
reply — and the collector was reading only the destination field, so the return
leg of every AWS conversation would have arrived unattributed in a customer's
account and did not here. Fixed in both places, and a test now asserts the
corpus emits what AWS emits. Worth stating plainly because it is the second
time the corpus has been wrong in the flattering direction, and there is no
reason to think it is the last: every signal measured against synthetic
traffic carries this risk until an account we did not build disagrees with it.

## The blocker

Design partner access. Everything technical that justified building first is
resolved; every remaining question needs an environment nobody here has seen.

The kill gate to take seriously is still the week-8 one: four or more scans
with nothing surprising in any means companies do know what they run, and
discovery is not a business.

## If you are picking this up

Read in this order:

1. `docs/A0-FINDINGS.md` — what was measured and what it means
2. `docs/SECURITY-INVARIANTS.md` — the five rules and the tests enforcing them
3. `CONTRIBUTING.md` — how not to break them
4. `docs/OPERATIONS.md` — how to actually run a scan

Then `make check`. If it passes, the invariants hold and the gate is still
green.
