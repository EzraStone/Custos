# Working in this repository

## Before you commit

```
make check
```

CI runs exactly these targets. If `make check` passes, CI passes.

## The five invariants

`docs/SECURITY-INVARIANTS.md` lists SEC-16 through SEC-20. Each one names the
test that enforces it.

**A change that removes or weakens one of those tests is a change to the
invariant, not a refactor.** They are written so that breaking one requires
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

## Changing the corpus

`a0/tests/test_corpus.py` asserts the corpus is hard: volume alone must not
separate the classes, a negative must interleave tool calls, a negative must
accumulate context, and some negatives must have no inbound correlation.

Those tests exist so the corpus cannot quietly become easy and turn a passing
gate into an artefact. Making the corpus harder is welcome. Making it easier
needs a reason in the commit message.

## Changing the G0 result

`a0/tests/test_g0.py` pins the numbers that were the basis for proceeding past
the gate. If your change moves them, that is a change to a business decision.
Say so in the commit message and update `docs/A0-FINDINGS.md` in the same
commit.

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
