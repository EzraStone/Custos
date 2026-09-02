# Working in this repository

## Before you commit

```
make check
```

CI runs exactly these targets. If `make check` passes, CI passes.

## The eight invariants

`docs/SECURITY-INVARIANTS.md` lists SEC-16 through SEC-23. Each one names the
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

## Meta-tests

Several tests in this repository test the repository rather than the product:
every invariant cites a test that exists, every API route is in `docs/API.md`
and nothing else is, every relative link resolves, the Terraform log format
matches the parser, the Go wire types match the Pydantic models.

They exist because each of those drifts silently. A renamed test leaves a
citation pointing at nothing; an undocumented route is a capability nobody can
use; a documented route that was removed is something an integrator builds
against and discovers at runtime.

Add one whenever you notice two places that have to agree and nothing making
them.

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
