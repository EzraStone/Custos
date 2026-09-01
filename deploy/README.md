# Deploying the control plane

The control plane is one process and one SQLite file. That is deliberate: at
pilot volume a database server is operational cost with no matching benefit,
and the move to Postgres is a connection string when it is needed.

## Run it

```
export CUSTOS_TOKENS="447120043318:$(openssl rand -hex 24)"
docker compose -f deploy/compose.yaml up --build
```

`CUSTOS_TOKENS` is a comma-separated list of `account:token` pairs. Generate one
token per customer account, hand it to them with their collector configuration,
and never commit it — a credential in a repository is a credential in every fork
of it.

## Configuration

| Variable | Meaning |
|---|---|
| `CUSTOS_DB` | Path to the SQLite file. Default `custos.db`. |
| `CUSTOS_TOKENS` | `account:token` pairs, comma separated. |
| `CUSTOS_SLACK_WEBHOOK` | Optional. Findings go here as they are ingested. |
| `CUSTOS_SIEM_WEBHOOK` | Optional. Every field of every finding, one request per scan. |
| `CUSTOS_SIEM_HEADERS` | Optional. `X-Api-Key: secret, X-Tenant: acme`. |

A token appearing against several accounts covers all of them, which is how one
customer's fleet is expressed:

```
CUSTOS_TOKENS=111111111111:tok-acme,222222222222:tok-acme
```

That is not multi-tenancy. Every account behind a token belongs to one
customer, and a token still cannot reach an account it was not issued for.

An unconfigured control plane authenticates nobody. That is the correct
direction for that failure to fall.

## Without Docker

```
make setup
CUSTOS_TOKENS="447120043318:dev-token" \
  .venv/bin/uvicorn custos.api.main:app --port 8080
```

## Without a server at all

The first customer scan does not need any of this. A collector dry-run writes a
batch to a file and the CLI turns it into a report:

```
custos --db acme.db scan batch.json --out report.html
```

No service to deploy and nothing extra to explain in a security review, which
matters more than convenience when the entry motion has to clear a platform
lead's bar without a meeting.

## Notifications

With `CUSTOS_SLACK_WEBHOOK` or `CUSTOS_SIEM_WEBHOOK` set, findings are delivered
as batches arrive. Without them the control plane is report-only, which is a
supported state — a first scan is read directly, and delivery is what a customer
turns on when they want to stop opening reports.

A delivery failure never rejects a batch. The findings are in the register
either way, and turning a Slack outage into lost telemetry would be the wrong
trade.

See [../docs/DELIVERY.md](../docs/DELIVERY.md) for what gets sent and what
deliberately does not.

## Backups

The database is a single file. Copy it. With WAL enabled a live copy can be
inconsistent, so use SQLite's own backup rather than `cp`:

```
sqlite3 custos.db ".backup '/backups/custos-$(date +%F).db'"
```

The register is the product. Losing it loses every sanction decision a customer
has made, and asking them to re-review forty agents is a conversation that ends
a pilot.
