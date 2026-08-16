// Gate 6 runs on headless WebKit at two viewports: WebKit is the closest
// available stand-in for iOS Safari, and the spec forbids a paid device farm.
const { defineConfig, devices } = require("@playwright/test");

const PORT = process.env.TT_E2E_PORT || "8099";

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "off",
  },
  projects: [
    {
      name: "webkit-desktop",
      use: { ...devices["Desktop Safari"], viewport: { width: 1280, height: 900 } },
    },
    {
      name: "webkit-iphone",
      use: { ...devices["iPhone 13"] },
    },
  ],
  webServer: {
    command: `.venv/bin/python -m tonic_trainer.server --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
