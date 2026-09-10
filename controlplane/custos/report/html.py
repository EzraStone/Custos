"""The scan report.

This file is the product for the whole of A1. Everything upstream exists to
make this document true, and the exit criterion for A1 is that a report run
against a real environment surprises the person who owns it.

Three editorial rules govern what goes in it, and each exists because of a way
reports like this fail:

1. **Every finding names an owner or is segregated.** SEC-20. A list a security
   lead cannot route is a list they stop opening.
2. **Every finding carries the sentences behind it.** The workload owner will
   dispute the finding. They should be arguing with a byte ratio, not with a
   confidence score.
3. **Every limitation is stated in the document.** What was not collected, how
   stale the catalogue is, what the spend figures are worth. A report that
   overstates once is never trusted again, and this is a category where the
   customer's security team is professionally suspicious by disposition.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

from ..baseline import Drift
from ..classify import Verdict
from ..diff import Change, ChangeKind, ScanDiff
from ..register.model import Agent, BlastRadius
from ..scan import ScanResult
from ..spend import PRICES_REVISION

_RADIUS_LABEL = {
    BlastRadius.DESTRUCTIVE: "can destroy",
    BlastRadius.WRITE: "can write",
    BlastRadius.READ: "read only",
}


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _short_principal(principal: str) -> str:
    return principal.rsplit("/", 1)[-1]


def _money(amount: float) -> str:
    if amount < 1:
        return "&lt;$1"
    return f"${amount:,.0f}"


def _regions_row(agent: Agent) -> str:
    """Where this agent has been seen, when it is more than one place.

    One region is the ordinary case and printing it on every row would be a
    column of the same word. Several is the interesting case, and it carries
    the caveat that still applies: spend is now summed across regions, and
    reach is not — the tools and data stores listed are the ones the scan that
    last saw this agent observed, in one region.
    """
    if len(agent.regions) < 2:
        return ""
    named = ", ".join(_e(r) for r in sorted(agent.regions))
    return (
        f'<div><dt>Regions</dt><dd>{named} '
        f'<span class="muted">(reach from one)</span></dd></div>'
    )


def _agent_row(agent: Agent) -> str:
    owner = agent.identity.owner_team or agent.identity.owner_human or "unattributed"
    contact = (
        f'<span class="contact">{_e(agent.identity.owner_human)}</span>'
        if agent.identity.owner_human
        else ""
    )
    reach_items = sorted(agent.reach.tools | agent.reach.data_stores)
    reach = ", ".join(_e(r) for r in reach_items[:6]) or "none observed"
    if len(reach_items) > 6:
        reach += f" <span class='muted'>and {len(reach_items) - 6} more</span>"

    evidence = "".join(f"<li>{_e(line)}</li>" for line in agent.provenance.evidence)
    radius = agent.reach.blast_radius

    return f"""
    <article class="finding radius-{_e(radius)}">
      <header>
        <h3>{_e(_short_principal(agent.identity.principal))}</h3>
        <span class="radius">{_e(_RADIUS_LABEL[radius])}</span>
      </header>
      <dl class="meta">
        <div><dt>Owner</dt><dd>{_e(owner)} {contact}</dd></div>
        <div><dt>Principal</dt><dd class="mono">{_e(agent.identity.principal)}</dd></div>
        <div><dt>Compute</dt><dd>{_e(agent.identity.compute or "unknown")}</dd></div>
        <div><dt>Confidence</dt><dd>{agent.provenance.confidence:.2f}</dd></div>
        <div><dt>Est. spend</dt><dd>{_money(agent.monthly_spend_usd)}/mo</dd></div>
        <div><dt>Status</dt><dd>{_e(agent.status)}</dd></div>
        {_regions_row(agent)}
      </dl>
      <p class="reach"><span class="label">Reaches</span> {reach}</p>
      <details>
        <summary>Why this was flagged</summary>
        <ul class="evidence">{evidence}</ul>
      </details>
    </article>"""


@dataclass(frozen=True, slots=True)
class Review:
    """One workload the classifier was unsure about, as the report shows it.

    A separate shape from `Verdict` because the two sources of review
    candidates carry different things. A scan has the live verdict, features
    and firings included. A report rendered from the store has only what was
    written down — and, crucially, one thing the live verdict cannot know:
    how many previous scans said the same thing.

    Reconstructing a `Verdict` from stored rows was the alternative, and it
    would have meant inventing firings with contributions to make `evidence`
    come back out. Fabricated evidence in the one section of the report whose
    entire purpose is to say "we are not sure" is not a trade worth making.
    """

    principal: str
    confidence: float
    evidence: tuple[str, ...] = ()
    seen_in_scans: int = 1

    @classmethod
    def from_verdict(cls, verdict: Verdict) -> Review:
        return cls(
            principal=verdict.principal,
            confidence=verdict.confidence,
            evidence=tuple(verdict.evidence),
        )


def _recurrence(seen: int) -> str:
    """How often this workload has landed here, when that is worth saying.

    One scan is the default and stating it adds nothing. Repetition is the
    signal: a workload uncertain in every scan for a month is a standing
    question about the account, not a noisy window.
    """
    if seen <= 1:
        return ""
    return (
        f'<div><dt>Seen in</dt><dd>{seen} scans</dd></div>'
    )


@dataclass(frozen=True, slots=True)
class Question:
    """An internal address that behaves like a model endpoint, and who reaches it.

    Not a finding, and printed in its own section for that reason. Everything
    else in this document is something we concluded; this is the one thing we
    are asking. It matters more than its size suggests: a workload whose model
    calls go through an address we do not recognise has no model traffic we
    can see, so it produces no finding of any kind — and a report with nothing
    in it is exactly what that account looks like.
    """

    address: str
    question: str
    reached_by: tuple[str, ...] = ()


def _review_row(review: Review) -> str:
    evidence = "".join(f"<li>{_e(line)}</li>" for line in review.evidence)
    return f"""
    <article class="finding review">
      <header>
        <h3>{_e(_short_principal(review.principal))}</h3>
        <span class="radius">needs a human</span>
      </header>
      <dl class="meta">
        <div><dt>Principal</dt><dd class="mono">{_e(review.principal)}</dd></div>
        <div><dt>Confidence</dt><dd>{review.confidence:.2f}</dd></div>
        {_recurrence(review.seen_in_scans)}
      </dl>
      <details><summary>What was observed</summary>
        <ul class="evidence">{evidence}</ul></details>
    </article>"""


# What each absent flow log field costs the report, phrased as what the report
# may not claim. A reader has to be able to tell "we looked and found none"
# from "the log this account keeps cannot answer that".
_FIELD_COSTS = {
    "dstport": "Destination ports were not recorded by this account's flow "
               "log, so nothing here is identified as an MCP server or a "
               "datastore. Their absence from the scope above is a gap in the "
               "log, not a finding.",
    "pkt-dst-aws-service": "AWS service annotations were not recorded, so "
                           "Bedrock and S3 traffic was classified from address "
                           "ranges alone. An AWS endpoint outside those ranges "
                           "reads as an ordinary external address.",
    "pkt-src-aws-service": "The AWS service at the source end was not "
                           "recorded, so the return leg of an AWS conversation "
                           "arrives unattributed.",
    "log-status": "AWS's own NODATA and SKIPDATA markers were not recorded, so "
                  "the coverage figure above cannot account for records AWS "
                  "dropped before we read them. It is an upper bound.",
}


def _where_missing(
    field: str,
    missing_in: dict[str, tuple[str, ...]],
    covered: tuple[str, ...],
) -> str:
    """Which regions lack this field, when that is fewer than all of them.

    A flow log format is set per flow log, and an account with three regions
    has three of them. A field absent in one is a gap somebody can go and fix
    in one place; describing it as a property of the account hides both where
    it is and that the rest of the estate is fine.

    Silent when the field is missing everywhere: the regions covered are named
    two sentences below, and repeating them here would read as if somewhere
    still had the field.
    """
    regions = tuple(missing_in.get(field, ()))
    if not regions or len(covered) < 2 or set(regions) >= set(covered):
        return ""
    rest = [r for r in covered if r not in regions]
    return (
        f" That is true of {', '.join(_e(r) for r in regions)}; "
        f"{', '.join(_e(r) for r in rest)} "
        f"{'do' if len(rest) > 1 else 'does'} record it."
    )


def _format_limits(coverage: Coverage | None) -> list[str]:
    """What the account's flow log format prevents this report from saying."""
    if coverage is None:
        return []

    missing_in = dict(coverage.missing_in)
    items = [
        _FIELD_COSTS[name] + _where_missing(name, missing_in, coverage.regions)
        for name in coverage.missing_fields
        if name in _FIELD_COSTS
    ]
    if coverage.regions:
        named = ", ".join(_e(r) for r in coverage.regions)
        items.append(
            f"This report covers {named} and no other region. An AWS account is "
            "a region-by-region thing: a workload in another region has its own "
            "flow logs, its own interfaces, and no representation here at all. "
            "Everything above is a statement about the regions named, not about "
            "the account."
        )
    if coverage.read_errors:
        items.append(
            f"{coverage.read_errors:,} AWS read{'s' if coverage.read_errors != 1 else ''} "
            "failed while this scan was collected, after retries. Anything they "
            "would have named is missing: a finding under 'Unattributed' may be "
            "there for that reason rather than for want of resource tags."
        )
    if coverage.ipv6_destinations:
        items.append(
            f"{coverage.ipv6_destinations:,} of the public destinations reached "
            "were IPv6. The model endpoint catalogue is IPv4 only — there are no "
            "published provider ranges for IPv6 we can verify, and guessing one "
            "would manufacture findings out of unrelated traffic. An agent "
            "reaching a provider over IPv6 does not appear above at all."
        )
    if coverage.direction_undecided:
        items.append(
            f"{coverage.direction_undecided:,} flow records were discarded "
            "because this account's flow log does not record direction and "
            "neither end could be established as the interface's own address. "
            "Their bytes are in no figure above."
        )
    return items


