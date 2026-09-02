import { useEffect, useRef, type RefObject } from "react";

/**
 * The behaviour a modal has to have, in one place.
 *
 * Both dialogs in this console ask for a decision that changes the register,
 * and both had grown their own copy of escape-to-close and body-scroll
 * locking. Neither trapped focus, which is the part that is easy to leave out
 * and hard to notice: Tab from the last control lands somewhere behind the
 * overlay, where a keyboard user can operate a page they cannot see and a
 * screen reader reads a register that is not the subject of the question.
 *
 * Returns a ref for the dialog element. `initialFocus` is what to focus on
 * open — the cancel control, in both cases, because a dialog that opens with
 * the irreversible action under the return key is one that gets past people
 * rather than in front of them.
 */
export function useDialog<T extends HTMLElement>(
  onCancel: () => void,
  initialFocus?: RefObject<HTMLElement | null>,
): RefObject<T | null> {
  const container = useRef<T | null>(null);

  useEffect(() => {
    // Where focus was before the dialog took it, so it can be given back. A
    // dialog that drops focus to the top of the document makes someone
    // navigating by keyboard start the page again for every decision.
    const opener = document.activeElement as HTMLElement | null;
    (initialFocus?.current ?? container.current)?.focus();
    return () => opener?.focus?.();
  }, [initialFocus]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = tabbable(container.current);
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      // Wrap at both ends. Without this the tab order continues into the page
      // behind the overlay, which is still there and still interactive.
      if (event.shiftKey && (active === first || !container.current?.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return container;
}

const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([disabled]), " +
  "select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

function tabbable(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    // A disabled confirm button is common here — the evidence gate and the
    // required reason both produce one — and it must not be a tab stop.
    //
    // Visibility is checked by attribute rather than by offsetParent. That
    // property is null for anything inside a display:none subtree, but it is
    // also null for a position:fixed element, which the overlay is — and it is
    // null for everything under jsdom, where these are tested. Attributes are
    // the same answer in both places.
    (el) =>
      !el.hasAttribute("disabled") &&
      !el.hasAttribute("hidden") &&
      el.getAttribute("aria-hidden") !== "true",
  );
}
