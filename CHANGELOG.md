# Changelog

Notable changes, newest first. Dates are when the work landed on `main`.

## Unreleased

### The candidate signal's numbers were prose, and one was wrong

`CANDIDATES` was added so a measured-and-promising signal is not lost between
"shipped" and "rejected". Its entry quoted six figures that nothing in the
repository produced — measured once by hand, typed into a docstring,
unfalsifiable from then on. A rejected signal's reasons can be prose; nobody
acts on them. A candidate's cannot, because the category exists so somebody
picks it up later and decides against those numbers.

`make candidates` computes them, and eleven tests hold the entry to them. Five
of the six were right. The IDE assistant reads 0.310, not the 0.30 quoted, and
it is outside the scope the entry implied at all: the predicate excludes
anything reaching an MCP server, so the "four coupled tool-calling workloads"
are three.

Better stated as a result, too. Scoped, the signal separates the workloads the
classifier cannot resolve by +0.154. Unscoped it separates by -0.840, because
an embedding service varies more than every agent in the corpus. Both belong in
the entry: one says the idea works, the other says what fencing it off costs —
and the +0.154 rests on a single positive, which is asserted rather than left
for the next reader to notice.

### `make mutate` — whether a signal has a test at all

The ablation says what a signal is worth on the corpus. It says nothing about
whether the suite would notice if one stopped working, and those are unrelated
questions: `mcp_fingerprint` costs 0.000 of separation and is noticed by ten
tests, while a signal could cost a great deal and be pinned by one assertion in
one file with nothing to say so.

Remove each signal, run both suites, count what goes red. Zero fails the run —
a signal no test notices can be broken by an unrelated refactor and ship. The
signal list is read from the shipping table rather than written down here,
because a list in the tool is a second place that has to agree.

Not part of `make check`. Removing a signal fails a large fraction of the suite
once per signal, which is minutes rather than seconds — the same reason
`make smoke` is a thing you run rather than a gate.

### The ablation could not see what one signal was for

`mcp_fingerprint` read 0.000 of separation on both corpora and cost no recall,
and the measurement whose stated purpose is finding signals that carry nothing
reported it as carrying nothing — twice. Margin, recall and precision are all
measured at the register boundary, and the only thing that signal does is hold
`ide-assistant-backend` in the *review queue*. Removing it takes that workload
from one an operator is asked to look at to one nobody hears about.

The table has a `surfaced` column now, `load_bearing` counts it, and a row that
drops an agent names the agent. The test that justified keeping the signal had
also gone vacuous — it asserted the margin goes negative, which a later corpus
addition made true regardless — and now asserts the difference instead.

### Recall counts the register; a second number counts the queue

`surfaced_recall` is the fraction of true agents an operator is shown by either
door. Recall falling while it holds is the classifier being unsure; it falling
is the classifier being wrong, and those are different products.

G0 was already resting on the distinction in prose: the pass narrative said the
agents lost without load balancer logs "land in the review band rather than
being dropped", which was true, load-bearing and unchecked. It is a criterion
now. The stress command prints surfaced recall beside recall and says which
door each missed agent went out of.

### A scan that cannot decide about a workload says so

An agent behind a chat box or an editor answers inbound requests and calls
internal services between model calls. So does a retrieval-augmented chatbot,
and in everything a flow log observes they are the same workload. The
classifier is right to dismiss them — a register listing every RAG chatbot as
an agent is worth nothing — but a report silent about a whole class reads as
"we looked and there were none" rather than "we looked and could not tell".

The report counts them and says what it would mean if any of them is an agent.
Schema 25 stores the count so a report served a week later says the same thing.

### The documents had drifted from the measurements

`docs/STATUS.md` said the stress corpus separates by 0.18 with one agent in
review. It separates by -0.202 with two agents missed, one dismissed outright —
the page written to say where the classifier fails was reporting that it works.
The numbers were copied out of a terminal by hand and nothing connected them to
the code. A test recomputes each one, and refuses any decimal in that block
that no measurement produces.

### Four defects in the correction, found by re-reading it

Found by re-reading the week's own code rather than by any test. A ratio
computed against a payload that clamped to zero, hitting its cap at a million
to one; a discriminator with no minimum sample, deciding a 42x correction on
six packets; and model bytes summed over the calls rather than over every
window, which dropped the tail of a response from the ratio's denominator.

Those three inflate the ratio, which is to say they point at false positives —
which a correction whose job is to remove bytes from a denominator was always
going to do. None of them moved a gate, because no workload in either corpus
has an unreadable response, a six-packet conversation or an
acknowledgement-only window.

