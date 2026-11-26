import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    mode?: string;
};

const AGENT_NAME = "welcome-probe";
const INSTANCE_ID = "ab".repeat(16);
const RUN_ID = "01JE2E2EWELCOME00000000001";
const SPAN_ID = "abcdef0123456789";
const EVENT_ID = "c".repeat(32);

function portFromBaseURL(baseURL: string | undefined): string {
    if (!baseURL) {
        return "18701";
    }
    try {
        return new URL(baseURL).port || "18701";
    } catch {
        return "18701";
    }
}

function readAdminKey(port: string): string {
    const portPath = path.join(here, `.admin-key.${port}`);
    const legacy = path.join(here, ".admin-key");
    const file = existsSync(portPath) ? portPath : legacy;
    return readFileSync(file, "utf8").trim();
}

function readSeed(port: string): Seed {
    const portPath = path.join(here, `.seed.${port}.json`);
    const legacy = path.join(here, ".seed.json");
    const file = existsSync(portPath) ? portPath : legacy;
    return JSON.parse(readFileSync(file, "utf8")) as Seed;
}

async function login(page: Page, adminKey: string): Promise<void> {
    await page.goto("/app/overview");
    // Empty seed enables settings.dev; loopback auto-auth skips the login form.
    await expect
        .poll(() => page.url(), { timeout: 15_000 })
        .toMatch(/\/(login\?next=|app\/overview)/);
    if (/\/login/.test(page.url())) {
        await page.locator('input[name="admin_key"]').fill(adminKey);
        await page.getByRole("button", { name: "Log in" }).click();
    }
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

test.describe("welcome onboarding @empty", () => {
    test("fresh DB to first event via stepper with dev grant @empty", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.mode).toBe("empty");

        await login(page, adminKey);

        await expect(page.getByTestId("overview-first-run")).toBeVisible({
            timeout: 15_000,
        });
        await page.getByRole("link", { name: "Start onboarding" }).click();
        await expect(page).toHaveURL(/\/app\/welcome\/?/);
        await expect(page.getByTestId("welcome-wizard")).toBeVisible();
        await expect(page.getByTestId("welcome-stepper")).toBeVisible();

        // Step 1 — create agent (no owners yet → email fields).
        await page.getByTestId("create-agent-name").fill(AGENT_NAME);
        await page.getByTestId("create-agent-environment").fill("dev");
        await page.getByTestId("welcome-owner-email").fill("welcome@example.com");
        await page.getByTestId("welcome-owner-display").fill("Welcome");
        await page.getByTestId("welcome-create-submit").click();

        await expect(page.getByTestId("welcome-policy-step")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page).toHaveURL(/step=2/);

        // Step 2 — choose template with YAML preview, apply + activate.
        await page.getByTestId("welcome-template-read-only").click();
        await expect(page.getByTestId("welcome-template-preview")).toContainText(
            `agent: ${AGENT_NAME}`,
        );
        await expect(page.getByTestId("welcome-template-preview")).toContainText(
            "environment: dev",
        );
        await page.getByTestId("welcome-policy-apply").click();

        await expect(page.getByTestId("welcome-connect-step")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page).toHaveURL(/step=3/);
        await expect(page.getByTestId("connection-waiting")).toBeVisible();

        // Live first-connection via dev grant (empty seed runs with settings.dev).
        const tokenRes = await page.request.post("/v1/token", {
            data: {
                grant_type: "dev",
                agent: AGENT_NAME,
                environment: "dev",
                instance_id: INSTANCE_ID,
                host: "e2e-welcome",
                runtime: { sdk: "0.6.0" },
            },
        });
        expect(tokenRes.ok()).toBe(true);
        const tokenBody = (await tokenRes.json()) as {
            access_token: string;
            agent_id: string;
        };
        expect(tokenBody.access_token).toBeTruthy();

        await expect(page.getByTestId("connection-connected")).toBeVisible({
            timeout: 10_000,
        });
        await expect(page.getByTestId("connection-connected")).toContainText(
            INSTANCE_ID.slice(0, 8),
        );
        await page.getByTestId("welcome-connect-continue").click();

        await expect(page.getByTestId("welcome-done-step")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page).toHaveURL(/step=4/);
        await expect(page.getByTestId("welcome-waiting-event")).toBeVisible();

        const eventRes = await page.request.post("/v1/events", {
            headers: { Authorization: `Bearer ${tokenBody.access_token}` },
            data: {
                events: [
                    {
                        id: EVENT_ID,
                        ts: "2026-11-10T16:00:00.000Z",
                        type: "decision",
                        task: RUN_ID,
                        run: RUN_ID,
                        span_id: SPAN_ID,
                        kind: "tool",
                        resource: "files.read",
                        effect: "allow",
                        status: "ok",
                        rule: "agent:tools.allow[0]",
                    },
                ],
            },
        });
        expect(eventRes.ok()).toBe(true);

        await expect(page.getByTestId("welcome-first-event")).toBeVisible({
            timeout: 10_000,
        });
        const traceLink = page.getByTestId("welcome-trace-link");
        await expect(traceLink).toBeVisible();
        await expect(traceLink).toHaveAttribute(
            "href",
            `/app/traces/${RUN_ID}?span=${SPAN_ID}`,
        );
        await traceLink.click();
        await expect(page).toHaveURL(
            new RegExp(`/app/traces/${RUN_ID}\\?span=${SPAN_ID}`),
        );
    });
});