def _limitations(
    result: ScanResult,
    degraded: list[str],
    declared: list[str] | None = None,
    coverage: Coverage | None = None,
) -> str:
    items = [
        "Payload contents were never collected. Identities, endpoints, byte "
        "counts, timings, and protocol fingerprints are the entire input to "
        "every finding above (SEC-18).",
        "Nothing in this report authorises anything. A discovered agent has no "
        "standing until an operator grants it explicitly (SEC-17).",
        f"Model endpoint catalogue revision {_e(result.catalogue_revision)}. A "
        "provider endpoint added after that date would not be recognised, and "
        "an agent using only that provider would not appear here.",
        "Blast radius is read from IAM policy, not from observed traffic. It "
        "states what the credential permits, not what the agent has done.",
    ]
    # What this account added to the catalogue, and what it therefore did not.
    # A finding is only as good as the catalogue that produced it, and a reader
    # who does not know whether a gateway was declared cannot tell an account
    # with no agents from an account whose agents are behind one.
    if declared:
        items.append(
            "This account declared "
            + _e(", ".join(declared))
            + " as model endpoints. Agents reaching them are visible because "
            "somebody said so; the catalogue would not have recognised them. A "
            "declaration naming a region applies only there, because a private "
            "address is a different host in every region."
        )
    else:
        items.append(
            "This account has declared no additional model endpoints. If model "
            "calls here go through a self-hosted gateway, the agents making "
            "them do not appear above at all — they have no model traffic we "
            "can see."
        )

    # The account's own revision, not the module default. An account that
    # supplied its rates should not read a caveat saying its figures came from
    # a placeholder, and one that has not must read exactly that.
    if result.prices_revision == PRICES_REVISION:
        items.append(
            "Spend figures use unverified placeholder pricing and are valid "
            "only for ranking agents against each other. Do not reconcile them "
            "against an invoice. Supply your own rates with `custos set-rate` "
            "and these become your numbers."
        )
    else:
        items.append(
            f"Spend figures use rates this account supplied "
            f"({_e(result.prices_revision)}). They are still derived from wire "
            "bytes rather than from token counts, so they remain estimates — "
            "but they are estimates at your prices rather than ours."
        )
    items.extend(_format_limits(coverage))
    if degraded:
        items.append(
            "Load balancer access logs were not available for this scan. The "
            "strongest single signal was therefore unavailable, and agents that "
            "would otherwise be confirmed may appear under review instead. "
            "Recall is reduced; nothing above is a false positive as a result."
        )
    return "".join(f"<li>{item}</li>" for item in items)


