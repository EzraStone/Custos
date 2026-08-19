# Custos collector

Read-only discovery of AI agents running in an AWS account.

This binary runs in **your** account, under **your** credentials, and reads
network and identity metadata. It installs nothing, mutates nothing, and never
reads a payload byte.

Apache-2.0. Read the source — that is why it is open.

---

## Setting it up (target: 30 minutes, no meeting)

**1. Look at what it does before granting it anything.**

```
go build ./cmd/custos-collector
./custos-collector --explain
```

**2. Apply the Terraform.**

```
cd deploy/terraform
terraform init
terraform apply -var="external_id=<the value we sent you>"
```

This creates one IAM role with read-only permissions and an explicit deny on
every mutating action. It creates no compute and touches no existing resource.

**3. Send us the `role_arn` output.** That is the only thing we need.

**4. Optional, and worth it:** set `CUSTOS_ACCESS_LOGS` to the S3 location of
your load balancer access logs. Without them our recall drops from 100% to 60%
on our test corpus, and the agents we miss are the low-volume ones — which are
usually the ones you would most want to know about.

We take four fields from those logs: timestamp, target address, and the two
byte counts. The URL, query string, user agent, client IP, and trace ID are
discarded at parse time and cannot reach us. See
`internal/ingest/accesslogs_test.go`, which proves it against a line containing
an email address and an API key.

---

## What it reads

| Source | What we take |
|---|---|
| VPC Flow Logs | Addresses, ports, byte counts, packet counts, timings, TCP flags |
| CloudTrail | Which principal is attached to which network interface |
| IAM (read-only) | Role tags, IAM paths, attached policy actions |
| ALB access logs *(optional)* | Timestamp, target address, and two byte counts — nothing else |

Flow logs are read from wherever you already deliver them — set
`CUSTOS_FLOW_LOGS` to a CloudWatch Logs group name or to `s3://bucket/prefix`.
We do not ask you to change your delivery destination.

## What it sends

Exactly the structures in [`internal/wire/wire.go`](internal/wire/wire.go).

There is no field on any of those types capable of holding a prompt, a
completion, or any other payload body, and the shipper's only entry point
accepts nothing but those types:

```go
func (s *Shipper) Send(ctx context.Context, batch wire.Batch) error
```

Those two files are the complete proof. This is not a redaction step that could
be misconfigured — there is no path from a payload byte to the network.

Verify it yourself:

```
go test ./internal/wire/    # walks every wire type by reflection
```

To see the literal bytes rather than take our word for it, against your real
account:

```
CUSTOS_DRY_RUN=1 \
CUSTOS_FLOW_LOGS=/aws/vpc/flowlogs \
CUSTOS_ACCOUNT_ID=<account> AWS_REGION=<region> \
  ./custos-collector
```

Or without granting any access at all, against an export you hand us:

```
CUSTOS_DRY_RUN=1 CUSTOS_FLOW_LOGS=x ./custos-collector --from-file <export>
```

## What it cannot do

Write anything.

Every AWS call goes through [`internal/awsread`](internal/awsread/awsread.go),
which refuses any operation whose verb is not `Describe`, `Get`, `List`,
`Filter`, `Lookup`, `BatchGet`, or `Search` — before a request is constructed.
An allowlist, so a verb AWS invents next year is refused by default.

The IAM policy grants no write permission, and a second policy explicitly denies
every mutating action alongside it. That deny changes nothing today; it exists
so that a future widening of the grant, by us or by someone on your side, still
cannot write.

`logs:StartQuery` is denied by name. Logs Insights would be a convenient way to
read flow logs and it creates a billable resource in your account.

Every operation the collector attempts is recorded and available to you.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `CUSTOS_ENDPOINT` | to send | Control plane, https only |
| `CUSTOS_TOKEN` | to send | Credential you hold |
| `CUSTOS_FLOW_LOGS` | yes | Log group or S3 prefix |
| `CUSTOS_ACCESS_LOGS` | no | ALB access logs; improves recall |
| `CUSTOS_ACCOUNT_ID` | no | Account being scanned |
| `CUSTOS_WINDOW` | no | Collection window, default `1h` |
| `CUSTOS_DRY_RUN=1` | no | Read and print, never send |

Without an endpoint and a token it does nothing at all and exits zero.

## Removing it

```
terraform destroy
```

There is nothing else. No agent on any host, no resource in any VPC, nothing
left behind.
