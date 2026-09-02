import type { Scan } from "../api/types";

/**
 * The last few scans, and whether they are getting better or worse.
 *
 * The console already fetches this to read one field off the newest entry.
 * Showing the rest answers a question no single scan can: coverage and scope
 * are properties of an account's configuration, and the useful thing about
 * them is the direction they are moving. A customer who tagged their ENIs
 * last week should be able to see that it worked.
 *
 * Collapsed by default. It is context, not the queue.
 */
export function Scans({ scans }: { scans: Scan[] }) {
  if (scans.length < 2) return null;

  return (
    <details className="scans">
      <summary>Recent scans</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Agents</th>
            <th scope="col">Coverage</th>
            <th scope="col">Scope named</th>
          </tr>
        </thead>
        <tbody>
          {scans.slice(0, 10).map((scan) => (
            <tr key={scan.id}>
              <td className="mono">{when(scan.started_at)}</td>
              <td>{scan.agents_found}</td>
              <td className={scan.coverage < 0.95 ? "warn" : ""}>
                {percent(scan.coverage)}
                {scan.truncated ? " (truncated)" : ""}
              </td>
              <td className={low(scan) ? "warn" : ""}>{scope(scan)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function low(scan: Scan): boolean {
  return (
    scan.scope_readable !== undefined
    && (scan.scope_total ?? 0) > 0
    && scan.scope_readable < 0.5
  );
}

function scope(scan: Scan): string {
  // A dash, not 0%. A scan that reached nothing internal has no unreadable
  // scope, and a zero in this column would read as a problem. The same is
  // true of a control plane too old to report the field at all.
  if (scan.scope_readable === undefined || !scan.scope_total) return "—";
  return `${percent(scan.scope_readable)} (${scan.scope_named} of ${scan.scope_total})`;
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function when(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toISOString().slice(0, 16).replace("T", " ");
}
