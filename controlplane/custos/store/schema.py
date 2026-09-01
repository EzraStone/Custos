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

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

-- One row per shipped collection window. The natural key makes ingestion
-- idempotent: a collector retry updates the row rather than adding traffic.
CREATE TABLE IF NOT EXISTS batches (
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
    UNIQUE (account_id, window_start, window_end)
);

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
    catalogue_revision  TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS scans_by_account ON scans (account_id, started_at DESC);

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
