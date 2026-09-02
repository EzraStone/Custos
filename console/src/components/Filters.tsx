import { RADIUS_LABEL, type BlastRadius } from "../api/types";
import { NO_FILTERS, isFiltered, type FilterState } from "../filters";

/**
 * Two controls, chosen because they match the two questions an operator
 * actually arrives with: "show me the dangerous ones" and "where is the thing
 * I am looking for".
 *
 * The logic behind them is in ../filters.ts, where it can be tested without
 * rendering.
 */
const RADII: (BlastRadius | "all")[] = ["all", "destructive", "write", "read"];

// Only the statuses an agent in the register can actually be in. Listing every
// value the server might send would put chips on the page that match nothing.
const STATUSES: { value: string; label: string }[] = [
  { value: "all", label: "Any status" },
  { value: "discovered", label: "Discovered" },
  { value: "pending_review", label: "For review" },
  { value: "sanctioned", label: "Sanctioned" },
  { value: "retired", label: "Retired" },
];

export function Filters({
  value,
  onChange,
  showing,
  total,
}: {
  value: FilterState;
  onChange: (next: FilterState) => void;
  showing: number;
  total: number;
}) {
  return (
    <div className="filters">
      <div className="radios" role="group" aria-label="Filter by blast radius">
        {RADII.map((radius) => (
          <button
            key={radius}
            className={value.radius === radius ? "chip on" : "chip"}
            aria-pressed={value.radius === radius}
            onClick={() => onChange({ ...value, radius })}
          >
            {radius === "all" ? "Any radius" : RADIUS_LABEL[radius]}
          </button>
        ))}
        <button
          className={value.unattributedOnly ? "chip on" : "chip"}
          aria-pressed={value.unattributedOnly}
          onClick={() => onChange({ ...value, unattributedOnly: !value.unattributedOnly })}
        >
          Unattributed
        </button>
      </div>

      <div className="radios" role="group" aria-label="Filter by status">
        {STATUSES.map((status) => (
          <button
            key={status.value}
            className={value.status === status.value ? "chip on" : "chip"}
            aria-pressed={value.status === status.value}
            onClick={() => onChange({ ...value, status: status.value })}
          >
            {status.label}
          </button>
        ))}
      </div>

      <label className="search">
        <span className="visually-hidden">Search the register</span>
        <input
          type="search"
          value={value.query}
          placeholder="role, team, or something it reaches"
          onChange={(event) => onChange({ ...value, query: event.target.value })}
        />
      </label>

      {isFiltered(value) ? (
        <p className="filter-count">
          {/*
            The total is always shown next to the count. A filtered view that
            says "2 agents" and nothing else is how someone concludes an
            account is nearly clean while looking at a third of it.
          */}
          Showing {showing} of {total}.{" "}
          <button className="link" onClick={() => onChange(NO_FILTERS)}>
            Clear
          </button>
        </p>
      ) : null}
    </div>
  );
}