The fourth points the other way and is worse. The correction reached model
byte counts and stopped there, leaving tool counts as wire bytes — and a
self-hosted gateway proxies Server-Sent Events, so on a streamed capture its
ratio fell under the threshold and the detector asked **no questions at all**.
A report with no findings and no questions is the artefact a hidden gateway
produces. Both sides go through one function now, and `make questions`
measures both regimes.

### Six features computed for nobody

Five outlived signals that were measured and rejected — context growth, episode
persistence, egress per inbound request — and the sixth outlived
`offhours_activity`. All six were computed for every principal on every scan.

They are gone, and the reason is not the arithmetic. `Features` is the
interface between what can be observed and what can be concluded, so a field in
it reads as evidence the classifier weighs. `test_leakage.py` fails on an
orphan now, the same way it fails on a feature that reads a principal name.

### The classifier had never been ablated

Five weighted signals, and nothing had ever measured which of them was
producing the result. `make ablation` removes each in turn and re-measures.

The answer depends on which corpus you ask. On the base corpus four of the five
are redundant and `egress_asymmetry` — the signal the specification was
rewritten around — costs 0.043 of separation. On the stress corpus
`tool_interleave` carries 0.226 of it. A signal can look free on a corpus that
is not hard enough to need it, so `make gates` prints the stress table.

**`offhours_activity` is removed.** Not inert but negative: taking it out
widens the margin by 0.07 on both corpora with no verdict changing. The reason
is structural rather than a fact about this corpus — a nightly reconciliation
agent runs at 3am and so does a nightly batch summariser — so it joins the
rejected signals rather than being reweighted. Base margin 0.415 to **0.485**,
stress 0.291 to 0.356.

**`mcp_fingerprint` is kept, and the corpus got a workload to justify it.** It
contributed exactly 0.000 on both corpora, which by the precedent of the IAM
policy is an argument for deleting it. It was the corpus: every MCP user in it
already scored 0.995 on the other four signals. With an IDE assistant backend
added — inbound-coupled because a person types first, trajectories too short
for the transcript to accumulate — removing the signal makes the classes
overlap at -0.199.

That workload lands at 0.657, in the review band, and stays there. Stress
recall falls to 0.91 and the stress margin to 0.180. Confirming an agent on
that evidence would be guessing, and a corpus containing only cases the
classifier passes is not measuring anything.

**A third number is printed.** The review threshold's clearance: how far the
nearest workload sits from the line at which a human is asked to look. It is
0.010, and was 0.030 before the removal. The comment beside those thresholds
has always claimed measured empty space, which is true of the agent threshold
and never was of this one — and three of the four figures in that comment were
stale, so a test now holds them against the corpus.

### The signal that carries the product was measuring the protocol

`egress_asymmetry` is the finding this whole product rests on: an agent resends
its accumulated transcript at every step, so it sends far more than it
receives. It was computed on wire bytes.

Wire bytes carry one acknowledgement per two outbound segments, a TLS
certificate chain per connection, and — when a response streams — about
forty-two bytes of framing for every byte the model said. On a streamed capture
of the same corpus, four of five confirmed agents came back with a ratio below
1:1. Below one is the shape of a chatbot answering questions, and that sentence
is printed as the evidence beside the finding.

The verdicts survived, because four other signals carry them. That is the
uncomfortable part rather than the reassuring one: the signal the specification
was rewritten around was contributing almost nothing on a capture from an
account that streams, and no recorded number would have shown it.

Model byte counts become payload at the point the telemetry is built.
Acknowledgements, handshakes and streaming framing come out; the wire figures
are kept for anything that quotes bytes back to a customer. The classifier now
reads the same whether or not an account streams — the property Finding 3
settled for the aggregation interval, for the same reason.

**G0 moves, and this is a change to a business decision.** Base corpus
separation margin 0.260 to **0.415**, stress corpus 0.142 to **0.291**, recall
and precision unchanged at 1.00, no workload changing side. The ratio midpoint
moves from 7.0 to 11.5 because the feature is on a different scale: agents
above 15.3 and negatives below 8.7, where it was 7.9 and 6.5.

It was never only about streaming. The acknowledgements and certificate chains
were in the ratio of every account ever scanned, and taking them out widened
the gap between the classes from 1.2x to 2x.

Spend figures fall for every existing customer by roughly the protocol overhead
on their traffic. They were too high before, and every report says which
reading produced them.

