import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, Client } from "./api/client";
import { byConsequence, type Agent, type Health, type Scan } from "./api/types";
import { AccountPicker } from "./components/AccountPicker";
import { Finding } from "./components/Finding";
import { GrantDialog } from "./components/GrantDialog";
import { SignIn } from "./components/SignIn";
import * as session from "./session";

type View = "unsanctioned" | "all";

export function App() {
  const [auth, setAuth] = useState(() => session.load());
  const [health, setHealth] = useState<Health | null>(null);
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [scans, setScans] = useState<Scan[]>([]);
  const [accounts, setAccounts] = useState<string[] | null>(null);
  const [view, setView] = useState<View>("unsanctioned");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [granting, setGranting] = useState<Agent | null>(null);
  const [grantError, setGrantError] = useState<string | null>(null);
  const [grantBusy, setGrantBusy] = useState(false);

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

      const [registry, history, status] = await Promise.all([
        client.register(account, view === "unsanctioned"),
        client.scans(account).catch(() => ({ scans: [] })),
        client.health().catch(() => null),
      ]);
      if (mine !== ticket.current) return;
      setAgents([...registry.agents].sort(byConsequence));
      setScans("scans" in history ? history.scans : []);
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

  // load() raises a spinner before awaiting the API. Fetching from the control
  // plane is the external system this effect exists to synchronise with, which
  // is the case the rule's own guidance carves out; the state it objects to is
  // the spinner, and there is nowhere earlier to raise it.
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load();
  }, [load]);

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

      {agents === null ? (
        <p className="empty">{loading ? "Loading…" : "Nothing loaded."}</p>
      ) : agents.length === 0 ? (
        <p className="empty">
          {view === "unsanctioned"
            ? "No unsanctioned agents. Check the coverage of the last scan before concluding the account is clean."
            : "No agents in this account yet."}
        </p>
      ) : (
        agents.map((agent) => (
          <Finding
            key={agent.id}
            agent={agent}
            operator={session.canSanction(auth) ? auth.operator : null}
            spendIsEstimate={spendIsEstimate}
            busy={granting?.id === agent.id && grantBusy}
            onGrant={(target) => {
              setGrantError(null);
              setGranting(target);
            }}
          />
        ))
      )}

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
