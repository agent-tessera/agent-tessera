/**
 * Capture console screenshots for docs/assets/console/.
 *
 * Run from `web/` after a build (Playwright starts e2e/serve.py):
 *
 *   npm run build
 *   npx playwright install chromium
 *   npm run screenshots
 */
import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const e2eDir = path.resolve(here, "../../e2e");
const docsAssets = path.resolve(here, "../../../docs/assets/console");

type Seed = {
    chain_run_id?: string;
    pending_approval_id?: string;
    agent_name?: string;
};

function portFromBaseURL(baseURL: string | undefined): string {
    if (!baseURL) {
        return "18700";
    }
    try {
        return new URL(baseURL).port || "18700";
    } catch {
        return "18700";
    }
}

function readAdminKey(port: string): string {
    const portPath = path.join(e2eDir, `.admin-key.${port}`);
    const legacy = path.join(e2eDir, ".admin-key");
    const file = existsSync(portPath) ? portPath : legacy;
    return readFileSync(file, "utf8").trim();
}

function readSeed(port: string): Seed {
    const portPath = path.join(e2eDir, `.seed.${port}.json`);
    const legacy = path.join(e2eDir, ".seed.json");
    const file = existsSync(portPath) ? portPath : legacy;
    return JSON.parse(readFileSync(file, "utf8")) as Seed;
}

async function login(page: Page, adminKey: string): Promise<void> {
    await page.goto("/app/overview");
    await expect(page).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
    await page.locator('input[name="admin_key"]').fill(adminKey);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

async function capture(
    page: Page,
    name: string,
    ready: () => Promise<void>,
): Promise<void> {
    await ready();
    mkdirSync(docsAssets, { recursive: true });
    const target = path.join(docsAssets, `${name}.png`);
    await page.screenshot({
        path: target,
        fullPage: false,
        animations: "disabled",
    });
    expect(existsSync(target)).toBe(true);
}

test.describe("docs console screenshots", () => {
    test("overview, explorer, agents, approvals, and trace", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.chain_run_id).toBeTruthy();
        expect(seed.pending_approval_id).toBeTruthy();

        await login(page, adminKey);
        await page.emulateMedia({ colorScheme: "light" });
        await page.setViewportSize({ width: 1280, height: 800 });

        await capture(page, "overview", async () => {
            await page.goto("/app/overview");
            await expect(page.getByTestId("overview-populated")).toBeVisible({
                timeout: 15_000,
            });
            await expect(page.getByTestId("kpi-row")).toBeVisible();
            await expect(
                page
                    .getByTestId("recent-runs")
                    .getByRole("link", { name: seed.chain_run_id }),
            ).toBeVisible({ timeout: 15_000 });
        });

        await capture(page, "explorer", async () => {
            await page.goto("/app/explorer");
            await expect(page.getByTestId("explorer-page")).toBeVisible({
                timeout: 15_000,
            });
            await expect(page.getByTestId("explorer-results")).toBeVisible({
                timeout: 15_000,
            });
        });

        await capture(page, "agents", async () => {
            await page.goto("/app/agents");
            await expect(page.getByTestId("agents-page")).toBeVisible({
                timeout: 15_000,
            });
            await expect(page.getByTestId("agents-table")).toBeVisible();
        });

        await capture(page, "approvals", async () => {
            await page.goto(
                `/app/approvals?id=${encodeURIComponent(seed.pending_approval_id!)}`,
            );
            await expect(page.getByTestId("approvals-inbox")).toBeVisible({
                timeout: 15_000,
            });
            await expect(page.getByTestId("approval-detail")).toBeVisible();
        });

        await capture(page, "trace", async () => {
            await page.goto(`/app/traces/${seed.chain_run_id}`);
            await expect(page.getByTestId("trace-detail")).toBeVisible({
                timeout: 15_000,
            });
            await expect(page.getByTestId("trace-waterfall")).toBeVisible();
            await expect(
                page
                    .getByTestId("trace-swimlane")
                    .filter({ hasText: "beta-worker" })
                    .first(),
            ).toBeVisible();
        });
    });
});
