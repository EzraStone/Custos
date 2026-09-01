# Security invariants

SEC-01 through SEC-15 belong to the Checkpoint and are specified in the
companion production document. The Register adds SEC-16 through SEC-18. This
repository adds SEC-19 through SEC-22, each of which arose from building
something rather than from planning it.

Every invariant here is testable. None is relaxable for a feature request. Each
one names the test that enforces it, and a change that removes the test is a
change to the invariant, not a refactor.

## SEC-16 — Discovery is read-only

The collector's credential grants no write permission to any customer resource,
and the collector contains no code path that mutates customer infrastructure.

**Enforced by:** `collector/internal/awsread` wraps every AWS client and exposes
only read operations. `TestNoMutatingAPIs` fails if any AWS SDK call whose verb
is not in the read allowlist appears in the collector's call graph.

## SEC-17 — Discovered records confer no authority

A discovered agent cannot execute through the Checkpoint until an operator
explicitly grants imprimatur. Discovery populates the register; it never
authorises.

Without this, traffic that imitates the agent signature would auto-provision
itself a credential — the register would become a self-service credential
issuer for anything that could produce a burst of model calls.

**Enforced by:** `AgentRecord.status` transitions to `sanctioned` only through
`grant_imprimatur()`, which requires an operator identity. `test_sec17_*`
asserts no classifier confidence, however high, produces a sanctioned record.

## SEC-18 — Metadata only

Identities, endpoints, byte counts, timings, and protocol fingerprints leave the
customer account. Prompts, completions, and payload bodies never do, under any
configuration.

**Enforced structurally.** The collector's wire types have no field capable of
holding a payload, and the shipper accepts only those types. This is not a
redaction step that could be misconfigured; there is no path from a payload byte
to the network. `TestWireTypesCarryNoPayload` walks the wire structs by
reflection and fails on any unrecognised field.

## SEC-19 — The collector is inert without an explicit destination

The collector performs no network egress until configured with a control plane
endpoint and a customer-held credential. A collector binary that is run with no
configuration reads nothing and sends nothing.

This exists so that "what happens if someone runs this by accident" has a boring
answer during security review.

**Enforced by:** `TestZeroConfigIsInert`.

## SEC-20 — Findings are attributable or they are not shipped

A finding with no resolved owner is noise, and noise gets the tool uninstalled.
Any register entry that cannot be attributed to at least a resource tag is
emitted as an unattributed candidate in a separate section of the report, never
mixed into the owned findings.

This is an invariant rather than a preference because the failure it prevents is
commercial, and commercial failures are the ones that get rationalised away.

**Enforced by:** `test_sec20_unattributed_findings_are_segregated`, `TestSEC20ResolvedAndDegradedAreNeverMerged`.

## SEC-21 — Logs follow the same rule as the wire

Nothing that describes a person reaches a log line. Principals, endpoints, byte
counts, and agent identifiers describe software and are logged. URLs, query
strings, user agents, client addresses, and anything credential-shaped are
dropped by key before a line is written.

Keys are filtered, never values. Inspecting values means deciding what a string
is, and the only reliable way to keep sensitive text out of a log is to have no
field that could hold it — the same argument as the absent fields on the wire
types.

This invariant exists because a third-party HTTP client was found logging full
request URLs at INFO, routing query strings into a customer's SIEM by a path the
application's own middleware never touched. Libraries that do this are quieted
rather than filtered.

**Enforced by:** `test_forbidden_keys_never_reach_a_log_line`,
`test_a_noisy_library_cannot_leak_a_query_string`.

## SEC-22 — A window is shortened, never silently truncated

A collection window that exceeds its record limit is cut back to what was
actually read, and the collection cursor resumes from there.

Truncation is silent data loss. A window shipped as if it covered its whole
span advances the cursor past records that were never read, and the next scan
reports fewer agents — which is indistinguishable from an account that has
fewer agents. That is the one wrong answer this product must never give by
accident.

**Enforced by:** `TestAShortenedWindowIsNotReportedAsDataLoss`,
`TestShortenToUsesTheLatestRecordNotTheLastOne`,
`TestTheCursorDoesNotAdvanceOnFailure`.