### The regime is a property of a conversation, not of a workload

Whether responses stream was decided per principal, and a workload that embeds
a query at one endpoint and generates from another has one of each. Summed
together they read as neither, which was written down as a limit of flow logs.

It is not one. Those are two conversations with two different peers, a flow log
is keyed on the 5-tuple, and the peer address is the finest grain the data
supports. Deciding there costs nothing: the assistant that used to fit neither
constant now reads Bedrock whole at 1,739 bytes per data packet and Anthropic
streamed at 241, and `make conversion` reports one row per conversation —
twenty-four of them, 4.12 to 4.29 payload bytes per token, nothing excluded.

### Four bytes a token was right all along, on the right input

The per-regime constant, the framing haircut and the streaming discriminator
are all gone from the spend estimate. With the protocol removed, every workload
in the corpus lands between 4.13 and 4.24 payload bytes per token at both ends
of the conversation, in both captures — so `estimate_tokens` divides both
directions by one number and takes payload rather than wire bytes.

The correction was in the wrong place rather than wrong. Compensating for a
wire-level effect where the bill is computed left every other consumer of the
same numbers reading the uncorrected version, and the classifier was one of
them.

### Four bytes a token, when a streamed response is 187

Every dollar figure on a Custos report comes from one conversion: observed wire
bytes divided by a constant. That constant was four in both directions, which
is right for a JSON body and wrong for a streamed response by around forty-four
times — concentrated in output tokens, which every provider prices at five
times input. The report could have been telling a budget owner that an agent
costs ten thousand dollars a month when it costs a few hundred.

A streaming API flushes after every token, so each token is its own Server-Sent
Events frame, its own TLS record and its own TCP segment: four bytes of text
inside about 183 of protocol. Arithmetic on a documented protocol rather than
an opinion.

A flow record does not say whether a response streamed, which is why this had
been one number for as long as it existed. It carries packet counts, and those
are enough: subtract the acknowledgements the outbound segments imply and the
mean inbound data packet is 1,444–2,740 bytes when responses arrive whole and
232–288 when they stream, with nothing between. Decided per principal, because
an account whose agents stream and whose chatbots do not is two regimes in one
number.

`make conversion` measures it, `make gates` prints five numbers rather than
four, and both reports say which reading produced their figures — it was
inferred rather than supplied, and a figure that sensitive resting on a reading
nobody stated is the shape of the problems the limitations section exists for.

### G0 gates on headroom, not only on margin

Streaming was measured against the classifier as well, and the separation
margin went **up**: 0.260 to 0.371, because the negatives lose more confidence
than the agents do. In the same run the weakest agent went from 0.151 above the
reporting threshold to 0.054 above it.

Both are true of one run and only one was in the gate. A margin is a gap
between two classes and says nothing about where the gap sits, so a change that
widened it and pushed an agent under 0.80 would have read as an improvement in
every recorded number and been a row missing from a customer's report. G0 now
requires the weakest agent to clear the threshold by 0.05 as well, and the
sweep prints both.

### "A question for a human" was three questions, and two had answers

An account reaching a model provider over PrivateLink sends every model call to
a private address in its own subnet, and AWS names the service
`com.amazonaws.vpce.<region>.vpce-svc-0a1b2c3d`. That id explains nothing, and
the previous position was that whatever is behind it is a question for a human.

Taken apart, it is three cases. **The customer named it**: a VPC endpoint is
their own resource and their Terraform usually calls it what it is — a tag that
was already coming back on a call the collector made and was throwing away,
reading one field of the response. **The publisher named it**: a service sold
to other accounts carries a private DNS name its publisher configured, which
for a model provider is their own API hostname, and one further read returns
it. **Nobody named it**: the residue, and a question exactly as before.

The residue is now asked about first. Every other candidate is a machine inside
the account that somebody can walk over and look at; this one is a door into
another company's network carrying a transcript-shaped stream, which is what a
model provider selling into AWS looks like from inside a customer's VPC.

It is not exempted from either test. A published endpoint whose workloads reach
nothing else is declined like any other: better evidence is not evidence, and
an exception carved out here is how a mechanism that must never classify
anything starts classifying.

Measured. A corpus workload was added for it and the question metric went from
**0.50 to 0.67** precision, with both of the corpus's askable model endpoints
now in the list a customer is shown — a property precision cannot see, since a
detector that asks one honest question and misses the other scores 1.00.
`--check` names an endpoint nobody explained before a scan runs, and the report
and the console badge it.

