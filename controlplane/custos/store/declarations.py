"""Storing what a customer said their model endpoints are.

Two rules shape this table, and both are about not being able to hide a
finding with configuration.

**Declarations are withdrawn, never deleted.** A finding produced while a
declaration was in effect is explained by it, and a row that vanished would
leave that finding unexplainable. That matters most in precisely the case
where somebody withdraws one to make a finding go away.

**Every write names a person.** Declaring an endpoint changes what the
classifier considers an agent, which makes it the second decision in this
system with that property. The first — granting imprimatur — already requires
a human identity, and this one is no different.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..declared import Declaration, Declared, build
from .db import dumps, iso, loads, now, parse


@dataclass(frozen=True, slots=True)
class DeclarationRecord:
    id: int
    account_id: str
    value: str
    kind: str
    note: str
    declared_by: str
    declared_at: datetime
    region: str = ""
    """Region this applies to. Empty means every region.

    Empty is right for a published provider range, which means the same thing
    everywhere. It is dangerous for a private one: 10.0.7.40 is the model
    gateway in us-east-1 and, in eu-west-1, whatever that account happens to
    run at that address — and declaring it there turns ordinary internal
    traffic into model traffic, which manufactures agents out of nothing.
    """

    withdrawn_by: str = ""
    withdrawn_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.withdrawn_at is None


class DeclarationStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def declare(
        self,
        account_id: str,
        value: str,
        kind: str,
        operator: str,
        note: str = "",
        region: str = "",
        at: datetime | None = None,
    ) -> DeclarationRecord:
        """Record a declaration, after checking it parses.

        Validated here rather than at read time. A declaration stored and
        rejected later would be one a customer believes is in effect while
        their agents stay invisible, which is the failure the whole mechanism
        exists to prevent — and finding out at the next scan is too late.
        """
        if not operator.strip():
            raise ValueError(
                "declaring an endpoint changes what counts as an agent; it needs "
                "a person's name, like granting imprimatur does"
            )
        declared = build([Declaration(value=value, kind=kind, note=note)])

        # A private range with no region is the one shape this must refuse.
        # 10.0.7.40 is the model gateway in us-east-1 and, in every other
        # region the account runs in, whatever happens to live at that address
        # — so declaring it everywhere turns ordinary internal traffic into
        # model traffic. That does not hide agents, it invents them, which is
        # the direction this system is least able to recover from.
        #
        # A public range means the same thing everywhere and needs no region.
        if not region.strip() and declared.private:
            raise ValueError(
                f"{value} is a private range and private addresses mean "
                "different things in different regions. Declare it for the "
                "region the question was asked about, or an unrelated service "
                "at that address elsewhere becomes a model endpoint."
            )

        stamp = at or now()
        cursor = self.conn.execute(
            "INSERT INTO declared_endpoints "
            "(account_id, value, kind, note, region, declared_by, declared_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, value, kind, note, region.strip(), operator.strip(), iso(stamp)),
        )
        return DeclarationRecord(
            id=cursor.lastrowid, account_id=account_id, value=value, kind=kind,
            note=note, region=region.strip(), declared_by=operator.strip(),
            declared_at=stamp,
        )

    def withdraw(
        self, declaration_id: int, account_id: str, operator: str, at: datetime | None = None
    ) -> bool:
        """Mark a declaration as no longer in effect. Returns whether it changed.

        The row stays. Withdrawing narrows what the classifier calls a model
        endpoint, so unlike declaring it *can* make a finding disappear — which
        is exactly why the record of it has to survive.
        """
        if not operator.strip():
            raise ValueError("withdrawing a declaration needs a person's name")
        cursor = self.conn.execute(
            "UPDATE declared_endpoints SET withdrawn_by = ?, withdrawn_at = ? "
            "WHERE id = ? AND account_id = ? AND withdrawn_at IS NULL",
            (operator.strip(), iso(at or now()), declaration_id, account_id),
        )
        return cursor.rowcount > 0

    def records_for(
        self, account_id: str, include_withdrawn: bool = False
    ) -> list[DeclarationRecord]:
        clause = "" if include_withdrawn else " AND withdrawn_at IS NULL"
        return [
            DeclarationRecord(
                id=row["id"], account_id=row["account_id"], value=row["value"],
                kind=row["kind"], note=row["note"], region=row["region"],
                declared_by=row["declared_by"],
                declared_at=parse(row["declared_at"]),
                withdrawn_by=row["withdrawn_by"] or "",
                withdrawn_at=parse(row["withdrawn_at"]) if row["withdrawn_at"] else None,
            )
            for row in self.conn.execute(
                "SELECT * FROM declared_endpoints WHERE account_id = ?" + clause
                + " ORDER BY declared_at, id",
                (account_id,),
            )
        ]

    def declared_for(self, account_id: str, region: str = "") -> Declared:
        """What is in effect for this account and region, ready to classify.

        A declaration with no region applies everywhere; one with a region
        applies only there. Asking without a region returns only the
        everywhere ones, which is the safe reading: a caller that does not know
        which region it is classifying must not be handed a private address
        that means something different in each.
        """
        return build([
            Declaration(value=r.value, kind=r.kind, note=r.note)
            for r in self.records_for(account_id)
            if r.region == "" or r.region == region
        ])


class CandidateStore:
    """Gateway candidates from a scan, kept so they can be asked about later.

    Separate from DeclarationStore because they are opposite things: a
    declaration is an answer a customer gave, a candidate is a question we are
    putting to them. Keeping them in one class would invite a method that
    turned one into the other automatically, and the point of a candidate is
    that a person decides.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(
        self, scan_id: int, account_id: str, found: list, region: str = ""
    ) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO gateway_candidates "
            "(scan_id, account_id, address, egress, ingress, principals, blind, "
            "question, region) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (scan_id, account_id, c.address, c.egress, c.ingress,
                 dumps(list(c.principals)), dumps(list(c.blind_principals)),
                 c.question, region)
                for c in found
            ],
        )

    def latest_for(self, account_id: str) -> list[dict]:
        """Candidates from this account's most recent scan that had any.

        Not from the most recent scan outright. A gateway that was quiet during
        one window is still a gateway, and an empty list because nothing
        happened to use it for an hour reads as "we looked and there is
        nothing" — which is a different and much more reassuring claim.
        """
        row = self.conn.execute(
            "SELECT MAX(scan_id) AS scan_id FROM gateway_candidates WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None or row["scan_id"] is None:
            return []

        return [
            {
                "address": r["address"],
                "egress": r["egress"],
                "ingress": r["ingress"],
                "principals": loads(r["principals"]),
                "blind_principals": loads(r["blind"]),
                "question": r["question"],
                "region": r["region"],
                "scan_id": r["scan_id"],
            }
            for r in self.conn.execute(
                "SELECT * FROM gateway_candidates WHERE scan_id = ? ORDER BY egress DESC",
                (row["scan_id"],),
            )
        ]


__all__ = ["CandidateStore", "DeclarationRecord", "DeclarationStore"]
