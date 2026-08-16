// The suite runs against the STATIC build — the same docs/ tree GitHub Pages
// publishes, served by the stdlib http.server so no custom code can drift from
// the deployed artifact. WebKit is the closest available stand-in for iOS
// Safari, and the spec forbids a paid device farm.
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
    command: `.venv/bin/python -m http.server ${PORT} --directory docs`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: true,
    stdout: "ignore",
    timeout: 60_000,
  },
});
