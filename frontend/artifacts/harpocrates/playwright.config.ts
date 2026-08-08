import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Repo layout: this app is at <repo>/frontend/artifacts/harpocrates.
// The FastAPI backend lives at <repo>/backend and the shared Python venv at
// <repo>/.venv (see AGENT_RULES.md §7.6 for how the backend is run locally).
const repoRoot = path.resolve(__dirname, "../../..");
const backendDir = path.join(repoRoot, "backend");
const pythonBin = path.join(repoRoot, ".venv", "bin", "python");

const FRONTEND_PORT = 5173;
const BACKEND_PORT = 8000;

/**
 * End-to-end test harness for the No-Compression encode/decode flow.
 *
 * Launches both required servers:
 *   - the FastAPI backend (`uvicorn app.main:app`) on :8000
 *   - the Vite dev server for this app on :5173 (needs PORT + BASE_PATH env)
 *
 * The Vite dev server proxies /api -> http://localhost:8000 (vite.config.ts),
 * so the browser talks to the real backend same-origin.
 *
 * Run with:  pnpm install && npx playwright install chromium && pnpm test:e2e
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${pythonBin} -m uvicorn app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: backendDir,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `pnpm exec vite --config vite.config.ts --host 127.0.0.1`,
      cwd: __dirname,
      env: {
        ...process.env,
        PORT: String(FRONTEND_PORT),
        BASE_PATH: "/",
      },
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
