# Changelog

Notable changes, newest first. Dates are when the work landed on `main`.

## Unreleased

### A declaration could invent agents in a region nobody asked about

**SEC-24.** Declaring an endpoint is one of two decisions in this system that
change what counts as an agent, and it is the one that widens the set: a
declared address becomes model traffic, and every workload reaching it becomes
an agent with an estimated monthly cost beside its name.

Declarations were scoped to the account. A customer answering "yes, 10.0.7.40
is our model gateway" was answering for every region they run in — and in
eu-west-1 that address is a database proxy, a build cache, an internal API
nobody has thought about in a year. All of that traffic becomes model traffic
and all of those workloads become agents.

A private range now needs the region the question was asked about and is
refused without one. A public provider range needs none: it means the same
thing everywhere and cannot collide with a customer's own addressing. Gateway
questions carry their region, the console sends it back when answering, and the
report says where each declaration was in force rather than only that it
existed.

Written up as an invariant because of the direction. Every other failure in
this system hides an agent, and a hidden agent is a gap somebody can be told
about. This one invents them, in a document whose whole value is that a
security team believes it.

### The gateway questions had no number, and it turned out to be zero

**The detector that interrupts a human had never been scored.** It asks a
customer whether an internal address is their model gateway, and the result
standing in for a measurement — asks nothing on the base corpus, finds the
real gateway first on the stress corpus — was a fact about the corpus. Neither
one contained an internal destination that ordinary infrastructure floods, so
there was no opportunity to ask a question a customer would answer no to.

Seven were added: a log collector, a backup service, a metrics pushgateway, an
artifact registry, an event proxy, a thumbnailer and a document extractor.
**Nine questions asked, five shown, the real gateway eighth.** Precision 0.00.

Neither volume nor ratio separates them — the gateway is the smallest thing on
the list and its ratio sits between the thumbnailer's and the extractor's. What
separates them is the loop. An agent behind a gateway calls the model, calls a
tool, and calls the model again with the result; a log shipper talks to one
thing forever, and a destination that is the only thing its workload ever
reaches cannot be that workload's model endpoint. Measured at 0.00 for every
bulk sender and 0.98 for the real gateway.

Two questions now, the gateway first, precision 0.50. The question itself
carries the new evidence, because it is the half that decides: "in 98% of the
minutes they reached it they also reached another internal service, which is
what a tool loop looks like."

The rule can be wrong — a gateway proxying its workload's tool calls as well
would be excluded by it — so the number of destinations declined for that
reason is carried into the report. A silent filter in front of the questions
would make a clean-looking empty report easier to produce, and that artefact is
the thing the questions exist to prevent.

`make questions` reproduces it; CI prints it on every build.

### The report described an account from one region's scan

**A scan is one region's window; the report is the whole register.** Once a
batch became one region, the served report kept building its figures from the
latest scan — which is now one region — while rendering agents from all of
them. Four false claims came out of that, all of them in the part of the
document a security team reads to decide what the list is worth:

- The limitations said "covered eu-west-1 and no other region" over a list
  containing us-east-1's agents.
- One region's flow log format was described as the account's, so a port field
  switched off in one region read as "this account records no ports" — false of
  the others, and a gap nobody could locate.
- The incomplete-coverage banner, which sits above everything precisely so it
  is read before any conclusion is formed, was suppressed by a healthy region:
  40% of one region's flow log unread, no banner, a clean-looking report.
- "Principals seen" in the masthead counted one region beside an agent count
  taken from all of them, so the ratio was between two populations.

The report now assembles from the latest scan of each region. Fields missing
in some regions but not others name the ones they are missing in, because that
is a gap somebody can go and fix in one place. The parse figure is the worst
region's rather than the last one's and the banner names which region it is
about. Counts that are counts — failed reads, records dropped for want of a
direction, the approval scope — add. The principal count is the largest
region's, a floor, and the limitations say so: summing would count an IAM role
that runs in two regions twice.

