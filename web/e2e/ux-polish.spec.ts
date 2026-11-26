import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const shotDir = path.join(here, "artifacts", "ux-polish");

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

async function assertNoHorizontalOverflow(page: Page): Promise<void> {
    const overflow = await page.evaluate(() => {
        const doc = document.documentElement;
        return doc.scrollWidth > doc.clientWidth + 1;
    });
    expect(overflow).toBe(false);
}

async function shot(page: Page, name: string): Promise<void> {
    mkdirSync(shotDir, { recursive: true });
    const target = path.join(shotDir, `${name}.png`);
    await page.screenshot({
        path: target,
        fullPage: true,
        animations: "disabled",
    });
    expect(existsSync(target)).toBe(true);
}

test.describe("0.6.1 UX polish", () => {
    test("overview, access, owners, audit, quotas look coherent", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.agent_id).toBeTruthy();

        await login(page, adminKey);
        await page.setViewportSize({ width: 1440, height: 900 });

        await page.goto("/app/overview");
        await expect(page.getByTestId("overview-populated")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("needs-attention")).toBeVisible();
        await expect(page.getByTestId("fleet-activity")).toBeVisible();
        await expect(page.getByTestId("fleet-activity")).toContainText(
            /Agent activated|Policy updated/,
        );
        await assertNoHorizontalOverflow(page);
        await shot(page, "overview");

        await page.goto(`/app/agents/${seed.agent_id}?section=access`);
        await expect(page.getByTestId("agent-access")).toBeVisible({
            timeout: 15_000,
        });
        for (const id of [
            "access-declared",
            "access-observed",
            "access-unused",
            "access-denied",
        ]) {
            await expect(page.getByTestId(id)).toBeVisible();
        }
        await expect(page.getByTestId("access-declared")).toContainText("files.read");
        await assertNoHorizontalOverflow(page);
        await shot(page, "agent-access");

        await page.goto("/app/settings/owners");
        await expect(page.getByTestId("settings-owners")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByText("platform", { exact: true })).toBeVisible();
        await expect(page.getByText("Active").first()).toBeVisible();
        await assertNoHorizontalOverflow(page);
        await shot(page, "settings-owners");

        await page.goto("/app/settings/audit");
        await expect(page.getByTestId("settings-audit")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("audit-verify-ok")).toBeVisible();
        await expect(page.getByTestId("audit-log-list")).toBeVisible();
        await assertNoHorizontalOverflow(page);
        await shot(page, "settings-audit");

        await page.goto("/app/insights?view=quotas");
        await expect(page.getByTestId("insights-quotas")).toBeVisible({
            timeout: 15_000,
        });
        const quotaRow = page.getByTestId(`quota-row-${seed.agent_id}-tokens_per_day`);
        await expect(quotaRow).toBeVisible();
        await expect(quotaRow).toContainText("1200");
        if (seed.agent_name) {
            await expect(quotaRow).toContainText(seed.agent_name);
        }
        await expect(quotaRow.getByRole("progressbar")).toBeVisible();
        await assertNoHorizontalOverflow(page);
        await shot(page, "insights-quotas");

        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto("/app/overview");
        await expect(page.getByTestId("fleet-activity")).toBeVisible({
            timeout: 15_000,
        });
        await assertNoHorizontalOverflow(page);
        await shot(page, "overview-mobile");

        await page.goto(`/app/agents/${seed.agent_id}?section=access`);
        await expect(page.getByTestId("agent-access")).toBeVisible({
            timeout: 15_000,
        });
        await assertNoHorizontalOverflow(page);
        await shot(page, "agent-access-mobile");
    });

    test("sidebar fills the viewport height", async ({ page, baseURL }) => {
        const adminKey = readAdminKey(portFromBaseURL(baseURL));
        await login(page, adminKey);
        await page.setViewportSize({ width: 1280, height: 800 });

        for (const route of ["/app/overview", "/app/explorer"]) {
            await page.goto(route);
            const sidebar = page.getByRole("complementary", { name: "Console" });
            await expect(sidebar).toBeVisible({ timeout: 15_000 });
            expect((await sidebar.boundingBox())?.height).toBe(800);
        }

        await expect(page.getByTestId("data-table-viewport")).toBeVisible();
        const extra = await page.evaluate(
            () =>
                document.documentElement.scrollHeight -
                document.querySelector("#root > div")!.getBoundingClientRect().height,
        );
        expect(extra).toBeLessThanOrEqual(1);

        await page.mouse.wheel(0, 2000);
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
        const sidebar = page.getByRole("complementary", { name: "Console" });
        expect((await sidebar.boundingBox())?.y).toBe(0);
    });
});
