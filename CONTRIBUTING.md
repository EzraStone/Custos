# Working in this repository

## Before you commit

```
make check
```

CI runs exactly these targets. If `make check` passes, CI passes.

## The nine invariants

`docs/SECURITY-INVARIANTS.md` lists SEC-16 through SEC-24. Each one names the
test that enforces it.

**A change that removes or weakens one of those tests is a change to the
invariant, not a refactor.**

Renaming one counts. `test_invariant_coverage.py` asserts that every test name
cited in the document exists, that every invariant cites at least one, and that
CI's invariants job mentions all of them — because a citation pointing at a
test that was renamed is a guarantee nobody can verify, and it found exactly
that on its first run. They are written so that breaking one requires
deliberately editing a test named after the thing you are breaking — that is the
mechanism, and it only works if nobody routes around it.

If an invariant is genuinely in the way, the move is to argue that it should
change, in a commit that says so. Not to loosen the test.

## Adding a classifier signal

Signals live in `controlplane/custos/classify/signals.py` with a weight and a
sentence a human can argue with.

Before adding one, measure it on the A0 corpus. Three signals that looked
obvious are in `REJECTED` in that file with the numbers that killed them, and
two of them were in the original specification. The corpus exists so that
"this signal should work" can be replaced with "here is what it does".

A signal that improves accuracy on the corpus but cannot be explained in one
sentence to the engineer who owns the workload is not worth having. Every
finding gets challenged by that person, and a confidence score loses that
argument.

## Check that a test fails

A test that passes against broken code is worse than no test: it is a claim
nobody has verified, sitting where a check appears to be.

Before committing a test for a bug you just fixed, break the fix and watch the
test go red. It takes a minute and it has been worth it every time. Three tests
written during the destination-naming work passed against deliberately broken
code:

- one asserted a duplicate could not appear in an approval scope, when keying
  that scope by address had already made the shape impossible — it was checking
  nothing;
- one exercised a merge that could never run, because the record it fed in was
  filtered out two steps earlier;
- one used `offsetParent` to decide whether a control was visible, which is
  null for every element under jsdom, so the focus trap it tested silently did
  nothing.

Each looked correct. Each was found by breaking the code under it on purpose.

The same applies to a test for behaviour you are keeping: if you cannot think of
an edit that would make it fail, it is describing the code rather than
constraining it.

**Put back exactly what you took out.** A mutation check reverts by replacing
the mutated text with the original — and if that text appears twice in the
file, the revert changes both. That happened here: the same expression sat in
an insert branch and an update branch, the revert gave both the update's
version, and inserting a new row started dereferencing a variable that is None
by definition. Ninety-one tests said so.

Anchor the revert on enough surrounding lines to be unique, or edit by line
number. And read the suite output before committing, which is the step that
would have caught it anyway.

**Clear `__pycache__` after a fast revert.** Break, run, restore, run — done
quickly enough, the restore lands inside the same filesystem-timestamp second
as the break, Python keeps the cached bytecode of the broken version, and the
suite reports a failure that is not there. It looks exactly like a revert that
did not apply, which is the worst thing for it to look like: the obvious next
move is to go looking for a bug in code that is already correct.

```
find controlplane -name __pycache__ -exec rm -rf {} +
```

Go does not have this problem — its build cache keys on content rather than
timestamps.

## Read the arc back before you call it done

Three defects in one week came from re-reading code written a day earlier, and
none of them moved a number. No workload in either corpus had an unreadable
response, a six-packet model conversation or an acknowledgement-only window, so
every gate was identical before and after the fixes. A green suite said nothing
about any of them.

Two questions find this class:

**What does this do when the input is degenerate?** Empty, clamped, one packet,
one window. The arithmetic in this repository is full of denominators, and a
denominator that reaches zero does not produce an error — it produces a
confident number at the edge of its range.

**Which way does it err?** Every one of those three pointed at false
positives, and in hindsight a correction whose whole job is to *remove* bytes
from a denominator was always going to err that way. Knowing the direction in
advance is most of finding them.

## Meta-tests

Several tests in this repository test the repository rather than the product:
every invariant cites a test that exists, every API route is in `docs/API.md`
and nothing else is, every relative link resolves, the Terraform log format
matches the parser, the Go wire types match the Pydantic models.

They exist because each of those drifts silently. A renamed test leaves a
citation pointing at nothing; an undocumented route is a capability nobody can
use; a documented route that was removed is something an integrator builds
against and discovers at runtime.

Three more were added after the same failure happened three times:

- **`test_both_reports_disclose.py`** — the two report paths have to carry the
  same caveats and the same sections. Three disclosures lived in the report the
  CLI prints and not in the one served a week later, which is the copy a
  customer forwards, each because a figure was computed at scan time and never
  stored. Nothing fails when that happens; the caveat is simply absent.
- **`test_report_is_account_wide.py`** — the served report route may not read a
  per-scan figure off the latest scan, because a scan is one region.
- **`test_local_gate_matches_ci.py`** — `make check` has to run what CI runs. A
  check that exists only in CI arrives after a push.