Every one of these was invisible in a single-region account, which is every
account anyone has run this against. A test reads the report route's source
and fails if it takes a per-scan figure off the latest scan again, because the
next figure added to the masthead will be written the same way and nobody will
think to write the two-region test for it.

### One region is not an account

**A scan of us-east-1 was reporting on the whole account.** The collector took
a single AWS_REGION and nothing anywhere said so, so "No unsanctioned agents
found." was a claim about part of an estate printed as a claim about all of it.
A workload in eu-west-1 has its own flow logs, its own interfaces, and no
representation whatsoever in that scan.

`--check` now names which of an account's regions have flow logs — regions
without one are not mentioned, since AWS enables about seventeen by default and
a list nobody reads is worse than none. `CUSTOS_REGIONS` covers them in one
collector run.

**One batch per region, not one merged batch.** A private address is unique
within a region and nowhere else: 10.0.4.21 is the billing API in us-east-1 and
something else in eu-west-1, so a scan holding both would print one region's
service name against the other's traffic — in the approval scope an operator
reads before granting authority. Every flow record and resolved destination now
carries the region it came from.

That made region part of a batch's identity, which needed the first
hand-written migration in the store: the key was (account, window), so a
customer running one collector per region had every region after the first
swallowed as a duplicate. Rows keep their ids so historical scans still resolve.

**The register remembers where an agent runs.** Regions accumulate across
scans, because a scan covers one region and a role running in three is
discovered three times — the register used to describe an agent as living
wherever it was most recently looked for.

**Spend adds up across regions; reach does not.** An agent's cost is now the
sum of what it spends in each region it was seen in, kept per region so a
re-scan of one replaces that region's figure rather than the whole total —
before this, a role running in three regions was billed at whichever region
was looked at last, which understates by however many regions were not.

Reach is still one region's: the tools and data stores listed beside an agent
are what the scan that last saw it observed. Every surface that names more
than one region says so, because a row listing three regions and one set of
tools otherwise reads as the whole of what that agent can touch.

### A role that asked for too much and delivered too little

**Thirteen permissions removed.** The policy granted `iam:ListRoles`,
`ec2:DescribeTags`, `ec2:DescribeSubnets`, `ec2:DescribeVpcs`,
`ec2:DescribeFlowLogs`, `logs:GetLogEvents`, `logs:DescribeLogStreams`,
`ecs:ListServices`, `ecs:DescribeServices`, `ecs:ListTasks`,
`lambda:ListFunctions`, `eks:ListClusters` and `eks:DescribeCluster`. Nothing
in the collector calls any of them. Twenty-one actions remain.

The first two are the ones that matter: `iam:ListRoles` on `*` enumerates every
role in an account and `ec2:DescribeTags` on `*` reads every tag, granted by a
product whose whole argument is that it asks for less than you expect.

### A role that could not read the logs

**The IAM policy granted no S3 access at all.** The collector reads flow logs
delivered to S3 and load balancer access logs with ListObjectsV2 and GetObject.
A customer took a change request through their organisation, applied the role,
ran the collector, and got AccessDenied on every object — on the delivery path
the target profile is most likely to be using.

Fixed and scoped to named buckets rather than "*", with the bucket list carried
in the onboarding material so nobody discovers the variable afterwards.

The fix is the small part. A test now connects the two artefacts that had
nothing between them: the interfaces in `awsread/api.go` are the complete set of
operations the collector can perform, and each must appear in the Terraform
policy. It parses the Go rather than reading a list, and it runs in both
directions — a granted action nothing calls is a permission a customer was
asked for and did not need.

### A first scan of a large account

**The retry budget was the SDK's default of three attempts.** That is right for
an application making a few calls. The collector makes thousands in a burst —
one DescribeNetworkInterfaces page per thousand interfaces, an IAM read per
principal, a CloudTrail lookup per unresolved address — so on an account with a
few thousand interfaces EC2 throttles and three attempts are spent inside a
second.

