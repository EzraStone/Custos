"""Persistent register.

The in-memory `Register` in `custos.register.store` is the reference
implementation of the state machine. This is the same machine backed by SQLite,
and the two are held to the same tests — the state machine is where SEC-17
lives, and having two implementations of it that could drift would be exactly
the wrong thing to be clever about.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..register.model import (
    Agent,
    Baseline,
    BlastRadius,
    Identity,
    Imprimatur,
    ModelUse,
    Provenance,
    Reach,
    Source,
    Status,
)
from ..register.store import _ALLOWED, TransitionError, agent_id
from .db import dumps, iso, loads, parse

_COLUMNS = """
    id, account_id, principal, status, imprimatur_by, imprimatur_at,
    approved_tools, approved_data, key_id, first_seen, last_seen, source,
    confidence, evidence, owner_team, owner_human, compute, providers,
    endpoints, est_monthly_spend, credentials, tools, data_stores, blast_radius
"""


def _row_to_agent(row: sqlite3.Row) -> Agent:
    imprimatur = None
    if row["imprimatur_by"]:
        imprimatur = Imprimatur(
            granted_by=row["imprimatur_by"],
            granted_at=parse(row["imprimatur_at"]),
            approved_tools=set(loads(row["approved_tools"])),
            approved_data=set(loads(row["approved_data"])),
            key_id=row["key_id"] or "",
        )
    return Agent(
        id=row["id"],
        first_seen=parse(row["first_seen"]),
        last_seen=parse(row["last_seen"]),
        status=Status(row["status"]),
        provenance=Provenance(
            source=Source(row["source"]),
            confidence=row["confidence"],
            observed_principal=row["principal"],
            evidence=loads(row["evidence"]),
        ),
        identity=Identity(
            principal=row["principal"],
            owner_team=row["owner_team"],
            owner_human=row["owner_human"],
            compute=row["compute"],
            account_id=row["account_id"],
        ),
        model=ModelUse(
            providers=set(loads(row["providers"])),
            endpoints=set(loads(row["endpoints"])),
            est_monthly_spend_usd=row["est_monthly_spend"],
        ),
        reach=Reach(
            credentials=set(loads(row["credentials"])),
            tools=set(loads(row["tools"])),
            data_stores=set(loads(row["data_stores"])),
            blast_radius=BlastRadius(row["blast_radius"]),
        ),
        imprimatur=imprimatur,
        baseline=Baseline(),
    )


class AgentStore:
    """Register operations against SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, agent_id_: str) -> Agent | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM agents WHERE id = ?", (agent_id_,)
        ).fetchone()
        return _row_to_agent(row) if row else None

    def upsert(self, agent: Agent) -> Agent:
        """Insert, or refresh observation on an existing record.

        The UPDATE deliberately omits status, imprimatur_by, imprimatur_at,
        approved_tools, approved_data, and key_id. A re-scan is an observation,
        not an authorisation event, and SEC-17 requires that no amount of
        re-observation promotes a record. Adding any of those columns to this
        statement would break the invariant.
        """
        existing = self.get(agent.id)
        if existing is None:
            self.conn.execute(
                f"INSERT INTO agents ({_COLUMNS}) VALUES ("
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent.id, agent.identity.account_id, agent.identity.principal,
                    str(agent.status), None, None, None, None, None,
                    iso(agent.first_seen), iso(agent.last_seen),
                    str(agent.provenance.source), agent.provenance.confidence,
                    dumps(agent.provenance.evidence),
                    agent.identity.owner_team, agent.identity.owner_human,
                    agent.identity.compute,
                    dumps(agent.model.providers), dumps(agent.model.endpoints),
                    agent.model.est_monthly_spend_usd,
                    dumps(agent.reach.credentials), dumps(agent.reach.tools),
                    dumps(agent.reach.data_stores), str(agent.reach.blast_radius),
                ),
            )
            self.record(agent.identity.account_id, agent.id, "system", "discovered",
                        agent.identity.principal, agent.first_seen)
            return agent

        # Attribution is only overwritten when the new scan resolved an owner
        # and the stored record has none. A scan run without IAM permissions
        # must not erase an owner an earlier scan established.
        owner_team = agent.identity.owner_team or existing.identity.owner_team
        owner_human = agent.identity.owner_human or existing.identity.owner_human

        self.conn.execute(
            """
            UPDATE agents SET
                first_seen = MIN(first_seen, ?),
                last_seen = MAX(last_seen, ?),
                confidence = ?, evidence = ?,
                owner_team = ?, owner_human = ?, compute = ?,
                providers = ?, endpoints = ?, est_monthly_spend = ?,
                credentials = ?, tools = ?, data_stores = ?, blast_radius = ?
            WHERE id = ?
            """,
            (
                iso(agent.first_seen), iso(agent.last_seen),
                agent.provenance.confidence, dumps(agent.provenance.evidence),
                owner_team, owner_human,
                agent.identity.compute or existing.identity.compute,
                dumps(agent.model.providers), dumps(agent.model.endpoints),
                agent.model.est_monthly_spend_usd,
                dumps(agent.reach.credentials), dumps(agent.reach.tools),
                dumps(agent.reach.data_stores), str(agent.reach.blast_radius),
                agent.id,
            ),
        )
        result = self.get(agent.id)
        assert result is not None
        return result

    def transition(
        self, agent_id_: str, to: Status, actor: str, at: datetime, detail: str = ""
    ) -> Agent:
        if to is Status.SANCTIONED:
            raise TransitionError(
                "SANCTIONED is reachable only through grant_imprimatur(), which "
                "requires an operator identity and an explicit approval scope."
            )
        agent = self.get(agent_id_)
        if agent is None:
            raise KeyError(agent_id_)
        if to not in _ALLOWED[agent.status]:
            raise TransitionError(f"{agent.status} -> {to} is not a permitted transition")

        if to is Status.RETIRED:
            self.conn.execute(
                "UPDATE agents SET status = ?, imprimatur_by = NULL, "
                "imprimatur_at = NULL, approved_tools = NULL, approved_data = NULL, "
                "key_id = NULL WHERE id = ?",
                (str(to), agent_id_),
            )
        else:
            self.conn.execute(
                "UPDATE agents SET status = ? WHERE id = ?", (str(to), agent_id_)
            )
        self.record(agent.identity.account_id, agent_id_, actor, str(to), detail, at)
        result = self.get(agent_id_)
        assert result is not None
        return result

    def grant_imprimatur(
        self,
        agent_id_: str,
        operator: str,
        at: datetime,
        approved_tools: set[str] | None = None,
        approved_data: set[str] | None = None,
    ) -> Agent:
        """The only path to SANCTIONED in the persistent store."""
        if not operator.strip():
            raise TransitionError("granting imprimatur requires an operator identity")
        agent = self.get(agent_id_)
        if agent is None:
            raise KeyError(agent_id_)
        if agent.status is Status.RETIRED:
            raise TransitionError("a retired agent must be reinstated before sanctioning")

        tools = approved_tools if approved_tools is not None else agent.reach.tools
        data = approved_data if approved_data is not None else agent.reach.data_stores

        self.conn.execute(
            "UPDATE agents SET status = ?, imprimatur_by = ?, imprimatur_at = ?, "
            "approved_tools = ?, approved_data = ? WHERE id = ?",
            (str(Status.SANCTIONED), operator, iso(at), dumps(tools), dumps(data), agent_id_),
        )
        self.record(
            agent.identity.account_id, agent_id_, operator, "sanctioned",
            f"tools={sorted(tools)}", at,
        )
        result = self.get(agent_id_)
        assert result is not None
        return result

    def record(
        self, account_id: str, agent_id_: str, actor: str, action: str,
        detail: str, at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit (at, account_id, agent_id, actor, action, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (iso(at), account_id, agent_id_, actor, action, detail),
        )

    def audit_for(self, agent_id_: str) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT at, actor, action, detail FROM audit WHERE agent_id = ? "
                "ORDER BY at, id",
                (agent_id_,),
            )
        ]

    def list_for_account(self, account_id: str, status: Status | None = None) -> list[Agent]:
        if status is None:
            rows = self.conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE account_id = ?", (account_id,)
            )
        else:
            rows = self.conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE account_id = ? AND status = ?",
                (account_id, str(status)),
            )
        return sorted((_row_to_agent(r) for r in rows), key=lambda a: a.priority)

    def unsanctioned(self, account_id: str) -> list[Agent]:
        """The set that regenerates on every scan and renews the contract."""
        return [a for a in self.list_for_account(account_id) if a.unsanctioned]


__all__ = ["AgentStore", "agent_id"]
