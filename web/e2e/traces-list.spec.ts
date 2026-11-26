import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    run_id?: string;
    spike_run_id?: string;
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
    await expect(page).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
    await page.locator('input[name="admin_key"]').fill(adminKey);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

test.describe("traces list", () => {
    test("Has denials filter returns only runs with a deny span", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.spike_run_id).toBeTruthy();
        expect(seed.run_id).toBeTruthy();

        await login(page, adminKey);
        await page.goto("/app/traces");
        await expect(page.getByTestId("traces-page")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("traces-row").first()).toBeVisible({
            timeout: 15_000,
        });

        const before = page.getByTestId("traces-row");
        const beforeCount = await before.count();
        expect(beforeCount).toBeGreaterThan(1);

        await page.getByTestId("traces-filter-has-denials").check();
        await expect(page).toHaveURL(/has_denials=1/);

        const rows = page.getByTestId("traces-row");
        await expect
            .poll(async () => rows.count(), { timeout: 15_000 })
            .toBeGreaterThan(0);
        await expect
            .poll(async () => rows.count(), { timeout: 15_000 })
            .toBeLessThan(beforeCount);

        const count = await rows.count();
        const runIds: string[] = [];
        for (let i = 0; i < count; i += 1) {
            const row = rows.nth(i);
            const denials = Number(await row.getAttribute("data-denials"));
            expect(denials).toBeGreaterThan(0);
            const runId = await row.getAttribute("data-run-id");
            expect(runId).toBeTruthy();
            runIds.push(runId!);
        }
        expect(runIds).toContain(seed.spike_run_id);

        await rows.first().click();
        await expect(page).toHaveURL(/\/app\/traces\//);
        await expect(page.getByTestId("trace-detail")).toBeVisible();
    });
});
