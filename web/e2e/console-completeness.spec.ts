import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    agent_id?: string;
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
    await expect(page).toHaveURL(/\/app\/overview\/?/, { timeout: 15_000 });
}

test.describe("console completeness (0.6.1)", () => {
    test("Owners, Audit, Access, Quotas, and Fleet activity render", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.agent_id).toBeTruthy();

        await login(page, adminKey);

        await expect(page.getByTestId("fleet-activity")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("fleet-activity")).toContainText(
            /Agent activated|Policy updated/,
        );

        await page.goto("/app/settings/owners");
        await expect(page.getByTestId("settings-owners")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByText("platform", { exact: true })).toBeVisible();
        await expect(page.getByText("e2e", { exact: true })).toBeVisible();

        await page.goto("/app/settings/audit");
        await expect(page.getByTestId("settings-audit")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("audit-verify-ok")).toBeVisible();
        await expect(page.getByTestId("audit-log-list")).toBeVisible();

        await page.goto(`/app/agents/${seed.agent_id}?section=access`);
        await expect(page.getByTestId("agent-access")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("access-declared")).toContainText("files.read");
        await expect(page.getByTestId("access-declared")).toContainText("files.write");
        await expect(page.getByTestId("access-observed")).toContainText("files.read");
        await expect(page.getByTestId("access-unused")).toBeVisible();
        await expect(page.getByTestId("access-denied")).toBeVisible();

        await page.goto("/app/insights?view=quotas");
        await expect(page.getByTestId("insights-quotas")).toBeVisible({
            timeout: 15_000,
        });
        await expect(
            page.getByTestId(`quota-row-${seed.agent_id}-tokens_per_day`),
        ).toBeVisible();
        await expect(
            page.getByTestId(`quota-row-${seed.agent_id}-tokens_per_day`),
        ).toContainText("1200");
    });
});
