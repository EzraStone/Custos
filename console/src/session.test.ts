import { beforeEach, describe, expect, it } from "vitest";

import { canRead, canSanction, clear, load, save, type Store } from "./session";

function memoryStore(): Store {
  const map = new Map<string, string>();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  };
}

describe("session", () => {
  let store: Store;
  beforeEach(() => {
    store = memoryStore();
  });

  it("round-trips a session", () => {
    save({ token: "tok", operator: "ezra@custos.dev", account: "447120043318" }, store);
    expect(load(store)).toEqual({
      token: "tok",
      operator: "ezra@custos.dev",
      account: "447120043318",
    });
  });

  it("starts empty", () => {
    expect(load(store)).toEqual({ token: "", operator: "", account: "" });
  });

  it("updates one field without clearing the others", () => {
    save({ token: "tok", operator: "ezra" }, store);
    save({ account: "111111111111" }, store);
    expect(load(store).operator).toBe("ezra");
  });

  // Someone signing out on a shared machine means both. Leaving a name behind
  // for the next person to sanction under is the worse mistake.
  it("clears the operator alongside the token", () => {
    save({ token: "tok", operator: "ezra", account: "1" }, store);
    clear(store);
    expect(load(store)).toEqual({ token: "", operator: "", account: "" });
  });

  it("allows reading with a token alone", () => {
    expect(canRead({ token: "tok", operator: "", account: "" })).toBe(true);
  });

  // SEC-17 needs a person. A token is a machine.
  it("refuses to sanction without an operator identity", () => {
    expect(canSanction({ token: "tok", operator: "", account: "" })).toBe(false);
    expect(canSanction({ token: "tok", operator: "   ", account: "" })).toBe(false);
    expect(canSanction({ token: "tok", operator: "ezra", account: "" })).toBe(true);
  });

  it("refuses to sanction without a token", () => {
    expect(canSanction({ token: "", operator: "ezra", account: "" })).toBe(false);
  });
});

// A private window, blocked site data, or an embedded webview all make
// sessionStorage throw on access. A console that white-screens there fails for
// exactly the security-conscious operator most likely to be using it.
describe("storage that throws", () => {
  it("falls back to memory rather than failing", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      get() {
        throw new DOMException("denied");
      },
    });

    try {
      expect(() => load()).not.toThrow();
      save({ token: "tok", operator: "ezra" });

      // Persists for the page, so an operator in a private window does not
      // re-enter their token on every render.
      expect(load().token).toBe("tok");
      expect(load().operator).toBe("ezra");

      clear();
      expect(load().token).toBe("");
    } finally {
      if (original) Object.defineProperty(globalThis, "sessionStorage", original);
    }
  });
});
