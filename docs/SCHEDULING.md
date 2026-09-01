# Running the collector continuously

A one-shot scan proves the product. Continuous collection is what makes the
unsanctioned set regenerate, which is what makes this a subscription.

```
CUSTOS_DAEMON=1 \
CUSTOS_WINDOW=1h \
CUSTOS_STATE_PATH=/var/lib/custos/cursor.json \
  ./custos-collector
```

## The cursor

The only difficult part of scheduling is knowing which window to collect next.

Naive scheduling — every hour, collect the last hour — loses data on every
restart, every deploy, and every run that overruns its interval. **The gaps are
invisible.** The next scan reports fewer agents, which is indistinguishable from
an account that has fewer agents.

So the collector records the end of the last window it shipped **successfully**,
and the next window starts exactly there.

- A crash costs latency, not data. The next run covers the gap.
- A failed send never advances the cursor. A window that did not arrive has not
  been collected.
- The same window arriving twice is fine. Ingestion is idempotent on
  `(account, window)`, so a retry replaces rather than doubles.

Catch-up is bounded at 24 hours. A collector down for a month should not replay
a month of flow logs: they have usually aged out, the API calls cost the
customer money, and the resulting scan describes an account that no longer
exists. Beyond the bound it starts fresh and says so.

## Busy hours

A window that would exceed the record limit is **shortened** to what was
actually read, and the cursor resumes there. A busy hour costs extra windows
rather than data.

If the summary keeps reporting shortened windows, the interval is too long for
that account. Shorten it — the cursor handles the transition without a gap.

## Where to run it

Anywhere with the role and an outbound HTTPS path. In practice:

| | |
|---|---|
| **ECS scheduled task** | Most common. The customer already has a cluster and a task role. |
| **Lambda on an EventBridge rule** | Fine for hourly. Watch the 15-minute timeout on a first catch-up. |
| **A container on a bastion** | Simplest for a pilot. `restart: unless-stopped` and a volume for the cursor. |

The cursor needs a durable path. Without one a restarting daemon re-collects a
window and loses nothing, but a daemon that restarts often never makes progress
past its interval.

## Before the first run

```
./custos-collector --check
```

Every onboarding failure produces the same symptom: a report with no findings.
`--check` names which one it is, before an hour is spent reading an empty
report.

## Jitter

Runs are spread by up to five minutes. Scheduled collectors otherwise
synchronise on the minute they were deployed and produce a thundering herd
against each customer's own rate limits.