What that produced was not an error. It was a report where half the findings
were unattributed, which is exactly the shape an account with no resource tags
produces, so the failure arrived disguised as a fact about the customer. Now
eight attempts in adaptive mode, which rate-limits the client once AWS pushes
back rather than retrying into a wall — the throttle the collector earns applies
to the customer's whole account, not just to us.

**And the report can now tell those two apart.** The batch carries a count of
AWS reads that failed after retries, and the report says it beside the
unattributed findings. The count ships and the messages do not: an AWS error
string can quote a resource ARN or a policy, and nothing describing an account's
contents leaves it except through the named wire fields.

### Two accounts on the hour

**The second one's window was being dropped.** One process, one connection, one
SQLite file, and FastAPI routes in a thread pool — so two collectors arriving
together hit the same connection, the second `BEGIN` failed with "cannot start
a transaction within a transaction", and the request 500'd. The collector
retries three times against the same clash and gives up. This is the schedule,
not an edge case.

Write transactions now take a process-wide lock, so the second account waits.
Reads stay outside it on purpose: holding every console read behind a
twenty-second ingest would be the worse trade, and the cost of that choice — a
brief view of a scan still being written — is stated rather than hidden.

The ceiling this creates is arithmetic instead of a crash, and it is written
down: total ingest seconds per collection interval, bracketed by 0.3s for a
quiet account's window and 25s for one at the collector's record limit.

### Two hundred megabytes

**A full collection window did not fit down the wire.** One window at the
collector's own record limit is 500,000 flow records, which is 225MB of JSON,
and the shipper posted it uncompressed under a fixed thirty-second timeout.
That send does not complete on any real egress path, and the failure it
produces is the worst kind: the collector reports that shipping failed, retries
three times, and the customer's first impression is that the product does not
work. The corpus never showed it — 33,000 records is 40KB.

Batches are compressed now, at a measured 32x, so 225MB goes as 6.2MB. Flow log
JSON is the most compressible payload imaginable. The request deadline scales
with the body instead of being one number for a 40KB batch and a 200MB one.

**And it was not survivable at the other end either.** Validating that batch
costs 2.7GB of resident memory. The deployment now says 4GB with the
measurement beside it, and a batch above 320MB is refused with a message
telling the operator to shorten the collection window — an error that names the
remedy beats an OOM kill that says nothing. A test reads the collector's record
limit out of the Go source and checks it still fits.

**An agent reaching a provider over IPv6 is invisible, and now says so.** Every
range in the catalogue is IPv4 and there are no published v6 ranges we can
verify; guessing one would manufacture findings out of unrelated traffic. Same
shape as the gateway problem, same treatment — counted before the scan by
`--check`, disclosed in the report, not silently absent.

### Read the logs they already have

**The collector no longer requires a flow log in our format.** It required one
because the parser read fixed positions, so a log written any other way decoded
into the wrong columns or failed the field-count check on every line — and both
of those report a clean account.

Fields are now located by name. A platform team with flow logs already going to
an S3 archive was being asked to pay twice, since VPC Flow Logs bill per
gigabyte, and to get a second Terraform apply through change control. Both are
reasons to stall that have nothing to do with whether the product works.

S3 needs no configuration at all: AWS writes the field names at the top of every
object, and the file is believed over any setting — a stale format setting
becomes a correct parse rather than a silent misread of every line. CloudWatch
carries no header, so `CUSTOS_FLOW_LOG_FORMAT` says.

**Direction is inferred where the format has none.** The AWS default format has
no `flow-direction` field, and every finding rests on the asymmetry between what
a workload sends and what it receives. Two sources: the interface's own address
as AWS reports it, and failing that the address present at some end of every
record on that interface — exact rather than heuristic, since one end of every
record on an interface is that interface. What neither answers is dropped rather
than guessed. A dropped record costs coverage, which is counted and reported; a
guessed one puts bytes on the wrong side of the ratio, silently.