def _change_row(change: Change) -> str:
    owner = change.owner_team or "unattributed"
    return f"""
    <li class="change change-{_e(change.kind)}">
      <span class="what">{_e(change.detail)}</span>
      <span class="who">{_e(owner)}</span>
    </li>"""


def _changes_section(diff: ScanDiff) -> str:
    """What is different since the last scan.

    Placed above the register, because a reader who has seen last week's report
    is here for this section. Putting the full inventory first is how the third
    report goes unread.
    """
    if diff.previous_scan_id is None:
        return ""

    if not diff.actionable:
        return """
<section>
  <h2>Since the last scan</h2>
  <p class="lede">Nothing changed. The same agents, the same reach, the same
  permissions.</p>
</section>"""

    escalations = [c for c in diff.actionable if c.kind is ChangeKind.BLAST_RADIUS_INCREASED]
    lead = (
        "<p class='alarm'>A credential gained permissions that increase what it "
        "could destroy. That is the finding on this page most worth acting on "
        "today.</p>"
        if escalations else ""
    )
    return f"""
<section>
  <h2>Since the last scan</h2>
  <p class="lede">{_e(diff.headline)}</p>
  {lead}
  <ul class="changes">{"".join(_change_row(c) for c in diff.actionable)}</ul>
</section>"""


