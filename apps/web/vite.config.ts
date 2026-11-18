/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks to one origin. The dev server forwards /auth and /api to the BFF, so
// the session cookie is first-party and no CORS is needed (and none is enabled on the BFF).
const bff = process.env.BFF_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    strictPort: true,
    proxy: { "/auth": bff, "/api": bff },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["./tests/setup.ts"],
    globals: true,
  },
});
