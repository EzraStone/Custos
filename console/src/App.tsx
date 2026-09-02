import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, Client } from "./api/client";
import {
  byConsequence,
  type Agent,
  type Health,
  type DiffResponse,
  type Scan,
  type TransitionableStatus,
} from "./api/types";
import { AccountPicker } from "./components/AccountPicker";
import { Changes } from "./components/Changes";
import { Filters } from "./components/Filters";
import { Finding } from "./components/Finding";
import { GrantDialog } from "./components/GrantDialog";
import { Scans } from "./components/Scans";
import { SignIn } from "./components/SignIn";
import { StatusDialog } from "./components/StatusDialog";
import { NO_FILTERS, matches, type FilterState } from "./filters";
import * as session from "./session";

type View = "unsanctioned" | "all";

export function App() {
  const [auth, setAuth] = useState(() => session.load());
  const [health, setHealth] = useState<Health | null>(null);
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [scans, setScans] = useState<Scan[]>([]);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [accounts, setAccounts] = useState<string[] | null>(null);
  const [view, setView] = useState<View>("unsanctioned");
  const [filters, setFilters] = useState<FilterState>(NO_FILTERS);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [granting, setGranting] = useState<Agent | null>(null);
  const [grantError, setGrantError] = useState<string | null>(null);
  const [grantBusy, setGrantBusy] = useState(false);
  const [changing, setChanging] = useState<{ agent: Agent; to: TransitionableStatus } | null>(
    null,
  );
  const [changeError, setChangeError] = useState<string | null>(null);
  const [changeBusy, setChangeBusy] = useState(false);

  // Every load takes a ticket, and a response is only allowed to write state
  // if its ticket is still the current one. Toggling the view twice in quick
  // succession issues two requests; without this the slower one wins whenever
  // it happens to land second, and the list shows sanctioned agents under a
  // heading that says unsanctioned. That is not a cosmetic race in a review
  // queue — it is the wrong set of agents in front of someone deciding what
  // to approve.
  const ticket = useRef(0);

  const client = useMemo(
    () => (auth.token ? new Client({ token: auth.token }) : null),
    [auth.token],
  );

  const signOut = useCallback(() => {
    session.clear();
    setAuth({ token: "", operator: "", account: "" });
    setAgents(null);
    setScans([]);
    setDiff(null);
    setAccounts(null);
    setHealth(null);
  }, []);

  const load = useCallback(async () => {
    if (!client) return;
    const mine = ++ticket.current;
    setLoading(true);
    setError(null);

    try {
      // Which accounts the credential covers is asked first, because on a fleet
      // token every other call needs an account and would 400 without one.
      const covered = (await client.accounts()).accounts;
      if (mine !== ticket.current) return;
      setAccounts(covered);

      // One account needs no choosing. Several, with none chosen, means the
      // picker renders instead of a register — there is nothing to show yet.
      const account = auth.account || (covered.length === 1 ? covered[0] : "");
      if (!account) {
        setAgents(null);
        return;
      }

      const [registry, history, status, changes] = await Promise.all([
        client.register(account, view === "unsanctioned"),
        client.scans(account).catch(() => ({ scans: [] })),
        client.health().catch(() => null),
        // The register is the point; the diff is context. A control plane too
        // old to have /v1/diff should still show findings rather than an
        // error, so this one failure is swallowed.
        client.diff(account).catch(() => null),
      ]);
      if (mine !== ticket.current) return;
      setAgents([...registry.agents].sort(byConsequence));
      setScans("scans" in history ? history.scans : []);
      setDiff(changes);
      if (status) setHealth(status);
    } catch (caught) {
      if (mine !== ticket.current) return;
      const failure = caught as ApiError;
      // An expired or revoked credential signs the session out rather than
      // leaving a console that shows an error on every action.
      if (failure.unauthenticated) {
        signOut();
        setError("That credential is no longer valid. Sign in again.");
      } else {
        setError(failure.message);
      }
      setAgents(null);
    } finally {
      // A superseded load must not clear the spinner: the request that
      // superseded it is still running.
      if (mine === ticket.current) setLoading(false);
    }
  }, [client, auth.account, view, signOut]);

  // Passed to every card, so it has to be stable: a new function each render
  // would hand each History a new prop and defeat its fetch-once behaviour.
  const loadHistory = useCallback(
    async (agentId: string) => {
      if (!client) return [];
      return (await client.audit(agentId)).entries;
    },
    [client],
  );

  const loadDrift = useCallback(
    async (agentId: string) => {
      if (!client) throw new Error("not signed in");
      return client.drift(agentId);
    },
    [client],
  );

  // load() raises a spinner before awaiting the API. Fetching from the control
  // plane is the external system this effect exists to synchronise with, which
  // is the case the rule's own guidance carves out; the state it objects to is
  // the spinner, and there is nowhere earlier to raise it.
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

  async function confirmTransition(reason: string) {
    if (!client || !changing) return;
    setChangeBusy(true);
    setChangeError(null);

    try {
      await client.setStatus(changing.agent.id, changing.to, auth.operator, reason);
      setChanging(null);
      await load();
    } catch (caught) {
      // A refused transition is the server enforcing SEC-17's state machine,
      // not a fault. It stays on screen with the server's own words.
      setChangeError((caught as ApiError).message);
    } finally {
      setChangeBusy(false);
    }
  }

  async function confirmGrant() {
    if (!client || !granting) return;
    setGrantBusy(true);
    setGrantError(null);

    try {
      await client.grant(granting.id, auth.operator);
      setGranting(null);
      await load();
    } catch (caught) {
      setGrantError((caught as ApiError).message);
    } finally {
      setGrantBusy(false);
    }
  }

  if (!session.canRead(auth)) {
    return (
      <main className="sheet">
        <Masthead />
        <SignIn
          initialOperator={auth.operator}
          error={error}
          onSubmit={(token, operator) => {
            session.save({ token, operator });
            setAuth((current) => ({ ...current, token, operator }));
            setError(null);
          }}
        />
      </main>
    );
  }

  const spendIsEstimate = health?.prices_revision !== undefined
    && health.prices_revision.startsWith("unverified");

  const shown = agents === null ? null : agents.filter((a) => matches(a, filters));

  const fleet = (accounts?.length ?? 0) > 1;
  const chooseAccount = (account: string) => {
    session.save({ account });
    setAuth((current) => ({ ...current, account }));
  };

  // A fleet credential with no account chosen has nothing to show: every route
  // below is scoped to one account. The picker is the page, not a banner on
  // top of an empty register.
  if (fleet && !auth.account) {
    return (
      <main className="sheet">
        <Masthead>
          <button className="link" onClick={signOut}>
            Sign out
          </button>
        </Masthead>
        {error ? (
          <div className="notice" role="alert">
            <span className="tag">Could not load the register</span>
            <p>{error}</p>
          </div>
        ) : null}
        <AccountPicker
          accounts={accounts ?? []}
          current={auth.account}
          onChoose={chooseAccount}
        />
      </main>
    );
  }

  return (
    <main className="sheet">
      <Masthead>
        <button className="link" onClick={signOut}>
          Sign out
        </button>
      </Masthead>

      <div className="meta-row">
        <span>{auth.operator || "no name set — reading only"}</span>
        {fleet ? (
          <span>
            account {auth.account}{" "}
            <button className="link" onClick={() => chooseAccount("")}>
              switch
            </button>
          </span>
        ) : null}
        {health ? <span>catalogue {health.catalogue_revision}</span> : null}
        {scans[0] ? <span>last scan {formatDate(scans[0].started_at)}</span> : null}
        {scans[0]?.truncated ? (
          <span className="warn">last scan was truncated</span>
        ) : null}
        <span className="spacer" />
        <button className="link" onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? (
        <div className="notice" role="alert">
          <span className="tag">Could not load the register</span>
          <p>{error}</p>
        </div>
      ) : null}

      {/*
        Everything else on this page changes silently for a screen reader: the
        list is replaced, the counts move, and nothing says so. Refresh in
        particular gives no feedback at all without this — the spinner is on a
        button the reader has already left.

        Polite rather than assertive: this is a status, and interrupting
        someone mid-sentence to say a list finished loading is worse than
        telling them a moment later.
      */}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement(loading, error, shown, agents, view)}
      </p>

      {/*
        Two different warnings, because they mean different things. Low
        coverage says findings may be missing. An unreadable scope says the
        findings are all here and the approval decision on each one is a guess.
      */}
      {scans[0]?.scope_readable !== undefined
        && scans[0].scope_readable < 0.5
        && (scans[0].scope_total ?? 0) > 0 ? (
        <div className="notice">
          <span className="tag">Scope is mostly addresses</span>
          <p>
            {scans[0].scope_named} of {scans[0].scope_total} internal
            destinations could be named. The findings below are unaffected, but
            approving one means approving a list of IP addresses. Tagging the
            ENIs behind those services is what makes this readable.
          </p>
        </div>
      ) : null}

      {scans[0] && scans[0].coverage < 0.95 ? (
        <div className="notice">
          <span className="tag">Incomplete coverage</span>
          <p>
            The last scan parsed {Math.round(scans[0].coverage * 100)}% of the
            flow log lines it read. Agents in the traffic it could not read do
            not appear below, so an empty list means less here than it usually
            would.
          </p>
        </div>
      ) : null}

      {diff ? (
        <Changes
          diff={diff}
          onFocus={(id) => {
            // Scroll rather than filter. The operator asked where to look, not
            // to be shown only that one — the surrounding findings are how they
            // judge whether this change matters.
            document
              .querySelector(`[data-testid="finding-${id}"]`)
              ?.scrollIntoView({ behavior: "smooth", block: "center" });
          }}
        />
      ) : null}

      <Scans scans={scans} />

      <h2>{view === "unsanctioned" ? "Unsanctioned agents" : "The register"}</h2>
      <p className="lede">
        {view === "unsanctioned"
          ? "Workloads making autonomous model calls that nobody has registered. Ordered by what each one could destroy, not by how confident we are that it exists."
          : "Every agent in this account, sanctioned and not."}
        {" "}
        <button
          className="link"
          onClick={() => setView(view === "unsanctioned" ? "all" : "unsanctioned")}
        >
          {view === "unsanctioned" ? "Show all" : "Show only unsanctioned"}
        </button>
      </p>

      {agents !== null && agents.length > 0 ? (
        <Filters
          value={filters}
          onChange={setFilters}
          showing={shown?.length ?? 0}
          total={agents.length}
        />
      ) : null}

      {agents === null || shown === null ? (
        <p className="empty">{loading ? "Loading…" : "Nothing loaded."}</p>
      ) : agents.length === 0 ? (
        <p className="empty">
          {view === "unsanctioned"
            ? "No unsanctioned agents. Check the coverage of the last scan before concluding the account is clean."
            : "No agents in this account yet."}
        </p>
      ) : shown.length === 0 ? (
        <p className="empty">
          {/*
            Deliberately different wording from an empty account. "No agents
            match" cannot be misread as "this account is clean", which is the
            one conclusion a filtered view must never invite.
          */}
          No agents match these filters. {agents.length} are hidden.{" "}
          <button className="link" onClick={() => setFilters(NO_FILTERS)}>
            Clear them
          </button>
        </p>
      ) : (
        shown.map((agent) => (
          <Finding
            key={agent.id}
            agent={agent}
            operator={session.canSanction(auth) ? auth.operator : null}
            loadHistory={loadHistory}
            loadDrift={loadDrift}
            spendIsEstimate={spendIsEstimate}
            busy={granting?.id === agent.id && grantBusy}
            onGrant={(target) => {
              setGrantError(null);
              setGranting(target);
            }}
            onTransition={(target, to) => {
              setChangeError(null);
              setChanging({ agent: target, to });
            }}
          />
        ))
      )}

      {changing ? (
        <StatusDialog
          agent={changing.agent}
          to={changing.to}
          operator={auth.operator}
          busy={changeBusy}
          error={changeError}
          onConfirm={(reason) => void confirmTransition(reason)}
          onCancel={() => {
            setChanging(null);
            setChangeError(null);
          }}
        />
      ) : null}

      {granting ? (
        <GrantDialog
          agent={granting}
          operator={auth.operator}
          busy={grantBusy}
          error={grantError}
          onConfirm={() => void confirmGrant()}
          onCancel={() => {
            setGranting(null);
            setGrantError(null);
          }}
        />
      ) : null}
    </main>
  );
}

/**
 * What a screen reader is told after a load settles.
 *
 * Counts, not "done". "Twelve unsanctioned agents" is the thing a person came
 * for; "loaded" makes them go and find out.
 */
function announcement(
  loading: boolean,
  error: string | null,
  shown: Agent[] | null,
  agents: Agent[] | null,
  view: View,
): string {
  if (loading) return "Loading the register.";
  if (error) return `Could not load the register. ${error}`;
  if (shown === null || agents === null) return "";

  const noun = view === "unsanctioned" ? "unsanctioned agent" : "agent";
  const plural = shown.length === 1 ? "" : "s";
  if (shown.length === agents.length) {
    return `${shown.length} ${noun}${plural}.`;
  }
  // The total, always. A filtered count read on its own is how someone
  // concludes an account is nearly clean while hearing a third of it.
  return `${shown.length} of ${agents.length} ${noun}${plural} shown; the rest are filtered out.`;
}

function Masthead({ children }: { children?: React.ReactNode }) {
  return (
    <header className="masthead">
      <div>
        <span className="gloss">Agent register</span>
        <h1 className="wordmark">Custos</h1>
      </div>
      <span className="spacer" />
      {children}
    </header>
  );
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(0, 16).replace("T", " ");
}