`ec2:DescribeVpcEndpointServices` is a new grant. **Every existing role needs a
re-apply of the Terraform module**; without it an account keeps reporting that
it cannot explain an endpoint, and nothing fails loudly.

### The readability gate could not see a harder interface

`CONTRIBUTING.md` says that when scope readability reaches 100%, that is a
signal to add harder interfaces rather than a result. Doing it was a no-op
against the gate, which was a floor on how many interfaces the resolver got
right: a nameable interface nothing could name left the success count where it
was, and moved only a percentage printed in a log line no assertion read.

It counts the question now. Every nameable interface must be named, by name, or
the test fails with the address and the reason it was expected to be readable.
The estate went from 24 nameable interfaces to 26 in the same arc, which is
what the old gate would have swallowed.

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

### Two reports, and the caveats were in one of them

There are two paths to a report. `custos scan` renders one from the scan it
just ran; `GET /v1/report` renders one from the store a week later, and that is
the copy a customer opens and forwards to their security team.

Three things were in the first and not the second, all for the same reason —
the figure was computed at scan time, used immediately, and never stored:

- the count of public IPv6 destinations reached, which is the one disclosure
  standing between "no findings" and "no findings, and a model endpoint reached
  over IPv6 would not have produced one"
- the records AWS itself dropped before we read them, which is the difference
  between "this account is quiet" and "we were handed less than happened"
- the entire behaviour section: an agent reaching something it has never
  reached, which is the part of this product that only exists because there is
  a week between scans

Nothing failed in any of those cases. The disclosure simply was not in the
document anybody keeps.

Two meta-tests now compare the two construction sites rather than the two
documents — the caveats inside the coverage block, and the sections each report
is rendered with — and a third catches the other direction, a section the
renderer handles for a caller that does not exist. Fields that legitimately
differ are named with their reason.

The same kind of gap existed between `make check` and CI: CI linted the console
and the local gate did not, so a warning that fails the build arrived only
after a push. A test compares those two files now as well.

### A schema comment that would have broken an upgrade

SQLite's ALTER TABLE rewrites the stored CREATE TABLE text, and a comment
inside the parentheses makes DROP COLUMN and RENAME COLUMN fail with
"incomplete input". The `scans` table carried four.

Nothing in the suite could have found it: the schema is created fresh in every
test and only an existing database is ever altered, so it fails on a customer's
file, during an upgrade, in a migration that worked everywhere it was tried.

Found while writing an upgrade test that removes every additive column from a
current database and reopens it — which is what a customer's upgrade does, and
what the previous test only did for one table. A second test then runs a real
ingest against the upgraded file, because columns arriving is not the same as
the code that writes them working.

### Half the account, alternating

The same shape again, in the two lists a person is supposed to act on. Gateway
questions and review candidates were both read as "the most recent scan that
had any" — and a scan is one region's window.

So a customer running one collector per region saw the questions about
us-east-1, then the questions about eu-west-1, then us-east-1 again. Never
both. The set they answered last week was the set that came back this week,
and the set they had not seen yet was invisible. Same for the review band,
where a workload the classifier is permanently unsure about looked as though
it resolved itself every other week.

Both are the surfaces where something we could not decide is handed to a
person. A list that drops half of itself on an alternating schedule is worse
than no list, because the absence reads as a decision somebody made.

Both now take the most recent candidate-bearing scan of each region. Within a
region the newest still replaces the last, or an address somebody answered
comes back beside last week's numbers. A principal uncertain in two regions is
one row at its highest confidence: an IAM role is account-wide, and "is this
principal an agent?" asked twice is not two questions.

Four of these have now been found one at a time, each after the previous was
fixed, so there is a test for the property rather than for the instances: two
regions collected alternately, and nothing the account can see may move when
another window of one of them arrives.

`--check` had a version of it too, in the other direction — it compared the
regions with flow logs against the single AWS_REGION rather than the
configured list, and warned a customer about a region they had already told it
to cover. It also reads one region's records while a collection covers all of
them, which is a reasonable trade and was a silent one: the report now names
the region its gateway, destination-name and IPv6 checks actually read.

### Everything with a memory was comparing two regions against each other

Making a batch one region's window fixed collection and left four
account-wide comparisons reading across regions as though they were one
stream. Each is a different way of telling a customer their workloads changed
when what changed was which region we last looked at.

