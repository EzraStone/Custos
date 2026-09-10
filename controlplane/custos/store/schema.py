"""Persistent storage schema.

SQLite, deliberately. The register for a single customer is thousands of rows,
not millions, and the operational cost of Postgres before a paying customer is
a cost with no matching benefit. The schema is written so the move to Postgres
is a connection-string change plus a migration runner — no ORM to unpick, no
SQLite-specific types, no reliance on its permissive typing.

Two design points that are not obvious:

`agents.status` is never written by an ingestion path. Every write of that
column goes through the register's state machine, and the schema records that
by keeping status separate from the observation columns that a re-scan updates
(SEC-17). A re-scan touching status would be a bug the schema cannot prevent,
but the separation makes it visible in a diff.

`batches` is keyed on (account_id, window_start, window_end). The collector
retries on failure, so the same window arrives more than once, and without a
natural key a retried batch would double every byte count and inflate every
agent's apparent spend and reach.
"""

from __future__ import annotations

SCHEMA_VERSION = 14

# Columns added after a table was first written, applied by ALTER on databases
# that already exist. The schema below is applied with CREATE TABLE IF NOT
# EXISTS, which does nothing to a table that is already there — so without this
# list, every column added after a customer's first scan is invisible to their
# database and the first write naming it fails during an upgrade.
#
# Additive only. A rename, a type change, or a new constraint is not a line
# here; it is a migration someone writes by hand and thinks about.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("scans", "scope_named", "INTEGER NOT NULL DEFAULT 0"),
    ("scans", "scope_total", "INTEGER NOT NULL DEFAULT 0"),
    # What the account's flow log format did not carry, and what direction
    # inference could not decide. Both change what a report may claim, so both
    # have to survive to the render rather than living in a collector log the
    # customer never sees.
    ("scans", "missing_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("scans", "direction_undecided", "INTEGER NOT NULL DEFAULT 0"),
    ("scans", "read_errors", "INTEGER NOT NULL DEFAULT 0"),
    # Which regions this scan covered. Plural from the start: an AWS account is
    # a region-by-region thing and a scan of one of them is a scan of part of
    # the account, which the report has to be able to say.
    ("scans", "regions", "TEXT NOT NULL DEFAULT '[]'"),
    # Regions this agent has been seen in. Accumulated across scans, because a
    # scan covers one region and a role running in three is discovered three
    # times.
    ("agents", "regions", "TEXT NOT NULL DEFAULT '[]'"),
    # Spend per region, from the last scan of each. A scan covers one region,
    # so an agent running in three was showing a third of its cost.
    ("agents", "region_spend", "TEXT NOT NULL DEFAULT '{}'"),
    # Which region a declaration applies to. Empty means every region, which
    # is right for a public provider range and dangerous for a private one:
    # 10.0.7.40 is a different host in every region an account runs in.
    ("declared_endpoints", "region", "TEXT NOT NULL DEFAULT ''"),
    # Which region a gateway question was asked about. A declaration answers it
    # only in the region it applies to, and the same address elsewhere is a
    # different host and still an open question.
    ("gateway_candidates", "region", "TEXT NOT NULL DEFAULT ''"),
)