- **`test_cli_covers_the_api.py`** — every API route has a CLI equivalent or a
  stated reason. Before a control plane is deployed the CLI is the only
  surface, and that is the phase every customer starts in.
- **`test_write_surfaces_agree.py`** — the API and the CLI bound their free
  text the same way. They write the same audit rows, and a bound on one is
  worth nothing if the other is the path a person takes.
- **`test_model_services_agree.py`** — the collector's preflight and the
  control plane recognise the same AWS endpoint services. If they drift,
  `--check` promises something the scan does not deliver.
- **`a0/tests/test_conversion.py`** — the two constants behind every dollar
  figure are held against the corpus they were measured in. Neither is
  derivable from anything in the control plane, and one of them is the
  measured figure adjusted for a framing haircut taken somewhere else in the
  file, so the two drifting apart produces a plausible number and no error.

Add one whenever you notice two places that have to agree and nothing making
them. The shape to look for is not a bug that fails; it is a claim in one place
that nothing obliges a second place to match.

## A capability only one surface has

There are three ways a person touches this product: the console, the API and
the CLI. The console and the API are the same surface — the console is an API
client — and the CLI is not.

That matters because of when each one exists. The control plane is deployed
when a customer wants continuous monitoring. Before that, through the whole
entry motion, there is no API and no console: there is a binary, a SQLite file
and the CLI. **Anything only the API can do is something nobody can do during
onboarding**, which is the phase every customer is in at least once and most
are in when the product has to prove itself.

Three were found one at a time, each after the previous was fixed: the CLI
could sanction an agent and not retire one, could write to the audit trail and
not read it, and printed drift once at the end of the scan that found it.

`tests/test_cli_covers_the_api.py` maps every route to the command covering it.
Adding a route means adding a line to that map — either the command that covers
it, or an exemption with the reason. A second assertion fails if the map names
a route that no longer exists, because otherwise it passes by describing a
surface nobody has.

## An account-scoped answer from a per-region row

The most expensive class of bug in this codebase so far, and it has been
written six separate times.

A batch is one region's window. Everything a customer sees is about their
account. Any code that answers an account-wide question by reading the latest
scan, the highest scan id, or the previous scan is answering from one region,
and the symptom is always the same: **the answer alternates with whichever
region was collected last.**

Found and fixed, one at a time, each after the previous one was fixed:

- the report labelled the whole register with the latest scan's region
- one region's flow log format was described as the account's
- the incomplete-coverage banner was suppressed by a healthy region
- reach was replaced rather than merged, so a rescan erased another region's
- behavioural baselines mixed two regions into one trend
- the scan diff compared each region against the other
- gateway questions and review candidates showed one region at a time

Before writing a query that answers something about an account, ask which
region the row it reads belongs to. If the answer is "whichever came last",
the query is wrong even when its test passes — because a single-region test
account, which is every test account, cannot tell the difference.

`tests/test_two_regions_alternate.py` asserts the property directly: two
regions collected alternately, and nothing the account can see may move when
another window of one of them arrives. Add to it rather than only fixing the
instance.

## Changing the interface estate

`collector/internal/ingest/eniestate_test.go` is the corpus for destination
naming: interfaces shaped the way AWS returns them, with the tag hygiene a real
account has. `TestScopeReadability` is the number it produces, and `make gates`
prints it beside the classifier's margins.

Two rules, both of which this file has already broken once.

**A shape has to be a shape AWS writes.** The Lambda entry was spelled
`AWS Lambda VPC ENI-checkout-worker-a1b2` when AWS writes a full 36-character
UUID, and the estate reported a miss that did not exist. A corpus of
approximations measures the approximations.

**It has to contain what cannot be named.** Six of its interfaces have no
honest answer — free text somebody typed, an untagged ENI on an untagged
instance, a security group called `default` — and they are there because a
corpus that names everything measures nothing. When readability reaches 100%,
that is a signal to add harder interfaces rather than a result.

**The gate counts the question, not the answer.** Every nameable interface must
be named, by name, or `TestScopeReadability` fails with its address and the
reason it was expected to be readable. `nameableFloor` records how many there
are and is checked in both directions: the estate cannot grow without somebody
recording that it did, and it cannot shrink into something easier.

It was the other way round once — a floor on how many the resolver got right —
and that had a hole in it exactly the size of the rule above. A nameable
interface nothing could name left the success count where it was, met the
floor, and moved only a percentage printed in a log line no assertion read. So
the instruction to add harder interfaces was a no-op against the gate meant to
enforce it.

## Changing the corpus

`a0/tests/test_corpus.py` asserts the corpus is hard: volume alone must not
separate the classes, a negative must interleave tool calls, a negative must
accumulate context, and some negatives must have no inbound correlation.

Those tests exist so the corpus cannot quietly become easy and turn a passing
gate into an artefact. Making the corpus harder is welcome. Making it easier
needs a reason in the commit message.

**Ask what a new signal looks like when it is absent, and make sure the corpus
contains that.** This has been got wrong three times, always in the flattering
direction: signal availability derived per principal rather than per capture, a
corpus that annotated both ends of a flow record when AWS annotates one, and
destination names that resolved for every endpoint when real accounts have
untagged ENIs. Each made the product look better here than it would in a
customer's account, and each hid a real defect until someone went looking.

