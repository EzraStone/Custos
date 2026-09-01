import { useState } from "react";

/**
 * Getting a token and a name into the session.
 *
 * Two fields, and the copy distinguishes them because they are different kinds
 * of thing. The token is a credential the control plane issued; the name is a
 * human identity that goes in an audit trail. Someone who types a service
 * account name into the second field has not been stopped by anything, but they
 * should at least have been told what it is for.
 */

export interface SignInProps {
  initialOperator?: string;
  onSubmit: (token: string, operator: string) => void;
  error?: string | null;
  busy?: boolean;
}

export function SignIn({ initialOperator = "", onSubmit, error, busy }: SignInProps) {
  const [token, setToken] = useState("");
  const [operator, setOperator] = useState(initialOperator);

  return (
    <form
      className="signin"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(token.trim(), operator.trim());
      }}
    >
      <h2>Sign in</h2>
      <p className="lede">
        The console reads the register and grants imprimatur. It cannot start a
        scan, change a policy, or reach anything the control plane does not
        already hold.
      </p>

      <div className="field">
        <label htmlFor="token">Control plane token</label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="the token issued with your account"
        />
        <span className="hint">
          Held for this tab only. Closing it signs you out.
        </span>
      </div>

      <div className="field">
        <label htmlFor="operator">Your name</label>
        <input
          id="operator"
          type="text"
          autoComplete="name"
          value={operator}
          onChange={(event) => setOperator(event.target.value)}
          placeholder="ezra@example.com"
        />
        <span className="hint">
          Recorded in the audit trail against everything you sanction. Not a
          credential, and not optional — the register needs a person.
        </span>
      </div>

      {error ? (
        <div className="notice" role="alert">
          <span className="tag">Could not sign in</span>
          <p>{error}</p>
        </div>
      ) : null}

      <div className="actions">
        <button className="primary" type="submit" disabled={!token.trim() || busy}>
          {busy ? "Checking…" : "Continue"}
        </button>
        {!operator.trim() ? (
          <span className="hint">
            Without a name you can read the register but not sanction anything.
          </span>
        ) : null}
      </div>
    </form>
  );
}