def _drift_section(drift: list[Drift], agents: dict[str, Agent]) -> str:
    """Departures from each agent's own established baseline.

    Deliberately phrased as questions. Custos does not know whether an agent is
    compromised, and a section that implied otherwise would be the one claim on
    this page the customer could disprove.
    """
    if not drift:
        return ""

    rows = []
    for d in drift:
        agent = agents.get(d.agent_id)
        name = _short_principal(agent.identity.principal) if agent else d.agent_id
        owner = (agent.identity.owner_team if agent else "") or "unattributed"
        rows.append(
            f'<li class="change"><span class="what">{_e(name)} {_e(d.question)}</span>'
            f'<span class="who">{_e(owner)}</span></li>'
        )

    return f"""
<section>
  <h2>Behaviour worth asking about</h2>
  <p class="lede">Each of these is an agent doing something it has not done
  before, measured against its own history. None of it is evidence of a
  problem — it is a list of questions worth putting to the people who own these
  workloads.</p>
  <ul class="changes">{"".join(rows)}</ul>
</section>"""


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the account this scan actually saw.

    Carried into the report rather than kept in a log, because it changes what
    the report means. "We found nothing" and "we found nothing in the 40% of
    your traffic we could read" are different sentences, and only one of them
    is worth acting on.
    """

    parsed_fraction: float = 1.0
    truncated: bool = False
    skipped_records: int = 0
    scope_named: int = 0
    scope_total: int = 0
    parse_by_region: tuple[tuple[str, float], ...] = ()
    """How much of each region's flow log parsed, when there is more than one.

    parsed_fraction above is the worst of these. This is what lets the banner
    name the region it belongs to instead of stating a fraction of an account
    that no single flow log has."""

    missing_in: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """For each missing field, which of the regions covered lack it.

    Pairs rather than a mapping because Coverage is frozen and a report is
    passed around. Empty for a single-region report, where the answer is
    always "the region this covers"."""

    missing_fields: tuple[str, ...] = ()
    """Flow log fields this account's format does not carry.

    An account whose format has no port field has no MCP servers in its
    register. The report has to say that, or its silence on MCP servers reads
    as a finding rather than as a field nobody looked at."""

    direction_undecided: int = 0
    """Records dropped because their direction could not be established.
    Coverage the parse counters do not show, because the lines parsed."""
    regions: tuple[str, ...] = ()
    """Which AWS regions this scan covered.

    An AWS account is a region-by-region thing. A workload in eu-west-1 has its
    own flow logs, its own interfaces, and no representation whatsoever in a
    scan of us-east-1 — so "no unsanctioned agents found" is a claim about one
    region, and a report that does not name it is making a claim about the
    account that nobody checked."""

    read_errors: int = 0
    """AWS reads that failed after retries while this scan was collected.

    An interface nobody could describe produces a finding with no owner, which
    is exactly what an account with no resource tags produces. Saying the count
    is what lets a reader tell those apart instead of concluding something
    about their tagging."""

    ipv6_destinations: int = 0
    """Public IPv6 addresses this scan's traffic reached.

    The provider catalogue is IPv4 only. A model endpoint reached over IPv6 is
    classified as an ordinary external address, so the agent behind it makes no
    finding at all — the same shape as an undeclared gateway, and it gets the
    same treatment here: said rather than left to look like a clean account."""

    @property
    def complete(self) -> bool:
        return self.parsed_fraction >= 0.95 and not self.truncated and not self.skipped_records

    @property
    def scope_readable(self) -> float:
        """Zero destinations is fully readable. There is no unreadable scope on
        a scan that reached nothing internal."""
        if self.scope_total <= 0:
            return 1.0
        return self.scope_named / self.scope_total


def _scope_banner(coverage: Coverage | None) -> str:
    """A separate banner, because it is a separate problem.

    Incomplete coverage says findings may be missing. An unreadable scope says
    every finding below is present and correct, and the approval decision on
    each one is a list of IP addresses. Folding the two together would let a
    reader discharge both with one glance at a caveat.
    """
    if coverage is None or coverage.scope_total <= 0 or coverage.scope_readable >= 0.5:
        return ""

    return f"""