**Every absence is stated twice.** `--check` says what the account's format
costs before the first scan, and the report says what it prevented the report
from claiming. An account whose log has no port field has no MCP servers in its
register, and the report now says why rather than letting its silence read as a
finding.

### A signal with nothing to measure

**Four of the five classifier signals were being evaluated over an empty set.**
They are ratios and fractions over the intervals containing model traffic, and
on a workload with none of those they did not come out neutral — they came out
maximally incriminating. `1 - inbound_coupling` evaluates to 1.0, so the
heaviest signal in the system fired at full weight and described itself in the
report as "100% of the intervals containing model traffic had no request
arriving at the load balancer", about a workload with no such intervals.

Those signals are now unavailable rather than zero. That distinction was
already the rule for load balancer logs — reported, never silently treated as
zero — and this was the same failure one level down.

Nothing in the gates moved: G0 still passes at 0.26 with full recall and no
false positives, and the stress corpus still separates by 0.14 with the gateway
declared. No workload in the base corpus has zero model traffic, which is why.
What moved is the stress corpus's agent behind an undeclared gateway: it scored
0.77 and sat in the review band, and 2.8 of its 4.4 points came from signals
measuring nothing. It now scores 0.17 and is not scored in any meaningful
sense, which is the correct answer — the entire evidence base is model traffic.

**So the report grew a Questions section.** A workload we cannot see making
model calls is an unanswered question rather than a low-confidence finding, and
it needs somewhere to appear. The open gateway candidates, the numbers behind
each, and the workloads reaching them now sit between the findings and the
review band — because an account with an undeclared gateway has agents that
produce no evidence at all, and a short findings list above an open question
means much less than a short findings list alone.

`custos gateways` was also not filtering declared addresses, so it kept asking
about answered questions on every scan.

### Forty accounts, one screen

**A fleet view.** The account picker listed twelve-digit numbers with nothing
to choose by. A customer in the target profile runs five to fifty accounts, and
"which one do I open first" was answerable only by opening all of them.

Each row now carries what somebody triages by: unsanctioned agents, how many of
those hold credentials that can destroy things, when the account was last
scanned, coverage, open questions. Unscanned accounts sort first — an account
nobody has ever scanned is the one most likely to be hiding something, and it
was previously indistinguishable from a clean one.

The route's cost is now pinned by a test that counts queries. It is a loop over
accounts, and the failure it invites is a lookup per agent inside that loop —
instant on a demo database, slow on the customer with the most to find.

**The served report shows the review band.** It was built with an empty verdict
list, so an account whose console listed three maybes got a report saying "For
review: 0" — and the report is the artefact that gets forwarded to the workload
owner. Both the served and the written report now carry the maybes, each with
how many scans it has recurred in.

**A prune says what questions it took.** Review candidates and gateway
questions leave with their scan by cascade, and a cascade reports nothing. An
operator reading "pruned 40 scans" had no way to know that eleven questions
they meant to answer went at the same time.

### The numbers are yours

**A customer can supply the rates they actually pay.** Every dollar figure came
from a table stamped `unverified-placeholder`, which has been an open item
since A0. The fix was never going to be us verifying harder — a customer has
the contract and we do not. So the figure becomes theirs when they say, dated,
and stays labelled as ours until then.

Rates are superseded rather than overwritten. A figure in last month's report
was computed from the rate in effect then, and losing that row would make the
report unreproducible — which matters most for the one number people act on. A
rate of zero is refused: far more likely an empty form field than a free
provider, and it would make every agent on that provider look free.

**The review band is kept, not just counted.** An operator could see that three
workloads were uncertain and not which three. They are now shown with their
evidence and with how many scans each has recurred in — one uncertain window is
noise, the same workload uncertain in eleven scans is a standing question.

There is no path from the review band into the register, in the API, the CLI,
or the console. Promoting a maybe by hand is what the register is not for, and
a route that allowed it would make every guarantee about how an agent got there
conditional on nobody having used it.

