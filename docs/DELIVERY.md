# Getting findings to a human

A report someone has to remember to open is a report that gets opened twice.
Delivery is how the third week still works.

The whole design is about restraint. The failure mode of a security channel is
not missing an alert — it is sending so many that the channel gets muted, after
which every alert is missed and the integration looks like coverage while
providing none.

## What gets sent

| | |
|---|---|
| **First scan** | One summary. Not one alert per agent — forty messages on day one is how a channel gets muted before it has ever been useful. |
| **Every scan after** | Only what changed. The register is in the report; repeating it weekly turns the channel into wallpaper. |
| **Drift** | Always as a question, never as a conclusion. |

## Severity

Three levels, because a scale with more than three gets argued about instead of
acted on, and the argument is always about the middle.

| | Meaning | Repeats after |
|---|---|---|
| `act_now` | A credential gained permissions that increase what it could destroy | 3 days |
| `review` | A new agent, or one reaching somewhere new | 14 days |
| `note` | Context. Delivered in digests, never alone | 30 days |

Drift is capped at `review` and can never be `act_now`. A departure from a
baseline is a question, and paging someone at 02:00 for one claims a certainty
the data cannot carry — the first false alarm costs every later alert its
credibility.

## Suppression

A finding already delivered is not delivered again until its repeat window has
passed. Three days for an escalation is long enough not to nag and short enough
that something nobody acted on returns before it is forgotten.

Suppression is **per channel**. A finding sent to Slack has not been sent to a
SIEM, and treating them as one silently drops half the integration a customer
paid for.

Delivery is recorded **after** a channel reports success. Recording first would
drop a finding whenever a webhook was briefly down — exactly when someone is
most likely to need it.

Resolutions are never announced. Nobody wants a message saying an alert they
ignored has gone away, and sending one doubles the volume for no decision made.

## Channels

**Slack** is for a person deciding whether to act today. One message per scan,
`act_now` findings in full, everything else counted. Every line names an owner,
because a finding a reader cannot route is one they scroll past.

**Webhook** is for a SIEM correlating this against everything else. Every field
of every finding, one request carrying an array. Truncating for readability
would drop the fields it joins on, and separate posts bill for events it cannot
correlate.

Both are best-effort. A delivery failure never fails a scan — the findings are
in the register and the report either way, and a scan that aborted because a
webhook was down would lose the data as well as the notification.

## Configuration

```
CUSTOS_SLACK_WEBHOOK   https://hooks.slack.com/services/...
CUSTOS_SIEM_WEBHOOK    https://siem.example.com/ingest
CUSTOS_SIEM_HEADERS    X-Api-Key: secret, X-Tenant: acme
```

```
custos --db acme.db scan batch.json --notify
```

No channels configured is a supported state, not a warning. A first scan is run
by hand and read directly; delivery is what a customer turns on when they want
to stop reading reports.

Plaintext endpoints are dropped rather than used. A finding names a customer's
principals and their blast radius, and sending that over `http` is a worse
outcome than not delivering it.