<div class="banner">
  <span class="tag">Scope is mostly addresses</span>
  <p>{coverage.scope_named} of {coverage.scope_total} internal destinations
  could be named. The findings below are unaffected — but approving one means
  approving a list of IP addresses rather than a list of services. Tagging the
  network interfaces behind those services is what makes this readable.</p>
</div>"""


def _parsed_where(coverage: Coverage) -> str:
    """Which regions read badly, when the report covers more than one.

    The figure is the worst region's. Over a three-region account that is a
    number about one flow log, and an operator cannot go and look at it until
    the banner says which.
    """
    poor = [region for region, parsed in coverage.parse_by_region if parsed < 0.95]
    if not poor or len(coverage.parse_by_region) < 2:
        return ""
    return f" in {', '.join(_e(region) for region in poor)}"


def _coverage_banner(coverage: Coverage | None) -> str:
    """A banner above everything when the scan could not see the whole account.

    Above everything deliberately. A caveat in a limitations section at the
    bottom is a caveat nobody reads before forming a conclusion.
    """
    if coverage is None or coverage.complete:
        return ""

    reasons = []
    if coverage.parsed_fraction < 0.95:
        reasons.append(
            f"only {coverage.parsed_fraction:.0%} of flow log lines parsed"
            + _parsed_where(coverage)
        )
    if coverage.truncated:
        reasons.append("the collection window was truncated at its record limit")
    if coverage.skipped_records:
        reasons.append(
            f"AWS dropped {coverage.skipped_records:,} records it could not capture"
        )

    return f"""
