import { CHANGE_LABEL, CHANGE_TONE, type Change, type DiffResponse } from "../api/types";

/**
 * What moved since the last scan.
 *
 * This is the difference between a subscription and an audit. A report that
 * repeats last week's findings verbatim gets skimmed the second time and
 * deleted the third; one that opens with "three agents appeared this week, and
 * one of them can now write to your billing tables" gets read every time.
 *
 * It sits above the register rather than replacing it, because the register is
 * still the thing an operator acts on. This is what tells them where to look.
 */
export function Changes({ diff, onFocus }: { diff: DiffResponse; onFocus: (id: string) => void }) {
  // One scan is the normal state of a new account. It gets the server's
  // sentence and no empty list, because an empty list looks like nothing
  // changed rather than like nothing has been compared yet.
  if (diff.previous_scan_id === null) {
    return (
      <section className="changes">
        <p className="empty">{diff.headline}</p>
      </section>
    );
  }

  // The console's types are hand-written — the API serves no schema — so they
  // are a promise about the server rather than a proof. A response missing
  // this field is a bug somewhere, but it should degrade to a headline with no
  // list rather than take the page down and read as an empty account.
  const changes = diff.changes ?? [];

  return (
    <section className="changes">
      <h2>Since the last scan</h2>
      <p className="headline">{diff.headline}</p>
      {changes.length > 0 ? (
        <ul className="change-list">
          {changes.map((change) => (
            <li key={`${change.kind}-${change.agent_id}`} className={tone(change)}>
              <span className="kind">{CHANGE_LABEL[change.kind] ?? change.kind}</span>
              <button className="link" onClick={() => onFocus(change.agent_id)}>
                {short(change.principal)}
              </button>
              <span className="detail">{change.detail}</span>
              <span className="owner">{change.owner_team || "unattributed"}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function tone(change: Change): string {
  const named = CHANGE_TONE[change.kind];
  return named ? `change ${named}` : "change";
}

function short(principal: string): string {
  return principal.split("/").pop() ?? principal;
}
