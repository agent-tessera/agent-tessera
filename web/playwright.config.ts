import { defineConfig, devices } from "@playwright/test";

const spikePort = process.env.TESSERA_E2E_PORT ?? "18700";
const emptyPort = process.env.TESSERA_E2E_EMPTY_PORT ?? "18701";
const spikeURL =
    process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${spikePort}/app/`;
const emptyURL = `http://127.0.0.1:${emptyPort}/app/`;

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: false,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    workers: 1,
    reporter: "list",
    timeout: 60_000,
    use: {
        baseURL: spikeURL,
        trace: "on-first-retry",
    },
    projects: [
        {
            name: "chromium",
            grepInvert: /@empty/,
            use: { ...devices["Desktop Chrome"], baseURL: spikeURL },
        },
        {
            name: "empty",
            grep: /@empty/,
            use: { ...devices["Desktop Chrome"], baseURL: emptyURL },
        },
        ...(process.env.DOCS_SCREENSHOTS === "1"
            ? [
                  {
                      // Opt-in only (`npm run screenshots`); not part of `npm run e2e`.
                      name: "docs-screenshots",
                      testDir: "./tests/e2e",
                      testMatch: /screenshots\.spec\.ts/,
                      use: {
                          ...devices["Desktop Chrome"],
                          baseURL: spikeURL,
                      },
                  },
              ]
            : []),
    ],
    webServer: [
        {
            command: "uv run python e2e/serve.py",
            url: `http://127.0.0.1:${spikePort}/healthz`,
            reuseExistingServer: false,
            timeout: 120_000,
            stdout: "pipe",
            stderr: "pipe",
            env: {
                ...process.env,
                TESSERA_E2E_PORT: spikePort,
                TESSERA_E2E_SEED: "spike",
                TESSERA_SERVER_TRACE_URL:
                    process.env.TESSERA_SERVER_TRACE_URL ??
                    "https://tracing.example/traces/{trace_id}?span={span_id}",
            },
        },
        {
            command: "uv run python e2e/serve.py",
            url: `http://127.0.0.1:${emptyPort}/healthz`,
            reuseExistingServer: false,
            timeout: 120_000,
            stdout: "pipe",
            stderr: "pipe",
            env: {
                ...process.env,
                TESSERA_E2E_PORT: emptyPort,
                TESSERA_E2E_SEED: "empty",
            },
        },
    ],
});