<div class="banner">
  <span class="tag">Incomplete coverage</span>
  <p>This scan did not see the whole account: {_e("; ".join(reasons))}. Agents
  running in the traffic we could not read would not appear below, so an
  absence of findings means less here than it usually would.</p>
</div>"""


def render(
    result: ScanResult,
    account_label: str,
    generated_at: datetime,
    diff: ScanDiff | None = None,
    drift: list[Drift] | None = None,
    coverage: Coverage | None = None,
    declared: list[str] | None = None,
    reviews: list[Review] | None = None,
    questions: list[Question] | None = None,
) -> str:
    """Render the scan report as a single self-contained HTML document.

    `reviews` overrides what the scan result carries, for the caller rendering
    from the store rather than from a scan in hand. It is a parameter rather
    than something read off `result` because the stored rows know how many
    scans each workload has appeared in and a live `ScanResult` does not.
    """
    findings = result.register.attributed_findings
    unattributed = result.register.unattributed_findings
    review_rows = (
        reviews
        if reviews is not None
        else [Review.from_verdict(v) for v in result.review_candidates]
    )
    degraded = sorted({s for v in result.verdicts for s in v.unavailable})

    writers = [a for a in result.register.unsanctioned if a.reach.blast_radius.rank > 0]
    total_spend = sum(a.monthly_spend_usd for a in result.register.unsanctioned)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Custos scan — {_e(account_label)}</title>
<style>{_CSS}</style>
</head><body><main class="sheet">

{_coverage_banner(coverage)}
{_scope_banner(coverage)}

<header class="masthead">
  <p class="gloss">Agent discovery scan</p>
  <h1>{_e(account_label)}</h1>
  <p class="headline">{_e(result.headline)}</p>
  <dl class="docctl">
    <div><dt>Generated</dt><dd>{_e(generated_at.strftime("%d %b %Y %H:%M UTC"))}</dd></div>
    <div><dt>Principals seen</dt><dd>{result.principals_seen}</dd></div>
    <div><dt>Agents found</dt><dd>{len(result.register.unsanctioned)}</dd></div>
    <div><dt>Write-capable</dt><dd>{len(writers)}</dd></div>
    <div><dt>For review</dt><dd>{len(review_rows)}</dd></div>
    <div><dt>Est. spend</dt><dd>{_money(total_spend)}/mo</dd></div>
  </dl>
</header>

{_changes_section(diff) if diff is not None else ""}

<section>
  <h2>Unsanctioned agents</h2>
  <p class="lede">Workloads making autonomous model calls that nobody has
  registered. Ordered by what each one could destroy, not by how confident we
  are that it exists.</p>
  {"".join(_agent_row(a) for a in findings) or '<p class="empty">None found.</p>'}
</section>

{_unattributed_section(unattributed)}
{_drift_section(drift or [], result.register.agents)}
{_questions_section(questions or [])}
{_review_section(review_rows)}

<section>
  <h2>What this report does not claim</h2>
  <ul class="limits">{_limitations(result, degraded, declared, coverage)}</ul>
</section>

<footer>
  Custos · agent discovery · catalogue {_e(result.catalogue_revision)}
</footer>
</main></body></html>"""


def _unattributed_section(agents: list[Agent]) -> str:
    if not agents:
        return ""
    return f"""
<section>
  <h2>Unattributed findings</h2>
  <p class="lede">These are agents by the same evidence as those above, but no
  owner could be resolved from resource tags, role tags, or IAM path. They are
  listed separately rather than mixed in, because a finding nobody owns is a
  finding nobody actions.</p>
  {"".join(_agent_row(a) for a in agents)}
</section>"""


