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
from .db import iso, now, parse


@dataclass(frozen=True, slots=True)
class DeclarationRecord:
    id: int
    account_id: str
    value: str
    kind: str
    note: str
    declared_by: str
    declared_at: datetime
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
        build([Declaration(value=value, kind=kind, note=note)])

        stamp = at or now()
        cursor = self.conn.execute(
            "INSERT INTO declared_endpoints "
            "(account_id, value, kind, note, declared_by, declared_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, value, kind, note, operator.strip(), iso(stamp)),
        )
        return DeclarationRecord(
            id=cursor.lastrowid, account_id=account_id, value=value, kind=kind,
            note=note, declared_by=operator.strip(), declared_at=stamp,
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
                kind=row["kind"], note=row["note"], declared_by=row["declared_by"],
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

    def declared_for(self, account_id: str) -> Declared:
        """What is in effect for this account right now, ready to classify."""
        return build([
            Declaration(value=r.value, kind=r.kind, note=r.note)
            for r in self.records_for(account_id)
        ])


__all__ = ["DeclarationRecord", "DeclarationStore"]
