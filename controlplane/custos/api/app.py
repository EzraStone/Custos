"""The control plane HTTP API.

Small on purpose. Three things a collector or an operator needs — ship a batch,
read the register, sanction an agent — and nothing else. Every endpoint that
mutates state names the human who did it.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..batch import Batch, BatchAccepted
from ..catalog import RANGES_REVISION
from ..deliver import Channel, notify
from ..deliver import config as deliver_config
from ..diff import ScanDiff, compare
from ..logging import event, get
from ..pipeline import ingest
from ..register.model import Status
from ..register.store import Register, TransitionError
from ..report import Coverage, Question, Review, render
from ..scan import ScanResult
from ..spend import PRICES_REVISION
from ..store.agents import AgentStore
from ..store.db import now, open_database
from ..store.declarations import CandidateStore, DeclarationStore
from ..store.rates import RateStore
from ..store.scans import ReviewStore, ScanStore
from .auth import Principal, TokenStore, parse_bearer
from .compression import GzipRequestMiddleware

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
    channels: list[Channel] | None = None,
) -> FastAPI:
    database = conn if conn is not None else open_database()
    token_store = tokens if tokens is not None else TokenStore.from_env()
    delivery = channels if channels is not None else deliver_config.from_env()

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
    # Before anything else reads the body. The collector compresses batches
    # because a full window is 203MB of JSON and 6.2MB gzipped, and Starlette
    # decompresses responses rather than requests.
    app.add_middleware(GzipRequestMiddleware)

    app.state.db = database
    app.state.tokens = token_store
    app.state.channels = delivery

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

        # Delivery happens here rather than in the pipeline, because ingestion
        # runs inside a transaction and a webhook has no business holding one
        # open. A failure is logged and reported in the response; it never
        # affects the 202, because the batch was accepted and the findings are
        # in the register regardless of whether anyone was told.
        delivered = 0
        if app.state.channels:
            notified = notify(
                app.state.db, outcome, batch.account_id, app.state.channels, now()
            )
            delivered = sum(d.sent for d in notified.deliveries)
            if not notified.ok:
                event(
                    log, "delivery.partial", account_id=batch.account_id,
                    failures=[d.channel for d in notified.deliveries if not d.ok],
                )

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
            delivered=delivered,
        )
        return BatchAccepted(
            batch_id=outcome.batch.id,
            scan_id=outcome.scan_id,
            duplicate=outcome.batch.duplicate,
            agents_found=len(outcome.result.register.agents),
            review_candidates=len(outcome.result.review_candidates),
            coverage_note=outcome.coverage_note,
            delivered=delivered,
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
        # The revision that priced these figures, alongside the catalogue that
        # produced the findings. A client labelling a spend column needs the
        # account's answer, and /healthz can only give it the process default.
        rates = RateStore(app.state.db).rates_for(account_id)
        return {
            "account_id": account_id,
            "catalogue_revision": RANGES_REVISION,
            "prices_revision": rates.revision,
            "agents": [_render(a) for a in records],
        }

    @app.get("/v1/accounts")
    def get_accounts(principal: Auth) -> dict:
        """The accounts this credential covers.

        A fleet token names several. Without this the console can only learn
        that fact by making a request that fails, and can only learn *which*
        accounts by parsing them out of the prose in a 400 — which would make
        rewording an error message a breaking change.

        This discloses nothing: the holder already has access to every account
        listed, and the same set governs every other route.
        """
        return {"accounts": sorted(principal.accounts)}

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
                    # How much of this scan's approval scope was a name rather
                    # than an address. A scan that is fully covered and fully
                    # unreadable produces correct findings nobody can act on.
                    "scope_readable": s.scope_readable,
                    "scope_named": s.scope_named,
                    "scope_total": s.scope_total,
                }
                for s in scans.scans_for(account_id, limit=min(limit, 100))
            ],
        }

    @app.get("/v1/agents/{agent_id}/drift")
    def get_drift(agent_id: str, principal: Auth) -> dict:
        """How this agent's behaviour compares with its own history.

        Per agent rather than per account. Drift is a question put to one
        workload's owner — "it started reaching something new, is that
        expected" — and an account-wide list of those is a list nobody owns.

        A baseline needs history. An agent seen once has none, which is
        reported as an empty list rather than as an error: it is the normal
        state of a new finding, not a failure to answer.
        """
        from ..baseline import detect_from_history

        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or not principal.covers(existing.identity.account_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such agent")

        scans = ScanStore(app.state.db)
        history = scans.observation_history(agent_id)
        baseline, drift = detect_from_history(agent_id, history)

        return {
            "agent_id": agent_id,
            "observations": len(history),
            "drift": [
                {
                    "kind": str(d.kind),
                    "observed_at": _iso(d.observed_at),
                    # `question` rather than `detail`: every drift finding is
                    # phrased as a question to the workload's owner, because a
                    # question gets answered and an accusation gets argued with.
                    "question": d.question,
                    "detail": d.detail,
                }
                for d in sorted(drift, key=lambda d: (d.severity, str(d.kind)))
            ],
            "baseline": {
                "tools": sorted(baseline.tool_set),
                "observations": baseline.observations,
                # Whether there is enough history for drift to mean anything.
                # A caller showing drift from an unestablished baseline is
                # showing noise with a confident label on it.
                "established": baseline.established,
            },
        }

    @app.get("/v1/fleet")
    def get_fleet(principal: Auth) -> dict:
        """One line per account this credential covers.

        A customer in the target profile runs five to fifty accounts, and the
        account picker listed twelve-digit numbers with nothing to choose by.
        Somebody deciding where to spend an afternoon needs to know which
        account has unsanctioned agents that can destroy things, and which one
        has not been scanned in three weeks.

        Every account the credential covers appears, including ones with no
        scans. An account missing from this list would be one nobody thinks to
        onboard, and an unscanned account is the most important row here.
        """
        agents = AgentStore(app.state.db)
        scans = ScanStore(app.state.db)
        reviews = ReviewStore(app.state.db)
        rates = RateStore(app.state.db)

        out = []
        for account_id in sorted(principal.accounts):
            registry = agents.list_for_account(account_id)
            unsanctioned = [a for a in registry if a.unsanctioned]
            latest = scans.latest_scan(account_id)
            open_questions = _open_questions(account_id)
            out.append({
                "account_id": account_id,
                "agents": len(registry),
                "unsanctioned": len(unsanctioned),
                # The number somebody triages by. Twelve unsanctioned agents
                # that can only read is a different afternoon from one that
                # can delete.
                "destructive": sum(
                    1 for a in unsanctioned if str(a.reach.blast_radius) == "destructive"
                ),
                "last_scan": _iso(latest.started_at) if latest else None,
                "coverage": latest.coverage if latest else None,
                "scope_readable": latest.scope_readable if latest else None,
                # Which regions this account has ever been collected in. The
                # question a fleet view has to answer is whether an account is
                # covered, and one region of a three-region account looks
                # identical to a clean account from every other column here.
                "regions": scans.regions_scanned(account_id),
                "reviews": len(reviews.latest_for(account_id)),
                "gateway_questions": len(open_questions),
                "rates_verified": rates.rates_for(account_id).verified,
            })
        return {"accounts": out}

    @app.get("/v1/rates")
    def get_rates(principal: Auth, account: str | None = None) -> dict:
        """What this account pays, and when they last said so."""
        account_id = scope(principal, account)
        store = RateStore(app.state.db)
        rates = store.rates_for(account_id)
        return {
            "account_id": account_id,
            "revision": rates.revision,
            # The question a reader with a budget asks first, answered without
            # them having to interpret a revision string.
            "verified": rates.verified,
            "current": {
                provider: {
                    "input_per_mtok": price.input_per_mtok,
                    "output_per_mtok": price.output_per_mtok,
                }
                for provider, price in sorted(rates.prices.items())
            },
            "history": store.history_for(account_id),
        }

    @app.post("/v1/rates")
    def supply_rate(body: RateRequest, principal: Auth, account: str | None = None) -> dict:
        """Record what this account pays for one provider.

        Applies to the next scan. Existing figures are not recomputed: a
        report already sent to somebody with a budget should still say what it
        said, and silently restating last month's numbers at this month's rate
        would be worse than leaving them alone.
        """
        account_id = scope(principal, account)
        store = RateStore(app.state.db)
        try:
            store.supply(
                account_id, body.provider, body.input_per_mtok,
                body.output_per_mtok, body.operator, at=now(),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        event(
            log, "rate.supplied", account_id=account_id,
            provider=body.provider, operator=body.operator,
        )
        rates = store.rates_for(account_id)
        return {
            "provider": body.provider,
            "revision": rates.revision,
            "verified": rates.verified,
            "effective": "next scan",
        }

    @app.get("/v1/reviews")
    def get_reviews(principal: Auth, account: str | None = None) -> dict:
        """Workloads the classifier was unsure about in the last scan.

        Not agents and not findings. SEC-17 keeps them out of the register —
        the classifier saying "this might be an agent and I am not confident
        enough to say so" is not a claim anything downstream should act on.

        There is no path from here into the register. Promoting a maybe by hand
        is precisely what the register is not for, and a route that allowed it
        would make every guarantee about how an agent got there conditional on
        nobody having used it.

        `seen_in_scans` is what makes this worth reading. A workload uncertain
        once is one uncertain window; one uncertain in every scan for a month
        is a different thing, and the count is the only way to tell.
        """
        account_id = scope(principal, account)
        reviews = ReviewStore(app.state.db)
        return {
            "account_id": account_id,
            "reviews": [
                {**r, "seen_in_scans": reviews.recurrence(account_id, r["principal"])}
                for r in reviews.latest_for(account_id)
            ],
        }

    def _open_questions(account_id: str) -> list[dict]:
        """Gateway questions this account has not answered.

        Already-answered ones are dropped rather than shown as resolved. A
        customer who declared a gateway last week should not be asked about it
        again on every scan.
        """
        store = DeclarationStore(app.state.db)
        return [
            c for c in CandidateStore(app.state.db).latest_for(account_id)
            # Per candidate: a question asked about us-east-1 is answered only
            # by a declaration in force there. The same address in eu-west-1 is
            # a different host and still an open question.
            if not store.declared_for(account_id, c.get("region", "")).covers(c["address"])
        ]

    @app.get("/v1/gateway-candidates")
    def get_gateway_candidates(principal: Auth, account: str | None = None) -> dict:
        """Internal destinations that behave like model endpoints.

        Questions, not findings. Every one is an address a workload sends far
        more to than it gets back, reached by workloads that never talk to a
        model provider we recognise — which is either a gateway nobody
        mentioned or an unusually chatty internal API.

        Nothing here is classified as anything. A heuristic that promoted an
        address to a model endpoint on its own would manufacture agents out of
        any busy internal service, so this waits for a person and
        /v1/endpoints is where their answer goes.
        """
        account_id = scope(principal, account)
        return {
            "account_id": account_id,
            "candidates": _open_questions(account_id),
        }

    @app.get("/v1/endpoints")
    def get_endpoints(
        principal: Auth, account: str | None = None, include_withdrawn: bool = False
    ) -> dict:
        """Model endpoints this account has declared.

        A customer running every model call through an internal gateway has
        agents we cannot see, because their model traffic looks like traffic to
        an internal API. This is how they tell us, and how they check what they
        already told us.
        """
        account_id = scope(principal, account)
        records = DeclarationStore(app.state.db).records_for(
            account_id, include_withdrawn=include_withdrawn
        )
        return {
            "account_id": account_id,
            "endpoints": [
                {
                    "id": r.id,
                    "value": r.value,
                    "kind": r.kind,
                    "note": r.note,
                    "region": r.region,
                    "declared_by": r.declared_by,
                    "declared_at": _iso(r.declared_at),
                    "active": r.active,
                    "withdrawn_by": r.withdrawn_by,
                    "withdrawn_at": _iso(r.withdrawn_at) if r.withdrawn_at else None,
                }
                for r in records
            ],
        }

    @app.post("/v1/endpoints")
    def declare_endpoint(
        body: DeclareRequest, principal: Auth, account: str | None = None
    ) -> dict:
        """Declare a model endpoint.

        Takes effect on the next scan, not retroactively. Reclassifying stored
        telemetry would rewrite the history of what was found when, and a
        register whose past changes underneath an operator is one they cannot
        reason about — so the response says so rather than leaving them to
        wonder why the list did not move.
        """
        account_id = scope(principal, account)
        store = DeclarationStore(app.state.db)
        try:
            record = store.declare(
                account_id, body.value, body.kind, body.operator, body.note,
                region=body.region, at=now(),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        # Logged separately from the request, like sanctioning, because it is
        # the other decision that changes what counts as an agent.
        event(
            log, "endpoint.declared", account_id=account_id,
            value=record.value, kind=record.kind, region=record.region,
            operator=record.declared_by,
        )
        return {
            "id": record.id,
            "value": record.value,
            "kind": record.kind,
            "note": record.note,
            "region": record.region,
            "declared_by": record.declared_by,
            "declared_at": _iso(record.declared_at),
            "active": True,
            "effective": "next scan",
        }

    @app.delete("/v1/endpoints/{declaration_id}")
    def withdraw_endpoint(
        declaration_id: int,
        principal: Auth,
        operator: str,
        account: str | None = None,
    ) -> dict:
        """Withdraw a declaration. The record of it stays.

        Withdrawing narrows what counts as a model endpoint, so unlike
        declaring it can make a finding disappear. That is why the row is
        marked rather than deleted, and why this needs a name.
        """
        account_id = scope(principal, account)
        store = DeclarationStore(app.state.db)
        try:
            changed = store.withdraw(declaration_id, account_id, operator, at=now())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not changed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="no such active declaration for this account",
            )
        event(
            log, "endpoint.withdrawn", account_id=account_id,
            declaration_id=declaration_id, operator=operator,
        )
        return {"id": declaration_id, "active": False}

    @app.get("/v1/diff")
    def get_diff(principal: Auth, account: str | None = None) -> dict:
        """What changed between the two most recent scans.

        The specification is explicit that the unsanctioned set regenerating on
        every scan is what makes this a subscription rather than an audit
        engagement — but only if the second scan says something the first did
        not. The CLI has answered this since the register existed. Nothing else
        could, so the console showed a register with no sense of time.

        One scan is not an error. It is the normal state of a new account, and
        it is reported as an empty diff with a headline saying so rather than
        as a 404 that a client has to special-case.
        """
        from ..diff import compare

        account_id = scope(principal, account)
        scans = ScanStore(app.state.db)
        agents = AgentStore(app.state.db)

        history = scans.scans_for(account_id, limit=2)
        if len(history) < 2:
            return {
                "account_id": account_id,
                "previous_scan_id": None,
                "current_scan_id": history[0].id if history else None,
                "headline": "Nothing to compare yet — this account has one scan.",
                "changes": [],
            }

        current, previous = history[0], history[1]
        registry = {a.id: a for a in agents.list_for_account(account_id)}
        result = compare(
            registry,
            scans.observations_for_scan(current.id),
            scans.observations_for_scan(previous.id),
            previous_scan_id=previous.id,
            current_scan_id=current.id,
        )
        return {
            "account_id": account_id,
            "previous_scan_id": result.previous_scan_id,
            "current_scan_id": result.current_scan_id,
            "headline": result.headline,
            "changes": [
                {
                    "kind": str(c.kind),
                    "agent_id": c.agent_id,
                    "principal": c.principal,
                    "detail": c.detail,
                    "owner_team": c.owner_team,
                    "blast_radius": str(c.blast_radius),
                }
                # Actionable only. UNCHANGED entries exist so the comparison can
                # account for every agent, and shipping them would make the
                # caller filter out the majority of a large response to find
                # the handful that moved.
                for c in sorted(
                    result.actionable, key=lambda c: (-c.severity, c.principal)
                )
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
        # The register below spans every region. What the report says about
        # how the telemetry was read has to span them too, or one region's
        # flow log format gets described as the account's.
        per_region = scans.latest_scan_per_region(account_id)
        result = ScanResult(
            register=register, verdicts=[], telemetry=[],
            # The largest region rather than the sum. An IAM role is
            # account-wide, so a role running in two regions is one principal
            # and adding the regions would count it twice. What this leaves
            # out — a region with fewer principals of its own — the report
            # says out loud, because the figure sits beside a count of agents
            # taken across every region.
            principals_seen=max((s.principals_seen for s in per_region), default=0),
            # Whose rates the figures below were computed at. The register
            # rows already hold the numbers; this is what labels them.
            prices_revision=RateStore(app.state.db).rates_for(account_id).revision,
        )
        coverage = Coverage(
            # The worst region, not the last one. A region that read 40% of
            # its flow log is a region nobody has seen, and averaging it
            # against a region that read all of its own — or taking whichever
            # shipped most recently — is how that disappears.
            parsed_fraction=min((s.coverage for s in per_region), default=1.0),
            truncated=any(s.truncated for s in per_region),
            parse_by_region=_parse_by_region(per_region),
            # Counts, so they add. Each region has its own destinations, its
            # own interfaces, and its own failed reads.
            scope_named=sum(s.scope_named for s in per_region),
            scope_total=sum(s.scope_total for s in per_region),
            missing_fields=_missing_fields(per_region),
            missing_in=_missing_in(per_region),
            direction_undecided=sum(s.direction_undecided for s in per_region),
            read_errors=sum(s.read_errors for s in per_region),
            bulk_senders=sum(s.bulk_senders for s in per_region),
            # Every region this account has been collected in, not the
            # latest scan's. The register below holds agents from all of
            # them; labelling it with one scan's region tells a reader a
            # region they can see agents from was never covered.
            regions=tuple(scans.regions_scanned(account_id)),
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

        # The maybes, from the store rather than from the empty verdict list
        # above. Without this the served report shows "For review: 0" on an
        # account whose console has three of them, and the report is the
        # artefact that gets forwarded.
        reviews = ReviewStore(app.state.db)
        candidates = [
            Review(
                principal=r["principal"],
                confidence=r["confidence"],
                evidence=tuple(r["evidence"]),
                seen_in_scans=reviews.recurrence(account_id, r["principal"]),
            )
            for r in reviews.latest_for(account_id)
        ]
        questions = [
            Question(address=q["address"], question=q["question"],
                     reached_by=tuple(q["blind_principals"]))
            for q in _open_questions(account_id)
        ]

        return HTMLResponse(render(
            result, account_label=account_id,
            generated_at=now(), diff=diff, coverage=coverage,
            declared=_declared_labels(DeclarationStore(app.state.db), account_id),
            reviews=candidates,
            questions=questions,
        ))

    @app.get("/v1/agents/{agent_id}/audit")
    def get_audit(agent_id: str, principal: Auth) -> dict:
        agents = AgentStore(app.state.db)
        existing = agents.get(agent_id)
        if existing is None or not principal.covers(existing.identity.account_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such agent")
        return {"agent_id": agent_id, "entries": agents.audit_for(agent_id)}

    _mount_console(app)
    return app


def _mount_console(app: FastAPI) -> None:
    """Serve the built console, if there is one.

    Mounted last so every API route is matched first. A static mount at the
    root is greedy — registered earlier it would shadow /v1 and /healthz, and
    the symptom would be the console's index.html returned where JSON was
    expected, which reads as a client bug rather than a routing one.

    Absent build, no mount. The control plane is useful without a console and
    must not refuse to start because nobody ran npm run build.
    """
    root = Path(
        os.getenv("CUSTOS_CONSOLE_DIR")
        or Path(__file__).resolve().parents[3] / "console" / "dist"
    )
    if not (root / "index.html").is_file():
        return

    # html=True serves index.html for a directory request, which is all the
    # console needs — it has one route and no client-side router to fall back
    # for.
    app.mount("/", StaticFiles(directory=str(root), html=True), name="console")


class RateRequest(BaseModel):
    provider: str = Field(min_length=1, description="anthropic, openai, bedrock, ...")
    input_per_mtok: float = Field(gt=0, description="USD per million input tokens")
    output_per_mtok: float = Field(gt=0, description="USD per million output tokens")
    operator: str = Field(min_length=1, description="Human identity supplying the rate")


class DeclareRequest(BaseModel):
    value: str = Field(min_length=1, description="A CIDR, an address, or an AWS service name")
    kind: str = Field(default="range", pattern="^(range|aws_service)$")
    operator: str = Field(min_length=1, description="Human identity making the declaration")
    note: str = Field(default="", description="What the customer calls this endpoint")
    region: str = Field(
        default="",
        description=(
            "Region this applies to. Required for a private range, because a "
            "private address means a different host in every region"
        ),
    )


class GrantRequest(BaseModel):
    operator: str = Field(min_length=1, description="Human identity granting the authority")
    approved_tools: list[str] | None = None
    approved_data: list[str] | None = None


class StatusRequest(BaseModel):
    status: str
    operator: str = Field(min_length=1)
    reason: str = ""


def _parse_by_region(per_region: list) -> tuple[tuple[str, float], ...]:
    """How much of each region's flow log parsed.

    The banner names the region that read badly. "Only 40% of flow log lines
    parsed" over a two-region account is a sentence an operator cannot act on
    until they know which flow log to go and look at.
    """
    return tuple(
        (region, scan.coverage) for scan in per_region for region in scan.regions
    )


def _missing_fields(per_region: list) -> tuple[str, ...]:
    """Every flow log field absent from any region covered.

    A union rather than the latest scan's list. A field missing in one region
    is missing from everything that region contributed, and the report's job
    is to say what it could not look for — silence on a field half the estate
    never recorded reads as "we looked and found none".
    """
    fields: set[str] = set()
    for scan in per_region:
        fields |= set(scan.missing_fields)
    return tuple(sorted(fields))


def _missing_in(per_region: list) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """For each of those fields, which regions lack it.

    The union above says what the report may not claim; this says where to go
    and fix it. Scans with no region contribute nothing: an older collector
    sent none, and an unnamed region is not somewhere anyone can look.
    """
    where: dict[str, set[str]] = {}
    for scan in per_region:
        for region in scan.regions:
            for field in scan.missing_fields:
                where.setdefault(field, set()).add(region)
    return tuple(
        (field, tuple(sorted(regions))) for field, regions in sorted(where.items())
    )


def _declared_labels(store, account_id: str) -> list[str]:
    """What this account declared, phrased for the report's limitations list.

    The region is part of the label. A reader deciding whether a finding is
    explained by a declaration has to know whether that declaration was in
    force where the traffic was — and one that names no region was in force
    everywhere, which is a different and larger claim.
    """
    labels = []
    for r in store.records_for(account_id):
        parts = [r.value]
        if r.note:
            parts.append(f"({r.note})")
        parts.append(f"in {r.region}" if r.region else "in every region")
        labels.append(" ".join(parts))
    return labels


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
        # Summed across every region this agent runs in. A scan covers one
        # region, so the scan's own figure is a fraction of the cost whenever
        # the agent runs in more than one.
        "est_monthly_spend_usd": agent.monthly_spend_usd,
        # Every region this agent has been seen in. The figures above come from
        # the scan that last saw it, which is one region's traffic — so a
        # tools, data_stores and blast_radius above come from the scan that
        # last saw this agent, which is one region's. Spend is not: it is
        # summed.
        "regions": sorted(agent.regions),
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
