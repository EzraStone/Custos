/**
 * Which account are we looking at.
 *
 * A fleet token covers many. Everything downstream — the register, the scan
 * history, the coverage warning — is scoped to exactly one, so the choice has
 * to be made before any of it means anything.
 *
 * It is rendered as a list rather than a dropdown deliberately. The number of
 * accounts a token covers is itself information the operator should see: a
 * customer who believes they run four accounts and is shown eleven has learned
 * something before clicking anything.
 */
export function AccountPicker({
  accounts,
  current,
  onChoose,
}: {
  accounts: string[];
  current: string;
  onChoose: (account: string) => void;
}) {
  return (
    <section className="picker">
      <h2>Choose an account</h2>
      <p className="lede">
        This credential covers {accounts.length} accounts. The register is
        scoped to one at a time.
      </p>
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
    </section>
  );
}
