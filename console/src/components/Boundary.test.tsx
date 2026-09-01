import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Boundary } from "./Boundary";

function Explode({ when }: { when: boolean }): React.ReactNode {
  if (when) throw new Error("classifier returned nonsense");
  return <p>the register</p>;
}

beforeEach(() => {
  // React logs the caught error itself. The boundary is what is under test.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the error boundary", () => {
  it("stays out of the way when nothing is wrong", () => {
    render(
      <Boundary>
        <Explode when={false} />
      </Boundary>,
    );
    expect(screen.getByText("the register")).toBeInTheDocument();
  });

  it("does not let a crash look like an empty register", () => {
    render(
      <Boundary>
        <Explode when={true} />
      </Boundary>,
    );

    // The whole point. A blank page in this console reads as "clean account",
    // which is the one wrong conclusion an operator could draw from a crash.
    expect(screen.getByRole("alert")).toHaveTextContent(
      /do not read this as an empty register/i,
    );
  });

  it("names the failure and where to go instead", () => {
    render(
      <Boundary>
        <Explode when={true} />
      </Boundary>,
    );

    expect(screen.getByText(/classifier returned nonsense/)).toBeInTheDocument();
    expect(screen.getByText(/custos register/)).toBeInTheDocument();
  });
});
