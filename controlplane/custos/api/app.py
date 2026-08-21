"""The control plane HTTP API.

Small on purpose. Three things a collector or an operator needs — ship a batch,
read the register, sanction an agent — and nothing else. Every endpoint that
mutates state names the human who did it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import __version__
from ..catalog import RANGES_REVISION
from ..pipeline import ingest
from ..register.model import Status
from ..register.store import TransitionError
from ..spend import PRICES_REVISION
from ..store.agents import AgentStore
from ..store.db import now, open_database
from ..store.scans import ScanStore
from .auth import Principal, TokenStore, parse_bearer
from .schema import Batch, BatchAccepted

MAX_FLOWS_PER_BATCH = 2_000_000
"""Matches the collector's own event cap. A batch larger than this did not come
from our collector, and accepting it would let one request exhaust memory."""


def authenticate(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the caller from a bearer token, or refuse.

    Defined at module level rather than inside create_app because
    `from __future__ import annotations` turns every annotation into a string,
    and FastAPI resolves those against the module namespace. A dependency alias
    scoped to a closure is invisible there, and the failure mode is quiet:
    FastAPI treats the unresolvable parameter as a query argument and every
    endpoint starts returning 422 instead of authenticating.

    Token lookup goes through request.app.state so the dependency needs no
    closure over configuration.
    """
    principal = request.app.state.tokens.resolve(parse_bearer(authorization))
    if principal is None:
        # Deliberately identical for a missing and a wrong token. Telling them
        # apart helps an attacker enumerate and helps a legitimate operator not
        # at all.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


Auth = Annotated[Principal, Depends(authenticate)]


def create_app(
    conn: sqlite3.Connection | None = None,
    tokens: TokenStore | None = None,
) -> FastAPI:
    database = conn if conn is not None else open_database()
    token_store = tokens if tokens is not None else TokenStore.from_env()

    app = FastAPI(
        title="Custos control plane",
        version=__version__,
        # No interactive docs by default. This API has exactly three consumers,
        # all of which we write, and a public schema browser on a security
        # product is an invitation nobody asked for.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db = database
    app.state.tokens = token_store

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        """Liveness, plus the two revisions that decide what a finding means."""
        return {
            "status": "ok",
            "version": __version__,
            "catalogue_revision": RANGES_REVISION,
            "prices_revision": PRICES_REVISION,
        }

    @app.post("/v1/batches", status_code=status.HTTP_202_ACCEPTED)
    def post_batch(batch: Batch, principal: Auth) -> BatchAccepted:
        if batch.account_id != principal.account_id:
            # A token names one account. Shipping telemetry for another is
            # either a misconfiguration or an attempt to poison someone else's
            # register, and both deserve the same refusal.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this credential cannot ship telemetry for that account",
            )
        if len(batch.flows) > MAX_FLOWS_PER_BATCH:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"batch exceeds {MAX_FLOWS_PER_BATCH} flow records",
            )
        if batch.window_end <= batch.window_start:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="window_end must be after window_start",
            )

        outcome = ingest(app.state.db, batch)
        return BatchAccepted(
            batch_id=outcome.batch.id,
            scan_id=outcome.scan_id,
            duplicate=outcome.batch.duplicate,
            agents_found=len(outcome.result.register.agents),
            review_candidates=len(outcome.result.review_candidates),
            coverage_note=outcome.coverage_note,
        )

    @app.get("/v1/register")
    def get_register(principal: Auth, unsanctioned_only: bool = False) -> dict:
        agents = AgentStore(app.state.db)
        records = (
            agents.unsanctioned(principal.account_id)
            if unsanctioned_only
            else agents.list_for_account(principal.account_id)
        )
        return {
            "account_id": principal.account_id,
            "catalogue_revision": RANGES_REVISION,
            "agents": [_render(a) for a in records],
        }

    @app.get("/v1/scans")
    def get_scans(principal: Auth, limit: int = 20) -> dict:
        scans = ScanStore(app.state.db)
        return {
            "account_id": principal.account_id,
            "scans": [
                {
                    "id": s.id,
                    "started_at": s.started_at.isoformat(),
                    "principals_seen": s.principals_seen,
                    "agents_found": s.agents_found,
                    "review_candidates": s.review_candidates,
                    "coverage": s.coverage,
                    "truncated": s.truncated,
                }
                for s in scans.scans_for(principal.account_id, limit=min(limit, 100))
            ],
        }

    @app.post("/v1/agents/{agent_id}/imprimatur")
    def grant(agent_id: str, body: GrantRequest, principal: Auth) -> dict:
        """Sanction an agent. The only path to SANCTIONED over HTTP.

        `operator` is required and is a human identity. It is taken from the
        body rather than from the token deliberately: the token authenticates a
        machine, and SEC-17 requires that a person granted the authority.
        """
        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or existing.identity.account_id != principal.account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such agent")

        try:
            agent = agents.grant_imprimatur(
                agent_id, operator=body.operator, at=now(),
                approved_tools=set(body.approved_tools) if body.approved_tools else None,
                approved_data=set(body.approved_data) if body.approved_data else None,
            )
        except TransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        return _render(agent)

    @app.post("/v1/agents/{agent_id}/status")
    def set_status(agent_id: str, body: StatusRequest, principal: Auth) -> dict:
        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or existing.identity.account_id != principal.account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such agent")

        try:
            agent = agents.transition(
                agent_id, Status(body.status), actor=body.operator,
                at=now(), detail=body.reason,
            )
        except TransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        return _render(agent)

    @app.get("/v1/agents/{agent_id}/audit")
    def get_audit(agent_id: str, principal: Auth) -> dict:
        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or existing.identity.account_id != principal.account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such agent")
        return {"agent_id": agent_id, "entries": agents.audit_for(agent_id)}

    return app


class GrantRequest(BaseModel):
    operator: str = Field(min_length=1, description="Human identity granting the authority")
    approved_tools: list[str] | None = None
    approved_data: list[str] | None = None


class StatusRequest(BaseModel):
    status: str
    operator: str = Field(min_length=1)
    reason: str = ""


def _render(agent) -> dict:
    """Render an agent for the API.

    Evidence is included. A finding without the sentences behind it is a score,
    and a score is what the workload's owner will argue with instead of the
    facts.
    """
    return {
        "id": agent.id,
        "principal": agent.identity.principal,
        "status": str(agent.status),
        "confidence": agent.provenance.confidence,
        "evidence": agent.provenance.evidence,
        "owner_team": agent.identity.owner_team,
        "owner_human": agent.identity.owner_human,
        "compute": agent.identity.compute,
        "attributed": agent.identity.attributed,
        "first_seen": _iso(agent.first_seen),
        "last_seen": _iso(agent.last_seen),
        "blast_radius": str(agent.reach.blast_radius),
        "tools": sorted(agent.reach.tools),
        "data_stores": sorted(agent.reach.data_stores),
        "est_monthly_spend_usd": agent.model.est_monthly_spend_usd,
        "unsanctioned": agent.unsanctioned,
        "imprimatur": None if agent.imprimatur is None else {
            "granted_by": agent.imprimatur.granted_by,
            "granted_at": _iso(agent.imprimatur.granted_at),
            "approved_tools": sorted(agent.imprimatur.approved_tools),
            "approved_data": sorted(agent.imprimatur.approved_data),
        },
    }


def _iso(value: datetime) -> str:
    return value.isoformat()
