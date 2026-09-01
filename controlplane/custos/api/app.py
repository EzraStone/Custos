"""The control plane HTTP API.

Small on purpose. Three things a collector or an operator needs — ship a batch,
read the register, sanction an agent — and nothing else. Every endpoint that
mutates state names the human who did it.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..batch import Batch, BatchAccepted
from ..catalog import RANGES_REVISION
from ..diff import ScanDiff, compare
from ..logging import event, get
from ..pipeline import ingest
from ..register.model import Status
from ..register.store import Register, TransitionError
from ..report import Coverage, render
from ..scan import ScanResult
from ..spend import PRICES_REVISION
from ..store.agents import AgentStore
from ..store.db import now, open_database
from ..store.scans import ScanStore
from .auth import Principal, TokenStore, parse_bearer

log = get("custos.api")

MAX_FLOWS_PER_BATCH = 500_000
"""Matches the collector's record cap, and both are set by measurement.

Ingestion costs roughly a kilobyte of peak memory per flow record end to end —
the JSON being parsed, the validated batch, and the conversion to telemetry all
live at once. Half a million is therefore about half a gigabyte of peak, which
a small container survives.

The previous value of two million was about two gigabytes on a single request
and would have taken the control plane down the first time a customer had a
busy hour. A batch larger than this cap did not come from our collector, which
shortens its window rather than exceeding it."""


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


def scope(principal: Principal, requested: str | None) -> str:
    """Resolve which account a read applies to, or refuse.

    A token covering one account needs no parameter. A token covering several
    must say which, because defaulting to the first would attribute one
    account's findings to another — quietly, and in the direction that makes a
    report wrong rather than empty.

    A requested account the token does not cover is 404 rather than 403. A
    distinct response would confirm the account exists to someone holding a
    credential for a different one.
    """
    if requested:
        if not principal.covers(requested):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no such account"
            )
        return requested

    if len(principal.accounts) == 1:
        return principal.account_id

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "this credential covers several accounts; pass ?account=<id> to say "
            f"which of {sorted(principal.accounts)}"
        ),
    )


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

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log every request as a structured event.

        The path is logged and the query string is not. A path is a fixed set
        of route templates we wrote; a query string is caller-supplied and
        could hold anything. That is the same reasoning as the absent fields on
        the wire types, applied to the one place where request data reaches a
        log line.

        Client addresses are not logged either. They describe whoever operates
        the collector, and this system inventories software.
        """
        started = time.monotonic()
        response = await call_next(request)
        event(
            log, "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return response

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
        if not principal.covers(batch.account_id):
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
        event(
            log, "batch.ingested",
            account_id=batch.account_id,
            scan_id=outcome.scan_id,
            duplicate=outcome.batch.duplicate,
            flow_records=len(batch.flows),
            agents_found=len(outcome.result.register.agents),
            review_candidates=len(outcome.result.review_candidates),
            changes=len(outcome.diff.actionable),
            drift_findings=len(outcome.drift),
            coverage=round(outcome.coverage.parsed_fraction, 3),
        )
        return BatchAccepted(
            batch_id=outcome.batch.id,
            scan_id=outcome.scan_id,
            duplicate=outcome.batch.duplicate,
            agents_found=len(outcome.result.register.agents),
            review_candidates=len(outcome.result.review_candidates),
            coverage_note=outcome.coverage_note,
        )

    @app.get("/v1/register")
    def get_register(
        principal: Auth, unsanctioned_only: bool = False, account: str | None = None
    ) -> dict:
        account_id = scope(principal, account)
        agents = AgentStore(app.state.db)
        records = (
            agents.unsanctioned(account_id)
            if unsanctioned_only
            else agents.list_for_account(account_id)
        )
        return {
            "account_id": account_id,
            "catalogue_revision": RANGES_REVISION,
            "agents": [_render(a) for a in records],
        }

    @app.get("/v1/scans")
    def get_scans(principal: Auth, limit: int = 20, account: str | None = None) -> dict:
        account_id = scope(principal, account)
        scans = ScanStore(app.state.db)
        return {
            "account_id": account_id,
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
                for s in scans.scans_for(account_id, limit=min(limit, 100))
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
        if existing is None or not principal.covers(existing.identity.account_id):
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

        # Sanctioning is the only action in the system that grants authority.
        # It is logged separately from the request so it survives a log level
        # that drops request noise.
        event(
            log, "agent.sanctioned",
            account_id=existing.identity.account_id, agent_id=agent_id,
            operator=body.operator, principal=agent.identity.principal,
        )
        return _render(agent)

    @app.post("/v1/agents/{agent_id}/status")
    def set_status(agent_id: str, body: StatusRequest, principal: Auth) -> dict:
        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or not principal.covers(existing.identity.account_id):
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

    @app.get("/v1/report", response_class=HTMLResponse)
    def get_report(principal: Auth, account: str | None = None) -> HTMLResponse:
        """Render the current register as the report a customer reads.

        Served rather than only written to a file because the second scan
        onward, someone wants a link rather than an attachment — and an
        attachment that has to be re-sent every week is a report that stops
        being sent.

        Rebuilt from stored state on each request. The alternative, caching the
        rendered page at ingestion, means a report that silently goes stale
        after a sanction and shows an agent as unsanctioned after someone
        approved it.
        """
        account_id = scope(principal, account)
        agents = AgentStore(app.state.db)
        scans = ScanStore(app.state.db)

        register = Register()
        for agent in agents.list_for_account(account_id):
            register.agents[agent.id] = agent

        latest = scans.latest_scan(account_id)
        result = ScanResult(
            register=register, verdicts=[], telemetry=[],
            principals_seen=latest.principals_seen if latest else 0,
        )
        coverage = Coverage(
            parsed_fraction=latest.coverage if latest else 1.0,
            truncated=latest.truncated if latest else False,
        ) if latest else None

        diff = ScanDiff()
        previous = scans.latest_scan_before(account_id, latest.id) if latest else None
        if latest and previous:
            diff = compare(
                register.agents,
                scans.observations_for_scan(latest.id),
                scans.observations_for_scan(previous.id),
                previous_scan_id=previous.id, current_scan_id=latest.id,
            )

        return HTMLResponse(render(
            result, account_label=account_id,
            generated_at=now(), diff=diff, coverage=coverage,
        ))

    @app.get("/v1/agents/{agent_id}/audit")
    def get_audit(agent_id: str, principal: Auth) -> dict:
        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or not principal.covers(existing.identity.account_id):
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
