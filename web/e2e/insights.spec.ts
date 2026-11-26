import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    spend_total_usd?: number;
    agent_name?: string;
};

type SpendResponse = {
    group_by: string;
    items: Array<{ cost_usd: number; name?: string; agent_id?: string }>;
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

test.describe("insights", () => {
    test("spend totals match /v1/dashboards/spend; energy labelled estimate", async ({
        page,
        baseURL,
        request,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.spend_total_usd).toBe(2.5);

        await login(page, adminKey);

        const origin = new URL(baseURL ?? `http://127.0.0.1:${port}/app/`).origin;
        const to = new Date();
        const from = new Date(to.getTime() - 24 * 60 * 60 * 1000);
        const spendUrl =
            `${origin}/v1/dashboards/spend` +
            `?from=${encodeURIComponent(from.toISOString())}` +
            `&to=${encodeURIComponent(to.toISOString())}` +
            `&group_by=agent`;
        const spendRes = await request.get(spendUrl, {
            headers: { Authorization: `Bearer ${adminKey}` },
        });
        expect(spendRes.ok()).toBeTruthy();
        const spendBody = (await spendRes.json()) as SpendResponse;
        const apiTotal = spendBody.items.reduce(
            (acc, row) => acc + (Number(row.cost_usd) || 0),
            0,
        );
        expect(apiTotal).toBeCloseTo(2.5, 5);

        await page.goto("/app/insights");
        await expect(page.getByTestId("insights-page")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("insights-spend")).toBeVisible();

        const total = page.getByTestId("insights-spend-total");
        await expect(total).toBeVisible();
        await expect
            .poll(async () => Number(await total.getAttribute("data-spend-total")))
            .toBeCloseTo(apiTotal, 5);
        await expect(total).toContainText(/\$/);

        if (seed.agent_name) {
            await expect(page.getByTestId("insights-spend-bars")).toContainText(
                seed.agent_name,
            );
        }

        await page.getByTestId("insights-tab-footprint").click();
        await expect(page).toHaveURL(/view=footprint/);
        await expect(page.getByTestId("insights-footprint")).toBeVisible();
        await expect(page.getByTestId("insights-footprint-energy-estimate")).toHaveText(
            "estimate",
        );
        await expect(page.getByTestId("insights-footprint-co2e-estimate")).toHaveText(
            "estimate",
        );
        await expect(page.getByTestId("insights-footprint-energy")).not.toContainText(
            "—",
        );

        await page.getByTestId("insights-tab-denials").click();
        await expect(page.getByTestId("insights-denials")).toBeVisible();
        await page.getByTestId("insights-denials-group-rule").click();
        await expect(page).toHaveURL(/denial_by=rule/);
        await expect(page.getByTestId("insights-denials-bars")).toContainText(
            "agent:tools.deny[0]",
        );

        await page.getByTestId("insights-tab-posture").click();
        await expect(page.getByTestId("insights-posture-table")).toBeVisible();
    });
});