BATCHES_TABLE = """CREATE TABLE IF NOT EXISTS batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    region        TEXT    NOT NULL DEFAULT '',
    window_start  TEXT    NOT NULL,
    window_end    TEXT    NOT NULL,
    collector     TEXT    NOT NULL DEFAULT '',
    received_at   TEXT    NOT NULL,
    flow_records  INTEGER NOT NULL DEFAULT 0,
    requests      INTEGER NOT NULL DEFAULT 0,
    have_alb_logs INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, region, window_start, window_end)
);"""
"""One row per shipped collection window per region.

Kept as its own constant because the one hand-written migration in this store
recreates this table, and a migration that rebuilds a table from a copy of its
definition is a migration that drifts from it.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

-- One row per shipped collection window per region.
--
-- The natural key makes ingestion idempotent: a collector retry updates the row
-- rather than adding traffic. Region is part of it because an AWS account is a
-- region-by-region thing and one collector covers one region, so three regions
-- of one account ship three windows for the same hour and a key without the
-- region turned two of them into retries of the first.
--
-- Region rather than merging into one window, because a private address is
-- unique within a region and nowhere else. A scan holding both regions' traffic
-- would key destination names and gateway questions on addresses that mean two
-- different things.
""" + BATCHES_TABLE + """

-- One row per scan run over a batch. Kept separate from batches because the
-- same telemetry can be re-classified by a newer classifier, and comparing
-- those runs is how a classifier change is evaluated against real traffic.
CREATE TABLE IF NOT EXISTS scans (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            INTEGER NOT NULL REFERENCES batches(id),
    account_id          TEXT    NOT NULL,
    started_at          TEXT    NOT NULL,
    principals_seen     INTEGER NOT NULL DEFAULT 0,
    agents_found        INTEGER NOT NULL DEFAULT 0,
    review_candidates   INTEGER NOT NULL DEFAULT 0,
    coverage            REAL    NOT NULL DEFAULT 0.0,
    truncated           INTEGER NOT NULL DEFAULT 0,
    catalogue_revision  TEXT    NOT NULL DEFAULT '',

    -- How much of this scan's approval scope could be named rather than shown
    -- as an address. Recorded per scan because it is a property of the
    -- account's tagging on that day, and because a scope that got less
    -- readable is worth noticing.
    scope_named         INTEGER NOT NULL DEFAULT 0,
    scope_total         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS scans_by_account ON scans (account_id, started_at DESC);

-- What an account actually pays per provider.
--
-- Superseded, not updated. A dollar figure in last month's report was computed
-- from the rate in effect then, and overwriting the row would make that report
-- unreproducible — which matters because the whole purpose of the figure is
-- that somebody with a budget acts on it.
CREATE TABLE IF NOT EXISTS account_rates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT    NOT NULL,
    provider        TEXT    NOT NULL,
    input_per_mtok  REAL    NOT NULL,
    output_per_mtok REAL    NOT NULL,
    supplied_by     TEXT    NOT NULL,
    supplied_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS account_rates_by_account
    ON account_rates (account_id, provider, supplied_at DESC);

-- Workloads the classifier was unsure about, per scan.
--
-- Emphatically not the register. No id that looks like an agent id, no status,
-- no imprimatur column, no foreign key anything else can follow. A review
-- candidate is a scan artefact — the classifier saying "this might be an agent
-- and I am not confident enough to say so" — and the moment it acquires a
-- state machine somebody will move one into the register by hand, which is the
-- thing SEC-17 exists to prevent.
--
-- Kept because only the count was, and an operator who wants to look at last
-- week's maybes could not. Pruned with the scan, like observations: a
-- workload the classifier was unsure about three months ago is not a question
-- anyone should still be answering from a stale window.
CREATE TABLE IF NOT EXISTS review_candidates (
    scan_id     INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    account_id  TEXT    NOT NULL,
    principal   TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    evidence    TEXT    NOT NULL,
    unavailable TEXT    NOT NULL DEFAULT '[]',
    PRIMARY KEY (scan_id, principal)
);
CREATE INDEX IF NOT EXISTS review_candidates_by_account
    ON review_candidates (account_id, scan_id DESC);

-- Gateway candidates from one scan: internal destinations that behave like
-- model endpoints and are not declared as any.
--
-- Stored per scan rather than recomputed, because the telemetry they were
-- derived from is not kept — observations are, but not the per-window
-- destination bytes this reads. Pruned with the scan they belong to, which is
-- correct: a question about traffic from three months ago is not a question
-- anybody should still be answering.
CREATE TABLE IF NOT EXISTS gateway_candidates (
    scan_id       INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    account_id    TEXT    NOT NULL,
    address       TEXT    NOT NULL,
    egress        INTEGER NOT NULL,
    ingress       INTEGER NOT NULL,
    principals    TEXT    NOT NULL,
    blind         TEXT    NOT NULL,
    region        TEXT    NOT NULL DEFAULT '',
    question      TEXT    NOT NULL,
    PRIMARY KEY (scan_id, address)
);
CREATE INDEX IF NOT EXISTS gateway_candidates_by_account
    ON gateway_candidates (account_id, scan_id DESC);

-- Model endpoints a customer declared, per account.
--
-- Per account and not global: 10.0.0.0/8 is where every customer's internal
-- services live, and a declaration that leaked between accounts would
-- manufacture agents out of unrelated traffic on a coincidental collision.
--
-- Declarations are never deleted, only withdrawn. A finding produced while a
-- declaration was in effect is explained by it, and a row that vanished would
-- leave that finding unexplainable — which matters most in exactly the case
-- someone withdraws one to make a finding go away.
CREATE TABLE IF NOT EXISTS declared_endpoints (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    value         TEXT    NOT NULL,
    kind          TEXT    NOT NULL,
    note          TEXT    NOT NULL DEFAULT '',
    region        TEXT    NOT NULL DEFAULT '',
    declared_by   TEXT    NOT NULL,
    declared_at   TEXT    NOT NULL,
    withdrawn_by  TEXT,
    withdrawn_at  TEXT,
    UNIQUE (account_id, value, kind, declared_at)
);
CREATE INDEX IF NOT EXISTS declared_by_account
    ON declared_endpoints (account_id, withdrawn_at);

-- The register. status and imprimatur columns are written only by the state
-- machine; every other column is refreshed by ingestion (SEC-17).
CREATE TABLE IF NOT EXISTS agents (
    id                  TEXT    PRIMARY KEY,
    account_id          TEXT    NOT NULL,
    principal           TEXT    NOT NULL,

    status              TEXT    NOT NULL,
    imprimatur_by       TEXT,
    imprimatur_at       TEXT,
    approved_tools      TEXT,
    approved_data       TEXT,
    key_id              TEXT,

    first_seen          TEXT    NOT NULL,
    last_seen           TEXT    NOT NULL,
    source              TEXT    NOT NULL,
    confidence          REAL    NOT NULL DEFAULT 0.0,
    evidence            TEXT    NOT NULL DEFAULT '[]',

    owner_team          TEXT    NOT NULL DEFAULT '',
    owner_human         TEXT    NOT NULL DEFAULT '',
    compute             TEXT    NOT NULL DEFAULT '',

    providers           TEXT    NOT NULL DEFAULT '[]',
    endpoints           TEXT    NOT NULL DEFAULT '[]',
    est_monthly_spend   REAL    NOT NULL DEFAULT 0.0,
    regions             TEXT    NOT NULL DEFAULT '[]',
    region_spend        TEXT    NOT NULL DEFAULT '{}',

    credentials         TEXT    NOT NULL DEFAULT '[]',
    tools               TEXT    NOT NULL DEFAULT '[]',
    data_stores         TEXT    NOT NULL DEFAULT '[]',
    blast_radius        TEXT    NOT NULL DEFAULT 'read',

    UNIQUE (account_id, principal)
);
CREATE INDEX IF NOT EXISTS agents_by_account ON agents (account_id, status);

-- Per-scan observation of each agent. This is what baselines and drift are
-- computed from, and what lets a report say "this changed since last week"
-- rather than only "this is true now".
CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           INTEGER NOT NULL REFERENCES scans(id),
    agent_id          TEXT    NOT NULL REFERENCES agents(id),
    observed_at       TEXT    NOT NULL,
    confidence        REAL    NOT NULL DEFAULT 0.0,
    model_egress      INTEGER NOT NULL DEFAULT 0,
    model_ingress     INTEGER NOT NULL DEFAULT 0,
    episodes          INTEGER NOT NULL DEFAULT 0,
    calls_per_hour    REAL    NOT NULL DEFAULT 0.0,
    tools             TEXT    NOT NULL DEFAULT '[]',
    active_hours      TEXT    NOT NULL DEFAULT '{}',
    blast_radius      TEXT    NOT NULL DEFAULT 'read'
);
CREATE INDEX IF NOT EXISTS observations_by_agent ON observations (agent_id, observed_at DESC);

-- What has already been said, per channel. Suppression reads this to avoid
-- reporting the same finding every week, which is how a channel gets muted.
--
-- Lives here rather than being created by the Suppressor, because a table that
-- appears the first time some object is constructed is a table that migrations
-- and retention both forget about.
CREATE TABLE IF NOT EXISTS deliveries (
    fingerprint   TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    severity      TEXT NOT NULL,
    channel       TEXT NOT NULL,
    delivered_at  TEXT NOT NULL,
    PRIMARY KEY (fingerprint, channel)
);
CREATE INDEX IF NOT EXISTS deliveries_by_account
    ON deliveries (account_id, delivered_at DESC);

-- Every status change, with who made it. The audit trail is what a customer
-- reads when they ask why an agent is sanctioned.
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_by_agent ON audit (agent_id, at DESC);
"""
