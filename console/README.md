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

So three things are deliberate and should not be optimised away:

- **No bulk approve.** Forty agents is forty decisions. A button that approves
  them together is a button that approves them unread.
- **Evidence before approval.** The grant control stays disabled until the
  finding's evidence has actually been opened.
- **The scope is shown before it is granted.** An operator approving an agent is
  approving what it was seen doing, and the tools and data stores in that scope
  are listed on the button's own dialog.

The fourth is not a constraint but a consequence of the same reasoning:
**every finding carries its history**. Who sanctioned an agent and when is
recorded whether or not anyone reads it, and a record nobody can read is a
record nobody can check. The history section is where an operator finds out
that the agent in front of them was approved last March by someone who has
since left. It loads on open, because forty findings would otherwise be forty
audit requests for history nobody asked to see.

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

The console is served by the control plane rather than deployed separately.
That is one fewer thing to run, and it means the console and the API share an
origin — so there is no CORS configuration to get wrong, and no second place a
credential has to be present.
