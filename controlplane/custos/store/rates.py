"""What an account pays, as they told us.

The spend figure is the field that gets a report forwarded to somebody with a
budget, and the first question that reader asks is whether the number is their
rate. Until an account supplies one the answer is no, and every surface says
so.

Superseded, never updated. A figure in last month's report was computed from
the rate in effect then, and overwriting the row would make that report
unreproducible — which matters most for the number people act on.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..spend import PRICES_REVISION, Price, Rates
from .db import iso, now, parse


class RateStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def supply(
        self,
        account_id: str,
        provider: str,
        input_per_mtok: float,
        output_per_mtok: float,
        operator: str,
        at: datetime | None = None,
    ) -> None:
        """Record what this account pays for one provider.

        A rate of zero is refused. It is far more likely to be an empty form
        field than a genuinely free provider, and a zero would silently make
        every agent on that provider look free — the one direction this number
        must never be wrong in, since its whole purpose is getting attention.
        """
        if not operator.strip():
            raise ValueError("supplying a rate needs a person's name")
        if input_per_mtok <= 0 or output_per_mtok <= 0:
            raise ValueError(
                "a rate must be greater than zero: a zero here makes every "
                "agent on this provider look free"
            )
        self.conn.execute(
            "INSERT INTO account_rates "
            "(account_id, provider, input_per_mtok, output_per_mtok, "
            "supplied_by, supplied_at) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, provider, input_per_mtok, output_per_mtok,
             operator.strip(), iso(at or now())),
        )

    def rates_for(self, account_id: str) -> Rates:
        """The newest rate per provider, ready to price with.

        The revision carries the date of the most recent supply, so a report
        can say when these were last confirmed rather than only that they were.
        A rate supplied two years ago is not the same claim as one from
        yesterday.
        """
        rows = self.conn.execute(
            "SELECT provider, input_per_mtok, output_per_mtok, supplied_at "
            "FROM account_rates WHERE account_id = ? ORDER BY supplied_at, id",
            (account_id,),
        ).fetchall()
        if not rows:
            return Rates()

        prices: dict[str, Price] = {}
        latest: datetime | None = None
        for row in rows:
            prices[row["provider"]] = Price(
                input_per_mtok=row["input_per_mtok"],
                output_per_mtok=row["output_per_mtok"],
            )
            stamp = parse(row["supplied_at"])
            latest = stamp if latest is None else max(latest, stamp)

        return Rates(
            prices=prices,
            revision=f"customer-supplied {latest:%Y-%m-%d}" if latest else PRICES_REVISION,
        )

    def history_for(self, account_id: str) -> list[dict]:
        """Every rate ever supplied, so an old report can be explained."""
        return [
            {
                "provider": r["provider"],
                "input_per_mtok": r["input_per_mtok"],
                "output_per_mtok": r["output_per_mtok"],
                "supplied_by": r["supplied_by"],
                "supplied_at": r["supplied_at"],
            }
            for r in self.conn.execute(
                "SELECT * FROM account_rates WHERE account_id = ? "
                "ORDER BY supplied_at DESC, id DESC",
                (account_id,),
            )
        ]


__all__ = ["RateStore"]