def _questions_section(questions: list[Question]) -> str:
    """The one section of this document that asks rather than concludes.

    Placed after the findings and before the review band, because it changes
    how the rest is read: an account with an undeclared gateway has agents
    that produce no evidence at all, and a short findings list above an open
    question here means much less than a short findings list alone.
    """
    if not questions:
        return ""
    rows = "".join(
        f"""
    <article class="finding question">
      <header>
        <h3 class="mono">{_e(q.address)}</h3>
        <span class="radius">needs an answer</span>
      </header>
      <p>{_e(q.question)}</p>
      {f'<p class="who">Reached by {_e(", ".join(_short_principal(p) for p in q.reached_by))}.</p>'
       if q.reached_by else ""}
    </article>"""
        for q in questions
    )
    return f"""
<section>
  <h2>Questions</h2>
  <p class="lede">Internal addresses that behave like model endpoints: far more
  sent than received, by workloads that never reach a model provider we
  recognise. Nothing here has been classified as anything — a heuristic that
  promoted an internal address to a model endpoint on its own would manufacture
  agents out of any busy internal service. If one of these is a model gateway,
  declare it and the next scan will see what runs behind it.</p>
  {rows}
</section>"""


def _review_section(reviews: list[Review]) -> str:
    if not reviews:
        return ""
    return f"""
<section>
  <h2>For review</h2>
  <p class="lede">Workloads that resemble agents without meeting the bar. Most
  are batch jobs or build pipelines. They appear here rather than in the
  register because discovery is not permitted to decide this on its own.</p>
  {"".join(_review_row(r) for r in reviews)}
</section>"""


