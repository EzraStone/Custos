import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest does not unmount between tests on its own, so a component left in the
// document makes the next test's query ambiguous — a failure that reads as a
// broken assertion rather than as leaked state.
afterEach(cleanup);