**Reach was replaced, not merged.** A scan of eu-west-1 overwrote the tools
observed in us-east-1, so an agent appeared to stop touching a billing API the
moment another region was collected. Reach is now kept per region and replaced
per region, unioned into the flat lists every reader already uses — the same
treatment spend got. Credentials and blast radius stay flat: they come from
IAM, and a role that can delete a bucket can delete it from anywhere it runs.

**Behavioural baselines mixed regions.** An agent's behaviour in us-east-1 is a
trend; its observations in two regions interleaved are two trends sampled
alternately. The worst case is the first scan of a second region, where every
internal service that region uses arrives as "reached for the first time in 30
scans" — a finding about a change in our own coverage, delivered to a
workload's owner as a finding about their workload. The same mixing widens the
volume baseline's standard deviation, which suppresses real spikes rather than
inventing them. Wrong in both directions.

**The scan diff compared each region against the other.** The comparison
baseline was whichever scan came before, which on a two-region account is the
other region: every workload in one arrived as APPEARED and every workload in
the other as DISAPPEARED, alternately, every week, for ever. It is the section
a customer reads first.

**Delivery suppressed the second region's drift as a repeat of the first.**
Findings are fingerprinted on account, principal, severity and title, and every
drift finding carries the same title. Two questions about two deployments
hashed to one, and the second was dropped for a fortnight.

Observations carry their region now, baselines and diffs are built per region,
findings name the region they are about on every surface, and the region is
part of a finding's fingerprint — appended only when there is one, so nothing
that never had a region is re-delivered for the sake of a hash change.

### Spend figures that came from a fallback rate now say so

An agent whose model traffic goes to a provider we do not recognise is priced
at a fallback rate, and nothing said which agents those were. On an account
that supplied its own pricing the report says the figures are "estimates at
your prices rather than ours" — true of every row except those, and false for
exactly those.

The report names them, at most four then a count: a list that runs to twenty is
a list that gets skipped, which is the same as not saying it. The console marks
the row, because that is where somebody looks at one agent and decides.

An agent with no providers recorded at all is not accused. That is every agent
discovered before the field existed, and calling it "not recognised" would be a
claim about our schema rather than about the account.

### Two caveats that were not true as written

The IPv6 disclosure said "an agent reaching a provider over IPv6 does not
appear above at all". False for AWS's own model endpoints, and false the whole
time: `pkt-dst-aws-service` describes the destination rather than its address
family, so Bedrock over IPv6 is recognised exactly as Bedrock over IPv4 is. The
count included those addresses too.

Overstating a limitation costs the limitations section the same credibility
that understating one does. Its whole value is that a reader can take what it
says at face value. `--check` and STATUS said the same thing and have been
corrected with it.

### Three things only the console could do

**The CLI could sanction an agent and not retire one.** It could write to the
audit trail and not read it. It printed drift once, at the end of the scan that
found it, and could never show it again.

That is not a cosmetic asymmetry. The control plane is deployed when a customer
wants continuous monitoring; before that — through the whole entry motion,
which is where every customer starts — there is no API at all. Anything only
the API can do is something nobody can do during onboarding, and retiring is
the control that keeps the queue readable: a decommissioned workload nobody
retires keeps surfacing as a finding for ever.

`custos status --to` takes all three states a person may set, with `retire` as
the shorthand OPERATIONS already uses. SANCTIONED is not among them — it is
reachable only through `grant`, which requires an explicit approval scope, and
argparse refusing it at the boundary is a better place to enforce SEC-17 than
an exception somebody has to read. `custos audit` and `custos drift` cover the
two reads.

Found one at a time, each after the previous was fixed, so there is a test that
maps every API route to the command covering it — with two exemptions named,
and a second assertion that the map does not describe routes which no longer
exist.

### Free text from the write surface had no bounds at all

`operator`, `note`, `reason`, `value` and `provider` come from a person through
the only write surface this product has, and end up in three places that
matter: the audit trail, which is the record of who granted what; the report's
provenance section, which is what a security team reads; and a column-aligned
terminal table. Nothing bounded any of them.

Not an injection problem — the report escapes what it renders and the store
parameterises every query. A problem about a record staying readable: an
operator name with a newline in it breaks the artefact somebody reads before
granting authority, and a field with no length limit is a field somebody
eventually puts a log file in.

The collector had the same gap in the other direction: a `Name` tag with a tab
in it, or 256 characters of it, went straight into the scope. Both surfaces
clean through one function now, and a test compares the two files rather than
trusting they agree.

### The destination cache was empty on every window