## Adding or removing a signal

`a0/custos_a0/ablation.py` measures what each one is worth: remove it,
re-measure the margin. Run it before arguing about a weight.

**Ask both corpora.** The base corpus is easy enough that four of five signals
are redundant, so a signal can read 0.000 there and be the difference between
separated classes and overlapping ones on the stress corpus. `make gates`
prints the stress table for that reason.

**A signal reading zero is a question, not an answer.** It means either the
signal does nothing or the corpus never contains the case it was carried for,
and those need opposite responses. The way to tell them apart is to add the
case and measure again — `mcp_fingerprint` read 0.000 on both corpora until a
workload existed that needed it, and then removing it made the classes
overlap.

**A signal reading negative is different.** `offhours_activity` was costing
0.07 of margin on both corpora, and no corpus addition fixes a signal that is
separating on the wrong question. It was rejected with the reason written into
`REJECTED`, which is where a signal goes rather than into a lower weight: a
weight says "worth a little", and the measurement said "worth less than
nothing".

**A feature nobody reads goes with the signal that read it.** `Features` is the
documented interface between what can be observed and what can be concluded, so
a field in it reads as evidence the classifier weighs — to the next person
deciding whether a new signal is redundant, and to anyone reading the class to
learn what this product can see. Six of them were being computed for every
principal on every scan, five for signals rejected months earlier.
`test_leakage.py` fails on an orphan now. The knowledge lives in `REJECTED`,
which is what stops somebody re-adding a signal that was already measured; a
dead field beside a live one does the opposite.

**Do not move a threshold to make a workload land where you want it.** That is
fitting to eleven workloads. If a removal leaves a workload a hundredth from a
threshold, record the clearance — the sweep prints it — and leave the
threshold alone.

## Changing the G0 result

`a0/tests/test_g0.py` pins the numbers that were the basis for proceeding past
the gate. If your change moves them, that is a change to a business decision.
Say so in the commit message and update `docs/A0-FINDINGS.md` in the same
commit.

**There are two numbers, and they can move in opposite directions.** The
separation margin is the gap between the classes; the headroom is how far the
weakest agent sits above the threshold at which it is reported at all. A margin
says nothing about where the gap is. Streaming responses widened the margin
from 0.260 to 0.371 and cut the headroom from 0.151 to 0.054 in one run, and a
change that widened the margin while pushing an agent under 0.80 would read as
an improvement in every recorded number and be a row missing from a customer's
report.

## Modelling the wire

`a0/custos_a0/wire` turns ground-truth calls into the telemetry a collector
could read, and it is the most dangerous file in the repository to be casually
wrong in. Everything the classifier is measured against comes out of it, and an
omission there is a corpus that is easier than production in a way no test can
see.

**Model the protocol, not the payload.** The response side of a model call was
four bytes per output token for a year, which is the payload. What a flow log
counts is packets: a streaming API sends every token in its own SSE frame, its
own TLS record and its own segment, which is 187 bytes for the same token. That
was not a measurement anybody had to take — the envelope is documented and the
rest follows — and it was wrong by forty-four times in the direction that
flatters the product.

**Assert that the capture is physically possible.**
`a0/tests/test_capture_is_possible.py` checks what an Ethernet path imposes: no
packet smaller than its own headers, none larger than the MTU, bytes and
packets agreeing about whether anything happened, every record inside the
window the capture claims. Four of those six passed the first time they were
written and two did not, and one of the two was a real hole — the capture
contained traffic from after the window it advertised.

**When a protocol detail has two settings, model both and assert neither.**
Whether real agent traffic streams is a question about customers. The corpus
can be built either way, both are in the sweep, and the difference is a number
rather than an argument. A detail made unrepresentable is a detail decided by
whoever wrote the model.

## Adding a delivery channel

The failure mode of a channel is not missing an alert. It is sending so many
that the channel gets muted, after which every alert is missed and the
integration looks like coverage while providing none.

So a new channel inherits three rules, and none of them is optional:

- Suppression is per channel and runs **before** sending, so an outage does not
  consume a finding's one delivery.
- Delivery is recorded **after** a channel reports success, so a finding that
  failed to send is still deliverable next scan.
- A failure is returned, never raised. A scan that aborted because a webhook was
  down would lose the data as well as the notification.

Get either ordering backwards and the failure is silent: a finding that was
never delivered and never will be.

## Commit messages

Say what changed and why the alternative was worse. The what is visible in the
diff; the why is the only thing a commit message can add that nothing else can.

Several decisions in this repository look arbitrary until you know what was
measured — the disposition thresholds, the gap tolerance, the choice of an
allowlist over a denylist. Those all have reasons, the reasons are in the commit
messages, and that is where the next person will look for them.

## Style

Python: `ruff` settings in each `pyproject.toml`. Timezone-aware datetimes are
enforced; flow log timestamps are UTC and a naive datetime silently comparing
against one is a class of bug worth making impossible.

Go: `gofmt`, `go vet`, and tests run with `-race`.

Comments explain why, not what. The code says what.
