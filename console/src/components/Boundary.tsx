import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Catches a render crash and says so.
 *
 * React unmounts the whole tree when a render throws, which leaves a blank
 * page. In most products that reads as "broken". Here it reads as "no
 * findings" — the console's empty state is a quiet page, and an operator who
 * sees one concludes the account is clean.
 *
 * That is the specific failure this exists to prevent. A crash must never be
 * mistaken for an all-clear.
 *
 * A class because there is still no hook for componentDidCatch.
 */
interface State {
  message: string | null;
}

export class Boundary extends Component<{ children: ReactNode }, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: unknown): State {
    return { message: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The console is served from the control plane and has nowhere to report
    // to. The browser console is where whoever is looking will look.
    console.error("console crashed", error, info.componentStack);
  }

  render() {
    if (this.state.message === null) return this.props.children;

    return (
      <main className="sheet">
        <div className="notice" role="alert">
          <span className="tag">The console stopped</span>
          <p>
            Something failed while drawing this page, so what you are looking
            at is incomplete. <strong>Do not read this as an empty register.</strong>{" "}
            Reload, and if it happens again use <code>custos register</code> —
            the CLI and the API are unaffected by a fault in here.
          </p>
          <p className="mono">{this.state.message}</p>
        </div>
      </main>
    );
  }
}
