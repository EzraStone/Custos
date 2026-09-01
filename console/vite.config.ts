import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In production the console is served by the control plane, so every request
// the client makes is same-origin and relative. `npm run dev` puts a Vite
// server in front instead, which would answer /v1/register with index.html and
// leave the console showing a parse error rather than a register.
//
// The proxy restores the production shape: same origin, relative paths, no
// CORS, no base URL to configure. CUSTOS_API overrides the target for a
// control plane on another port.
const target = process.env.CUSTOS_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": { target, changeOrigin: true },
      "/healthz": { target, changeOrigin: true },
    },
  },
});