### The gateway question

**A customer can tell us where their model calls go.** An agent whose model
traffic runs through a self-hosted gateway has no model traffic we can see —
not a low-confidence finding, not a review candidate, nothing. `docs/STATUS.md`
has called that the single most likely reason a real scan comes back emptier
than it should since A0, and the mechanism to fix it existed as a function
nobody could call.

Declarations are per account, not per process. Building this on the module-level
`catalog.extend()` was the obvious move and would have made one customer's
gateway a model endpoint for every account in the same process — 10.0.0.0/8 is
where everyone's internal services live, so a coincidental collision would have
manufactured agents out of unrelated traffic.

**And we can ask, rather than waiting to be told.** "Do you run a self-hosted
model gateway?" gets a confident no from the platform lead whose predecessor
stood one up. "Forty per cent of what this workload sends goes to 10.0.7.9, it
gets almost nothing back, and it never talks to a provider we recognise — is
that your gateway?" is checkable in a minute. That question is asked in the
console, in `custos gateways`, and by `custos-collector --check` before a byte
is sent.

It never classifies anything on its own. A heuristic that promoted an internal
address to a model endpoint would manufacture agents out of any busy internal
service, and the first false positive of that kind costs more trust than every
true one earns.

There is no way to answer no. A stored dismissal would be configuration that
suppresses a question. Being asked twice about something harmless costs a
glance; being asked never about a real gateway costs the account.

**The report says whether anything was declared** — including when nothing was,
because a report with no findings and no declarations is precisely the artefact
a hidden gateway produces.

### The approval is readable

**The operator console.** The register in a browser, served by the control
plane at `/`: read the findings, read the evidence, sanction an agent, retire
one that no longer exists. Ordered by what each agent could destroy rather than
by how confident the classifier is. The grant control stays disabled until the
evidence has been opened — a console that makes approving easier than reading
upholds SEC-17 in code while defeating it in practice.

Built ahead of the schedule §12 sets, which put it after a paying customer.
That ordering was right and the deviation is recorded in `docs/STATUS.md`.

**Destinations have names.** The approval scope read `10.0.4.23`,
`52.216.10.7`. Nobody can make a decision about an IP address, and that was the
one screen in the product where a human confers authority. It now reads
`billing-api 10.0.4.21`, `rds 10.0.9.45`, `s3` — from the ENI behind an
address, from AWS's own description of a managed service, or from the flow
log's service annotation. What none of those cover stays an address, honestly,
and every surface reports how much of a scan is in that state.

Three things this uncovered. The tools/data-stores split was inferred from what
a whole window saw, so an internal API could be filed as a data store on the
strength of unrelated traffic in the same minute. The collector read only
`pkt-dst-aws-service`, so the return leg of every AWS conversation arrived
unattributed in production — hidden here by a corpus that annotated both ends,
which is not what AWS emits. And a column added to the schema after a database
was created never reached it, because the schema is applied with `CREATE TABLE
IF NOT EXISTS`.

**SEC-23.** Only names matching a shape AWS writes leave the account. An ENI
description is free text a person typed into, and what people write there is
"temp box for INC-4471, ask Sam before deleting".

**What changed since the last scan**, over HTTP and in the console. The CLI has
answered this since the register existed; nothing else could, so the console
showed a register with no sense of time — which is the difference between a
subscription and an audit.

### Continuous, and harder to fool

**Delivery.** Findings reach Slack and a SIEM without anyone opening a report.
The design is mostly restraint: a first scan sends one summary rather than
forty alerts, later scans send only what changed, and suppression is per channel
with repeat windows set by severity. The failure mode of a security channel is
not missing an alert — it is sending so many that the channel gets muted, after
which every alert is missed.

