import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { GatewayCandidate } from "../api/types";
import { questionKey } from "../questions";
import { Gateways } from "./Gateways";

function candidate(overrides: Partial<GatewayCandidate> = {}): GatewayCandidate {
  return {
    address: "10.0.7.40",
    egress: 54_400_000,
    ingress: 12_800_000,
    principals: ["arn:aws:iam::1:role/agent-via-gateway"],
    blind_principals: ["arn:aws:iam::1:role/agent-via-gateway"],
    question:
      "10.0.7.40 received 54.4MB from a workload that never reaches a model "
      + "provider we recognise, and returned 12.8MB — a ratio of 4.3:1. Is it a "
      + "model gateway?",
    region: "us-east-1",
    scan_id: 12,
    ...overrides,
  };
}

function show(props: Partial<Parameters<typeof Gateways>[0]> = {}) {
  const onDeclare = vi.fn();
  render(
    <Gateways
      candidates={[candidate()]}
      operator="ezra@custos.dev"
      busy={null}
      error={null}
      onDeclare={onDeclare}
      {...props}
    />,
  );
  return { onDeclare };
}

describe("gateway candidates", () => {
  it("says nothing when there is nothing to ask", () => {
    const { container } = render(
      <Gateways candidates={[]} operator="ezra" busy={null} error={null} onDeclare={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("carries the numbers somebody would check", () => {
    // It is a question put to a person who has to verify it, not a verdict
    // they would have to trust.
    show();
    expect(screen.getByText(/54.4MB/)).toBeInTheDocument();
    expect(screen.getByText(/4.3:1/)).toBeInTheDocument();
    expect(screen.getByText(/is it a model gateway\?/i)).toBeInTheDocument();
  });

  it("names the workloads that made it a question", () => {
    show();
    expect(screen.getByText(/agent-via-gateway/)).toBeInTheDocument();
  });

  it("records the answer with what the customer calls it", async () => {
    const { onDeclare } = show();
    await userEvent.type(screen.getByLabelText(/what this endpoint is called/i), "vllm");
    await userEvent.click(screen.getByRole("button", { name: /yes, it is ours/i }));

    expect(onDeclare).toHaveBeenCalledWith(
      expect.objectContaining({ address: "10.0.7.40" }),
      "vllm",
    );
  });

  it("offers no way to say no", () => {
    // A stored dismissal would be configuration that suppresses a question.
    // An operator who is sure can simply not act, and next week's scan asks
    // again with that week's numbers.
    show();
    expect(screen.queryByRole("button", { name: /no|dismiss|ignore/i })).toBeNull();
  });

  it("cannot be answered by a reader with no name", async () => {
    // The answer changes what counts as an agent, so it is recorded against a
    // person like every other decision that does.
    show({ operator: null });
    expect(screen.getByRole("button", { name: /yes, it is ours/i })).toBeDisabled();
    expect(screen.getByText(/enter your name above/i)).toBeInTheDocument();
  });

  it("says the answer takes effect on the next scan", () => {
    show();
    expect(screen.getByText(/takes effect on the next scan/i)).toBeInTheDocument();
  });

  it("shows a refusal rather than swallowing it", () => {
    show({ error: "not a valid network: '10.0.7.0/99'" });
    expect(screen.getByRole("alert")).toHaveTextContent(/not a valid network/);
  });
});


describe("which region a question is about", () => {
  it("names it, because answering declares the address for that region only", () => {
    render(
      <Gateways
        candidates={[candidate({ region: "eu-west-1" })]}
        operator="ezra@custos.dev"
        busy={null}
        error={null}
        onDeclare={vi.fn()}
      />,
    );
    expect(screen.getByText(/eu-west-1/)).toBeInTheDocument();
  });

  it("says nothing when the question carries no region", () => {
    // An older control plane recorded candidates without one, and a bare
    // separator with nothing after it is worse than no separator.
    render(
      <Gateways
        candidates={[candidate({ region: "" })]}
        operator="ezra@custos.dev"
        busy={null}
        error={null}
        onDeclare={vi.fn()}
      />,
    );
    expect(screen.queryByText(/·/)).toBeNull();
  });
});


describe("destinations that were not asked about", () => {
  it("says so when there is nothing else to say", () => {
    // The case this exists for. Nine ruled out and none asked looks, in a
    // console that renders only questions, exactly like an account with
    // nothing to find.
    render(
      <Gateways
        candidates={[]}
        declined={9}
        operator="ezra@custos.dev"
        busy={null}
        error={null}
        onDeclare={vi.fn()}
      />,
    );
    expect(screen.getByText(/9 other destinations had this shape/)).toBeInTheDocument();
    expect(screen.getByText(/log collector or a backup service/)).toBeInTheDocument();
  });

  it("reads as one when there is one", () => {
    render(
      <Gateways
        candidates={[]}
        declined={1}
        operator="ezra"
        busy={null}
        error={null}
        onDeclare={vi.fn()}
      />,
    );
    expect(screen.getByText(/1 other destination had this shape/)).toBeInTheDocument();
    expect(screen.getByText(/the workload reaching it talks to nothing else/))
      .toBeInTheDocument();
  });

  it("still says so beside the questions there are", () => {
    show({ declined: 3 });
    expect(screen.getByText(/3 other destinations had this shape/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /yes, it is ours/i })).toBeInTheDocument();
  });

  it("stays silent when nothing was ruled out", () => {
    const { container } = render(
      <Gateways candidates={[]} operator="ezra" busy={null} error={null} onDeclare={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});


describe("the same address in two regions", () => {
  // 10.0.7.40 in us-east-1 and 10.0.7.40 in eu-west-1 are two different hosts,
  // two separate questions, and two separate answers. Keying on the address
  // alone gave them the same React key.
  const both = [
    candidate({ region: "us-east-1" }),
    candidate({ region: "eu-west-1" }),
  ];

  it("asks both", () => {
    render(
      <Gateways
        candidates={both}
        operator="ezra@custos.dev"
        busy={null}
        error={null}
        onDeclare={vi.fn()}
      />,
    );
    expect(screen.getByText(/us-east-1/)).toBeInTheDocument();
    expect(screen.getByText(/eu-west-1/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /yes, it is ours/i })).toHaveLength(2);
  });

  it("marks only the one being answered as busy", () => {
    render(
      <Gateways
        candidates={both}
        operator="ezra@custos.dev"
        busy={questionKey(both[0])}
        error={null}
        onDeclare={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("button", { name: /recording/i })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /yes, it is ours/i })).toHaveLength(1);
  });
});
