/**
 * Who is using the console, for as long as the tab is open.
 *
 * Two values live here and they are not the same kind of thing.
 *
 * The **token** authenticates a machine — it is the same credential a collector
 * holds, and it names a set of accounts. It goes in sessionStorage, not
 * localStorage: a credential that survives the tab is a credential still
 * present on a shared laptop tomorrow, and the convenience is not worth it.
 *
 * The **operator** is a human identity, and it is not a credential at all. It
 * is the name recorded in the audit trail against every sanction, and SEC-17
 * requires a person rather than a machine. It is remembered for the session so
 * nobody retypes it forty times, and it is shown on the grant dialog every time
 * so nobody forgets whose name is going on the record.
 */

const TOKEN_KEY = "custos.token";
const OPERATOR_KEY = "custos.operator";
const ACCOUNT_KEY = "custos.account";

export interface Session {
  token: string;
  operator: string;
  account: string;
}

/** A storage that works even where sessionStorage is unavailable. */
export interface Store {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/**
 * Falls back to memory when storage throws.
 *
 * A private window, a browser configured to block site data, or an embedded
 * webview all make sessionStorage throw on access rather than return null. A
 * console that white-screens there is a console that fails for exactly the
 * security-conscious operator most likely to be using it.
 */
const fallback = new Map<string, string>();

const memoryStore: Store = {
  getItem: (key) => fallback.get(key) ?? null,
  setItem: (key, value) => void fallback.set(key, value),
  removeItem: (key) => void fallback.delete(key),
};

function safeStore(): Store {
  try {
    const probe = "__custos_probe__";
    globalThis.sessionStorage.setItem(probe, "1");
    globalThis.sessionStorage.removeItem(probe);
    return globalThis.sessionStorage;
  } catch {
    // One map for the page, not one per call. A fresh map each time would mean
    // nothing persists even within the tab, so an operator in a private window
    // would re-enter their token on every render — which reads as the console
    // being broken rather than as storage being blocked.
    return memoryStore;
  }
}

export function load(store: Store = safeStore()): Session {
  return {
    token: store.getItem(TOKEN_KEY) ?? "",
    operator: store.getItem(OPERATOR_KEY) ?? "",
    account: store.getItem(ACCOUNT_KEY) ?? "",
  };
}

export function save(session: Partial<Session>, store: Store = safeStore()): void {
  if (session.token !== undefined) store.setItem(TOKEN_KEY, session.token);
  if (session.operator !== undefined) store.setItem(OPERATOR_KEY, session.operator);
  if (session.account !== undefined) store.setItem(ACCOUNT_KEY, session.account);
}

/**
 * Forget everything.
 *
 * Clears the operator alongside the token. They are different kinds of value,
 * but someone signing out on a shared machine means both, and leaving a name
 * behind for the next person to sanction under is the worse mistake.
 */
export function clear(store: Store = safeStore()): void {
  store.removeItem(TOKEN_KEY);
  store.removeItem(OPERATOR_KEY);
  store.removeItem(ACCOUNT_KEY);
}

/** A token alone is enough to read. Sanctioning additionally needs a name. */
export function canRead(session: Session): boolean {
  return session.token.trim().length > 0;
}

export function canSanction(session: Session): boolean {
  return canRead(session) && session.operator.trim().length > 0;
}
