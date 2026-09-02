/**
 * The control plane client.
 *
 * Small on purpose. The API has three consumers and this is one of them; an
 * abstraction layer here would be indirection over four fetch calls.
 *
 * Two rules the rest of the console depends on:
 *
 * The token is passed in, never read from module scope. A credential that any
 * module can reach is a credential that ends up in a log line, and keeping it
 * on one object makes the places it travels countable.
 *
 * Errors carry the server's message. A console that says "request failed" when
 * the server said "this credential covers several accounts; pass ?account=" has
 * moved the work to the person reading it.
 */

import type {
  AccountsResponse,
  Agent,
  AuditResponse,
  DiffResponse,
  Health,
  RegisterResponse,
  ScansResponse,
  TransitionableStatus,
} from "./types";

export class ApiError extends Error {
  // Written out rather than as a constructor parameter property, because the
  // project builds with erasableSyntaxOnly — TypeScript syntax that emits
  // runtime code is disallowed, and a parameter property is exactly that.
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /** True when the credential is missing or wrong, so the caller can sign out. */
  get unauthenticated(): boolean {
    return this.status === 401;
  }

  /** True when the token covers several accounts and none was named. */
  get needsAccount(): boolean {
    return this.status === 400 && this.message.includes("?account=");
  }
}

export interface ClientOptions {
  /** Base URL. Empty means same origin, which is how it is served. */
  baseUrl?: string;
  token: string;
  fetch?: typeof globalThis.fetch;
}

export class Client {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly doFetch: typeof globalThis.fetch;

  constructor(options: ClientOptions) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.token = options.token;
    this.doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const response = await this.doFetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.token}`,
        ...(init.headers ?? {}),
      },
    });

    if (!response.ok) {
      throw new ApiError(response.status, await detail(response));
    }
    return (await response.json()) as T;
  }

  health(): Promise<Health> {
    return this.request<Health>("/healthz");
  }

  /** The accounts this credential covers. One for most tokens, many for a fleet. */
  accounts(): Promise<AccountsResponse> {
    return this.request<AccountsResponse>("/v1/accounts");
  }

  register(account?: string, unsanctionedOnly = false): Promise<RegisterResponse> {
    const query = new URLSearchParams();
    if (account) query.set("account", account);
    if (unsanctionedOnly) query.set("unsanctioned_only", "true");
    const suffix = query.toString();
    return this.request<RegisterResponse>(`/v1/register${suffix ? `?${suffix}` : ""}`);
  }

  /** What changed between the two most recent scans. */
  diff(account?: string): Promise<DiffResponse> {
    const query = account ? `?account=${encodeURIComponent(account)}` : "";
    return this.request<DiffResponse>(`/v1/diff${query}`);
  }

  scans(account?: string): Promise<ScansResponse> {
    const query = account ? `?account=${encodeURIComponent(account)}` : "";
    return this.request<ScansResponse>(`/v1/scans${query}`);
  }

  audit(agentId: string): Promise<AuditResponse> {
    return this.request<AuditResponse>(
      `/v1/agents/${encodeURIComponent(agentId)}/audit`,
    );
  }

  /**
   * Move an agent between discovered, pending_review and retired.
   *
   * Not a path to sanctioned — that is `grant` alone, and the server refuses
   * it here. `reason` is free text and goes in the audit trail: an agent
   * retired with no explanation is a decision nobody can review later.
   */
  setStatus(
    agentId: string,
    status: TransitionableStatus,
    operator: string,
    reason: string,
  ): Promise<Agent> {
    return this.request<Agent>(
      `/v1/agents/${encodeURIComponent(agentId)}/status`,
      { method: "POST", body: JSON.stringify({ status, operator, reason }) },
    );
  }

  /**
   * Grant imprimatur. The only call in this client that changes anything.
   *
   * `operator` is a human identity and is required by the server. Scope
   * defaults to what was observed when omitted, because an operator approving
   * an agent is approving what it was seen doing — widening that is a separate,
   * deliberate act.
   */
  grant(
    agentId: string,
    operator: string,
    scope?: { tools?: string[]; data?: string[] },
  ): Promise<Agent> {
    return this.request<Agent>(
      `/v1/agents/${encodeURIComponent(agentId)}/imprimatur`,
      {
        method: "POST",
        body: JSON.stringify({
          operator,
          approved_tools: scope?.tools ?? null,
          approved_data: scope?.data ?? null,
        }),
      },
    );
  }
}

/**
 * Pull the server's own message out of a failed response.
 *
 * FastAPI returns `{detail: ...}`, where detail is a string for our own errors
 * and an array of field problems for validation ones. Both are worth showing;
 * the alternative is a console that says "422" and leaves someone guessing.
 */
async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((item: { loc?: unknown[]; msg?: string }) =>
          `${(item.loc ?? []).join(".")}: ${item.msg ?? "invalid"}`,
        )
        .join("; ");
    }
  } catch {
    // A non-JSON body from a proxy or a gateway. The status is all we have.
  }
  return `HTTP ${response.status}`;
}
