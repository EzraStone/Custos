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
        <div><dt>Est. spend</dt><dd>{_money(agent.model.est_monthly_spend_usd)}/mo</dd></div>
        <div><dt>Status</dt><dd>{_e(agent.status)}</dd></div>
      </dl>
      <p class="reach"><span class="label">Reaches</span> {reach}</p>
      <details>
        <summary>Why this was flagged</summary>
        <ul class="evidence">{evidence}</ul>
      </details>
    </article>"""


def _review_row(verdict: Verdict) -> str:
    evidence = "".join(f"<li>{_e(line)}</li>" for line in verdict.evidence)
    return f"""
    <article class="finding review">
      <header>
        <h3>{_e(_short_principal(verdict.principal))}</h3>
        <span class="radius">needs a human</span>
      </header>
      <dl class="meta">
        <div><dt>Principal</dt><dd class="mono">{_e(verdict.principal)}</dd></div>
        <div><dt>Confidence</dt><dd>{verdict.confidence:.2f}</dd></div>
      </dl>
      <details><summary>What was observed</summary>
        <ul class="evidence">{evidence}</ul></details>
    </article>"""


def _limitations(result: ScanResult, degraded: list[str]) -> str:
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
    if PRICES_REVISION == "unverified-placeholder":
        items.append(
            "Spend figures use unverified placeholder pricing and are valid "
            "only for ranking agents against each other. Do not reconcile them "
            "against an invoice."
        )
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


def render(
    result: ScanResult,
    account_label: str,
    generated_at: datetime,
    diff: ScanDiff | None = None,
    drift: list[Drift] | None = None,
) -> str:
    """Render the scan report as a single self-contained HTML document."""
    findings = result.register.attributed_findings
    unattributed = result.register.unattributed_findings
    reviews = result.review_candidates
    degraded = sorted({s for v in result.verdicts for s in v.unavailable})

    writers = [a for a in result.register.unsanctioned if a.reach.blast_radius.rank > 0]
    total_spend = sum(
        a.model.est_monthly_spend_usd for a in result.register.unsanctioned
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Custos scan — {_e(account_label)}</title>
<style>{_CSS}</style>
</head><body><main class="sheet">

<header class="masthead">
  <p class="gloss">Agent discovery scan</p>
  <h1>{_e(account_label)}</h1>
  <p class="headline">{_e(result.headline)}</p>
  <dl class="docctl">
    <div><dt>Generated</dt><dd>{_e(generated_at.strftime("%d %b %Y %H:%M UTC"))}</dd></div>
    <div><dt>Principals seen</dt><dd>{result.principals_seen}</dd></div>
    <div><dt>Agents found</dt><dd>{len(result.register.unsanctioned)}</dd></div>
    <div><dt>Write-capable</dt><dd>{len(writers)}</dd></div>
    <div><dt>For review</dt><dd>{len(reviews)}</dd></div>
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
{_review_section(reviews)}

<section>
  <h2>What this report does not claim</h2>
  <ul class="limits">{_limitations(result, degraded)}</ul>
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


def _review_section(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return ""
    return f"""
<section>
  <h2>For review</h2>
  <p class="lede">Workloads that resemble agents without meeting the bar. Most
  are batch jobs or build pipelines. They appear here rather than in the
  register because discovery is not permitted to decide this on its own.</p>
  {"".join(_review_row(v) for v in verdicts)}
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
.evidence{margin:.85rem 0 0;padding-left:1.1rem;font-size:.86rem;
  color:var(--ink-soft);max-width:44rem}
.evidence li{margin-bottom:.5rem}
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
