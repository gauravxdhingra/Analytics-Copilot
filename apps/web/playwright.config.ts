import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");

// Real browser, real Keycloak, real BFF and Postgres. Only the language model is scripted
// (COPILOT_MODEL=scripted), because the demo must run without a model key.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.spec.ts",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  use: { baseURL: "http://localhost:3000", trace: "retain-on-failure" },
  // E2E_STACK=compose: the stack is already running in containers (make demo), start nothing.
  webServer: process.env.E2E_STACK === "compose" ? undefined : [
    {
      command: "uv run uvicorn bff.main:app --port 8000",
      cwd: root,
      url: "http://localhost:8000/healthz",
      reuseExistingServer: true,
      timeout: 60_000,
      env: { ...process.env, COPILOT_MODEL: "scripted" } as Record<string, string>,
    },
    {
      command: "npm run dev",
      cwd: import.meta.dirname,
      url: "http://localhost:3000",
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
});