**Scheduled collection.** The collector runs as a service, tracking a cursor so
a crash, a deploy, or a throttled hour costs latency rather than data. A window
that exceeds its record limit is now **shortened** rather than truncated, which
closes a silent data-loss path: a truncated window advanced the cursor past
records that were never read, and the next scan simply reported fewer agents.

**Attribution reaches further.** Lambda and ECS execution roles resolve fully.
CloudTrail fills the remaining gaps by mapping a source address to the role that
used it — the one path that needs no network interface, and the only one that
reaches EKS pods.

**Multi-account tokens.** One customer in the target profile runs five to fifty
AWS accounts. A token now covers a named set rather than one, without weakening
the boundary: it still cannot reach an account it was not issued for, and must
say which account a read applies to rather than being allowed to guess.

**Onboarding.** `custos onboard` generates a customer's credentials, tfvars, and
the paragraph to paste into a ticket. `custos-collector --check` names which
onboarding failure occurred, because every one of them produces the same
symptom: a report with no findings.

### A harder corpus, and a smaller margin

Four workloads were added that break the clean coupled/decoupled split the
original corpus had: an agent that pauses for human approval, an agent on a
batch schedule, a chatbot with function calling, and an agent behind a
self-hosted gateway.

**Every verdict is still correct and there are still no false positives. The
separation margin falls from 0.26 to 0.14.** That is the number to quote
wherever the first would be doing work, and `make stress` prints it.

The weights were not retuned to widen it. They were already fitted on synthetic
traffic; fitting them again on more synthetic traffic would improve the metric
and nothing else.

### Two new invariants

Both from building rather than planning. SEC-21 came from finding a third-party
HTTP client logging full request URLs at INFO, routing query strings into a
customer's SIEM by a path the application's own middleware never touched.
SEC-22 came from realising a truncated collection window is silent data loss.


### The loop closed

The repository runs end to end: a collector reads a real AWS account, ships
metadata, the control plane classifies and persists it, and a report comes out
that says what changed since last week.

**Collector**
- Reads VPC Flow Logs from CloudWatch Logs or S3 — whichever the customer
  already uses, because asking a platform team to change where flow logs are
  delivered is a production change request the free-scan motion cannot survive.
- Resolves network interfaces to principals across EC2, Lambda, and ECS. EKS
  resolves to node level and says so rather than claiming pod attribution.
- Enumerates IAM policy, so a finding says "can write to your billing tables"
  rather than "talked to your billing API".
- Reads ALB access logs, taking four fields and discarding the URL, query
  string, user agent, client address, and trace ID at parse time.
- Ships collection statistics, so the control plane can tell "this account is
  clean" from "this scan read a third of the traffic".

**Control plane**
- HTTP API: ship a batch, read the register, sanction an agent, serve the
  report. Idempotent on the collection window.
- SQLite-backed register holding the same SEC-17 state machine as the in-memory
  one, and held to the same tests.
- Scan comparison. The second scan now says something the first did not, which
  is the difference between a subscription and an audit engagement.
- Per-agent baselines and drift, phrased as questions to the workload's owner.
- Retention with a mechanism, so "how long do you keep our data" has a number
  behind it.
- `custos` CLI: scan, register, history, diff, grant, prune. A first customer
  scan needs no server at all.

**Invariants**
- SEC-19 and SEC-20 added alongside the specification's SEC-16 through SEC-18.
- The cross-language wire contract is now tested: the Python schema and the Go
  wire types are compared field for field, in both directions.
- CI runs the invariants and the contract as their own jobs, so a failure reads
  as "an invariant broke" rather than as one line inside two hundred tests.

### Gate G0 — passed

The load-bearing assumption holds: an agent separates from a chatbot backend on
metadata alone. Separation margin 0.26, full recall, zero false positives,
identical at 60s and 600s flow log aggregation.

Two signals the specification expected to carry the classifier were measured
and rejected, and the signal it leads with — burst timing — turned out not to
be implementable at all. Full result and its limitations in
`docs/A0-FINDINGS.md`.
