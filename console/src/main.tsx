import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { Boundary } from "./components/Boundary";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("no #root element; index.html and main.tsx disagree");

createRoot(root).render(
  <StrictMode>
    <Boundary>
      <App />
    </Boundary>
  </StrictMode>,
);
