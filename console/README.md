# Custos console

The operator surface for the sanction workflow. Everything else — discovery,
classification, reporting — happens without a human; this is the one place a
person makes a decision.

That decision is granting imprimatur, and it is the only action in the entire
system that confers authority. SEC-17 says a discovered agent has no standing
until an operator explicitly grants it, and the console is where "explicitly"
either means something or does not.

## The design constraint

A console makes sanctioning easy. If sanctioning is easier than reading the
evidence, the register becomes a rubber stamp and SEC-17 is upheld in code while
being defeated in practice.

So these are deliberate and should not be optimised away:

- **No bulk approve.** Forty agents is forty decisions. A button that approves
  them together is a button that approves them unread.
- **Evidence before approval.** The grant control stays disabled until the
  finding's evidence has actually been opened.
- **The scope is shown before it is granted.** An operator approving an agent is
  approving what it was seen doing, and the tools and data stores in that scope
  are listed on the button's own dialog — by name where one exists, and by
  address where none does, because an invented name is worse than an honest
  address.
- **A filtered list always shows the total.** "1 agent" on its own is how
  somebody concludes an account is nearly clean while looking at a third of it,
  and an empty filtered list is worded so it cannot be read as an empty account.
- **`sanctioned` is not in the status control.** There is one door to it, and a
  dropdown containing it would be a second.

One more is not a constraint but a consequence of the same reasoning:
**every finding carries its history**. Who sanctioned an agent and when is
recorded whether or not anyone reads it, and a record nobody can read is a
record nobody can check. The history section is where an operator finds out
that the agent in front of them was approved last March by someone who has
since left. It loads on open, because forty findings would otherwise be forty
audit requests for history nobody asked to see.

## What else is on a finding

**Behaviour** compares an agent against its own history and puts what changed
as a question — "it reached `rds 10.0.9.45` for the first time. Is that
expected?" A question gets answered; an accusation gets argued with. Nothing is
shown until there is enough history for the comparison to mean something,
because drift over two observations is noise and a heading would give it the
standing of a finding.

**Retire** is the control that keeps the queue readable. A decommissioned
workload nobody retires keeps surfacing as an unsanctioned finding forever, and
a queue full of dead roles is a queue nobody reads — after which the real
finding in the middle of it goes unread too. It asks for a reason, because a
decision with no reason is one nobody can review later.

Above the register, **what changed since the last scan**. That is the whole
argument for scanning twice: a report repeating last week's findings verbatim
gets skimmed the second time and deleted the third.

## Running it

```
npm install
npm run dev          # proxies /v1 and /healthz to a control plane on :8080
npm run build        # emits dist/, which the control plane serves
npm test
```

Point the dev server somewhere else with `CUSTOS_API=http://host:port npm run
dev`. The proxy exists so development has the same shape as production —
same origin, relative paths — rather than a base URL that is configured in one
environment and wrong in the other.

## The smoke

`make smoke` builds a batch from the A0 corpus, scans it into a fresh database,
serves the control plane with the console mounted, and drives the built bundle
in a real browser: sign in, read five real findings, open the evidence, load an
agent's history, grant imprimatur, and confirm the sanction is recorded against
the operator's name.

It drives both mutations — granting and retiring — because a status
transition the server refuses looks identical to one it accepted until the
register reloads, and that is the kind of disagreement only a real browser
against a real control plane surfaces.

It is the only test that touches the built bundle. Everything under `src/`
mocks `fetch`, which cannot catch a request shape the server rejects or a
static mount that shadows an API route. It is not in CI — it needs a browser —
and it skips rather than fails when the pieces are missing.

The console is served by the control plane rather than deployed separately.
That is one fewer thing to run, and it means the console and the API share an
origin — so there is no CORS configuration to get wrong, and no second place a
credential has to be present.
