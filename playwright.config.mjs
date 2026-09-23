// Playwright config — smoke tests for the Vue panel.
// Backend: the project's own FastAPI app started in fake-TG mode (no network).
import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.TGDRIVE_TEST_PORT || 8712);

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // one shared sqlite db per run; keep serial
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `.venv\\Scripts\\uvicorn.exe app.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/admin/healthz`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      TGDRIVE_FAKE_TG: "1",
      TGDRIVE_SECRET: "test-secret-please-change-me-32chars",
      TGDRIVE_ADMIN_USERNAME: "admin",
      TGDRIVE_ADMIN_PASSWORD: "e2e-admin-pass",
      TGDRIVE_DATA_DIR: "data-e2e",
      TGDRIVE_PORT: String(PORT),
      PYTHONUNBUFFERED: "1",
    },
  },
});