`DestinationResolver` exists to cache, and its own comment said that without
one the collector re-asks AWS the same question hourly for ever. It was
constructed inside `Collect`, so every window started with an empty one.

Everything else in the daemon was rebuilt per window too: the AWS clients,
whose credentials cache refreshes itself and which are meant to be kept. Both
are kept now, per region — per region matters more than the saving does, since
a private address is a different host in each one and a shared resolver would
answer eu-west-1 with us-east-1's name in the scope an operator approves.

The cache is bounded now that it outlives a window, by a sweep of expired
entries, and dropped whole when a sweep frees nothing. The alternative is a
resolver that reaches its limit with live entries and then silently stops
caching for ever.

### Bedrock over PrivateLink was invisible, and AWS knew the answer

**An account that reaches Bedrock through an interface VPC endpoint had no
agents at all.** The endpoint puts an ENI in the customer's own subnet, so
every model call goes to a private address inside their VPC; the flow log's
AWS service annotation covers AWS's published ranges and a VPC endpoint is not
in one, so the record says nothing. On the wire it is an internal API on 443.

The corpus workload added for it scored 0.039 — the floor. It now scores 0.970
and nobody was asked to declare anything.

PrivateLink is what a security-conscious platform team turns on so their model
traffic does not cross the public internet. The account most likely to have it
is the account most likely to buy a tool for governing agents, and it was the
account where a scan came back empty and confident.

The collector asks DescribeVpcEndpoints about the endpoints its own traffic
already reached — a new permission on a role deliberately cut to 21 actions,
and the first one added back — and ships the service name beside the address.
The classifier reads the last segment: `bedrock-runtime`,
`bedrock-agent-runtime`, `sagemaker-runtime`, and nothing else. An endpoint for
`com.amazonaws.<region>.s3` is traffic to S3.

Kept out of the customer's declaration list on purpose. A declaration is a
person saying "this is our model gateway", and SEC-24 exists because that claim
can manufacture agents in a region nobody meant it to cover. This is AWS
answering about one ENI in the region the batch came from, and putting it in a
record of the customer's decisions would make that record false. The report
names it in its own sentence instead — without which an account with a resolved
endpoint reads word for word like one with a hidden gateway and nothing found.

`--check` says it before the first scan, in both directions: reassuring,
because a flow log makes that account look like it has no model traffic at all,
and a fact about their architecture the first conversation should surface.

**Bedrock traffic had no provider.** Found on the way. `provider_for` decides
`bedrock` from the flow log's service annotation and the one caller passed only
the address, so three of the corpus's eight agents were attributed to nobody
and priced at a fallback rate. The dollar figures do not move on the built-in
table — it prices bedrock and unknown identically — but an account that
supplied its own Bedrock rate was not having it applied to the agents that use
Bedrock, which is the entire reason the rate mechanism exists.

### How much of a scope an operator can read, measured

**46%, and now 100% of what is nameable.** The register's scope is what
somebody reads before conferring authority, and an entry they cannot read is an
approval they have to guess at. STATUS has said since A0 that how much of a
real scope is readable is unmeasured.

So: an estate of interfaces shaped the way AWS returns them, with the tag
hygiene a company that adopted agents bottom-up actually has — a few things
carefully labelled, a lot of managed infrastructure carrying AWS's own
descriptions, and a long tail nobody has ever named. Six of them have no honest
answer and are there for that reason; a corpus that names everything measures
nothing.

What was being thrown away:

- **`InterfaceType`**, a closed enum AWS sets itself, present on exactly the
  interfaces nobody tags. Every account has a NAT gateway and none of them has
  ever had a Name tag.
- **Six more descriptions AWS writes**: Redshift, DMS, EFS mount targets, EKS
  cluster and pod interfaces, and the NAT gateway description that predates the
  interface type.
- **AWS-reserved tags.** `aws:ecs:serviceName` names an ECS task after its
  service. The `aws:` prefix cannot be written by a customer, so these are
  AWS's own metadata rather than a label somebody typed.
- **The instance behind the interface.** The common case: people tag instances,
  and the console does not even show a Name field for the ENI it creates.
- **The one security group somebody named**, when nothing else answered and
  exactly one group is not `default`, `launch-wizard-3`, or generated by a
  controller.

And one thing that was being spent badly: a Name tag holding
`i-0a1b2c3d4e5f60718` was forwarded as a name. Honest, and exactly as useful to
an approver as the address beside it. The scope has one readable column.

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