_CSS = """
:root{
  --ink:#14181f; --ink-soft:#3d4653; --ink-faint:#6c7686;
  --paper:#e9ecf0; --raised:#f4f6f8; --rule:#c2c9d3; --hair:#d6dbe2;
  --seal:#8c2f39; --verdigris:#2d6a6b; --amber:#8a6a1f;
  --display:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --body:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --ink:#e6e9ee; --ink-soft:#b3bcc9; --ink-faint:#8b95a4;
    --paper:#12151a; --raised:#1a1f26; --rule:#2f3742; --hair:#242b34;
    --seal:#d4767f; --verdigris:#5fa6a7; --amber:#c9a84f;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);
  font-size:16px;line-height:1.62;-webkit-font-smoothing:antialiased}
.sheet{max-width:58rem;margin:0 auto;padding:0 1.5rem 6rem}
.masthead{padding:4rem 0 2rem;border-bottom:2px solid var(--ink)}
.gloss{font-family:var(--mono);font-size:.68rem;letter-spacing:.22em;
  text-transform:uppercase;color:var(--seal);margin:0 0 1rem}
h1{font-family:var(--display);font-size:clamp(2rem,6vw,3rem);line-height:1;
  margin:0;font-weight:600;letter-spacing:-.015em}
.headline{font-family:var(--display);font-style:italic;font-size:1.3rem;
  color:var(--ink-soft);margin:1.1rem 0 0;max-width:40rem}
.docctl{display:grid;grid-template-columns:repeat(auto-fit,minmax(8rem,1fr));
  gap:1rem 1.5rem;margin-top:2.5rem;padding-top:1.5rem;border-top:1px solid var(--hair);
  font-family:var(--mono);font-size:.72rem}
.docctl dt{text-transform:uppercase;letter-spacing:.12em;color:var(--ink-faint);margin:0 0 .2rem}
.docctl dd{margin:0}
section{padding-top:3rem;margin-top:3rem;border-top:1px solid var(--hair)}
section:first-of-type{border-top:none}
h2{font-family:var(--display);font-size:1.9rem;margin:0 0 .75rem;font-weight:600}
.lede{color:var(--ink-soft);max-width:42rem;margin:0 0 2rem}
.empty{color:var(--ink-faint);font-style:italic}
.finding{border:1px solid var(--rule);background:var(--raised);
  padding:1.2rem 1.35rem;margin-bottom:1.25rem;border-left-width:3px}
.finding.radius-destructive{border-left-color:var(--seal)}
.finding.radius-write{border-left-color:var(--amber)}
.finding.radius-read{border-left-color:var(--verdigris)}
.finding.review{border-left-color:var(--ink-faint);border-left-style:dashed}
.finding header{display:flex;flex-wrap:wrap;align-items:baseline;gap:.75rem;
  margin-bottom:.9rem}
.finding h3{font-family:var(--mono);font-size:1rem;margin:0;font-weight:600;
  word-break:break-all}
.radius{margin-left:auto;font-family:var(--mono);font-size:.66rem;
  letter-spacing:.12em;text-transform:uppercase;border:1px solid var(--rule);
  padding:.2rem .45rem;color:var(--ink-faint);white-space:nowrap}
.radius-destructive .radius{color:var(--seal);border-color:var(--seal)}
.radius-write .radius{color:var(--amber);border-color:var(--amber)}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));
  gap:.7rem 1.25rem;margin:0 0 1rem;font-size:.82rem}
.meta dt{font-family:var(--mono);font-size:.62rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-faint);margin:0 0 .15rem}
.meta dd{margin:0}
.mono,.contact{font-family:var(--mono);font-size:.9em;word-break:break-all}
.contact{color:var(--ink-faint)}
.muted{color:var(--ink-faint)}
.reach{font-size:.85rem;margin:0 0 .9rem;padding-top:.9rem;
  border-top:1px solid var(--hair)}
.reach .label{font-family:var(--mono);font-size:.62rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-faint);margin-right:.5rem}
details summary{cursor:pointer;font-family:var(--mono);font-size:.7rem;
  letter-spacing:.1em;text-transform:uppercase;color:var(--seal)}
.finding.question{border-left-color:var(--amber)}
.finding.question p{margin:.5rem 0 0;max-width:46rem}
.finding.question .who{color:var(--ink-soft);font-size:.86rem}
.evidence{margin:.85rem 0 0;padding-left:1.1rem;font-size:.86rem;
  color:var(--ink-soft);max-width:44rem}
.evidence li{margin-bottom:.5rem}
.banner{border:1px solid var(--seal);border-left-width:3px;background:var(--raised);
  padding:1.1rem 1.3rem;margin:2.5rem 0 0;max-width:46rem}
.banner .tag{font-family:var(--mono);font-size:.66rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--seal);display:block;margin-bottom:.5rem}
.banner p{margin:0;font-size:.92rem}
.alarm{max-width:42rem;border-left:3px solid var(--seal);padding-left:1.1rem;
  color:var(--ink);font-family:var(--display);font-size:1.1rem}
.changes{list-style:none;margin:0;padding:0;max-width:48rem}
.change{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:baseline;
  padding:.75rem 0;border-bottom:1px solid var(--hair);font-size:.9rem}
.change .what{flex:1 1 22rem}
.change .who{font-family:var(--mono);font-size:.66rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-faint)}
.change-blast_radius_increased{border-left:3px solid var(--seal);
  padding-left:.9rem;margin-left:-1.2rem}
.change-appeared{border-left:3px solid var(--amber);padding-left:.9rem;
  margin-left:-1.2rem}
.limits{max-width:44rem;color:var(--ink-soft);padding-left:1.1rem}
.limits li{margin-bottom:.7rem}
footer{margin-top:4rem;padding-top:1.5rem;border-top:2px solid var(--ink);
  font-family:var(--mono);font-size:.7rem;color:var(--ink-faint)}
@media print{body{background:#fff;font-size:10.5pt}.finding{break-inside:avoid}}
"""
