import { byUrgency, type FleetRow } from "../api/types";

/**
 * Which account are we looking at.
 *
 * A fleet token covers many, and everything downstream — the register, the
 * scan history, the coverage warning — is scoped to exactly one, so the choice
 * has to be made before any of it means anything.
 *
 * It used to be a list of twelve-digit numbers, which is nothing to choose by.
 * A customer in the target profile runs five to fifty accounts and the person
 * opening this has an afternoon; what they need is which account has
 * unsanctioned agents that can destroy things and which has never been
 * scanned.
 *
 * Ordered worst first, with unscanned accounts above everything. "We have
 * never looked here" outranks any finding — a finding is something somebody
 * knows, and an unscanned account is something nobody does.
 */
export function AccountPicker({
  fleet,
  accounts,
  current,
  onChoose,
}: {
  fleet: FleetRow[];
  /** Fallback when the control plane is too old to answer /v1/fleet. */
  accounts: string[];
  current: string;
  onChoose: (account: string) => void;
}) {
  const rows = [...fleet].sort(byUrgency);

  return (
    <section className="picker">
      <h2>Choose an account</h2>
      <p className="lede">
        This credential covers {rows.length || accounts.length} accounts. The
        register is scoped to one at a time.
      </p>

      {rows.length === 0 ? (
        <ul className="accounts">
          {accounts.map((account) => (
            <li key={account}>
              <button
                className={account === current ? "account current" : "account"}
                aria-current={account === current ? "true" : undefined}
                onClick={() => onChoose(account)}
              >
                {account}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="accounts">
          {rows.map((row) => (
            <li key={row.account_id}>
              <button
                className={row.account_id === current ? "account current" : "account"}
                aria-current={row.account_id === current ? "true" : undefined}
                onClick={() => onChoose(row.account_id)}
              >
                <span className="id">{row.account_id}</span>
                <span className="summary">{summary(row)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * One line about an account, in the order somebody would act on it.
 *
 * Never scanned comes first and alone: nothing else about that account is
 * known, and listing zeroes beside it would read as "we looked and it is
 * clean".
 */
function summary(row: FleetRow): string {
  if (row.last_scan === null) return "never scanned";

  const parts: string[] = [];
  if (row.destructive > 0) {
    parts.push(`${row.destructive} that can destroy`);
  }
  parts.push(
    row.unsanctioned === 0
      ? "nothing unsanctioned"
      : `${row.unsanctioned} unsanctioned`,
  );
  if (row.gateway_questions > 0) {
    parts.push(`${row.gateway_questions} gateway question${row.gateway_questions === 1 ? "" : "s"}`);
  }
  if (row.coverage !== null && row.coverage < 0.95) {
    parts.push(`${Math.round(row.coverage * 100)}% coverage`);
  }
  parts.push(`scanned ${day(row.last_scan)}`);
  return parts.join(" · ");
}

function day(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toISOString().slice(0, 10);
}
