import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { useDialog } from "./useDialog";

function Harness({
  onCancel,
  disabledConfirm = false,
}: {
  onCancel: () => void;
  disabledConfirm?: boolean;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const ref = useDialog<HTMLDivElement>(onCancel, cancelRef);
  return (
    <div ref={ref} role="dialog" aria-modal="true">
      <button ref={cancelRef}>Cancel</button>
      <input aria-label="reason" />
      <button disabled={disabledConfirm}>Confirm</button>
    </div>
  );
}

function Page({ open, disabledConfirm = false }: { open: boolean; disabledConfirm?: boolean }) {
  return (
    <>
      <button>Open</button>
      {open ? <Harness onCancel={() => {}} disabledConfirm={disabledConfirm} /> : null}
      <button>Behind</button>
    </>
  );
}

describe("modal behaviour", () => {
  it("focuses what the dialog asked it to", () => {
    render(<Page open />);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("keeps tab inside the dialog", async () => {
    // Without this, Tab from the last control lands behind the overlay, where
    // a keyboard user can operate a page they cannot see.
    render(<Page open />);

    await userEvent.tab();
    expect(screen.getByLabelText("reason")).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("wraps backwards too", async () => {
    render(<Page open />);
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
  });

  it("skips a disabled control", async () => {
    // Both dialogs routinely have one: the evidence gate and the required
    // reason each produce a disabled confirm.
    render(<Page open disabledConfirm />);
    await userEvent.tab();
    expect(screen.getByLabelText("reason")).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("gives focus back when it closes", () => {
    // A dialog that drops focus to the top of the document makes someone
    // navigating by keyboard start the page again for every decision.
    const { rerender } = render(<Page open={false} />);
    screen.getByRole("button", { name: "Open" }).focus();

    rerender(<Page open />);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();

    rerender(<Page open={false} />);
    expect(screen.getByRole("button", { name: "Open" })).toHaveFocus();
  });

  it("closes on escape", async () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalled();
  });

  it("locks and restores the page scroll", () => {
    const { unmount } = render(<Harness onCancel={() => {}} />);
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
