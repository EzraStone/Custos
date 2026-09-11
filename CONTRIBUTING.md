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

Add one whenever you notice two places that have to agree and nothing making
them. The shape to look for is not a bug that fails; it is a claim in one place
that nothing obliges a second place to match.

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

## Changing the G0 result

`a0/tests/test_g0.py` pins the numbers that were the basis for proceeding past
the gate. If your change moves them, that is a change to a business decision.
Say so in the commit message and update `docs/A0-FINDINGS.md` in the same
commit.

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
