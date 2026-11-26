import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const LIVE_RESOURCE = "live.tail";

function readAdminKey(): string {
    const port = process.env.TESSERA_E2E_PORT ?? "18700";
    const portPath = path.join(here, `.admin-key.${port}`);
    const legacy = path.join(here, ".admin-key");
    return readFileSync(existsSync(portPath) ? portPath : legacy, "utf8").trim();
}

async function login(page: Page, adminKey: string): Promise<void> {
    await page.goto("/app/overview");
    await expect(page).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
    await page.locator('input[name="admin_key"]').fill(adminKey);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

async function liveRowIds(page: Page): Promise<string[]> {
    const rows = page.locator(
        `[data-testid="explorer-row"][data-resource="${LIVE_RESOURCE}"]`,
    );
    const count = await rows.count();
    const ids: string[] = [];
    for (let index = 0; index < count; index += 1) {
        const id = await rows.nth(index).getAttribute("data-event-id");
        if (id) {
            ids.push(id);
        }
    }
    return ids;
}

test.describe("explorer live tail", () => {
    test("streams new rows, pauses on scroll, resumes buffered once", async ({
        page,
    }) => {
        const adminKey = readAdminKey();
        expect(adminKey.startsWith("tsa_")).toBe(true);

        await login(page, adminKey);
        await page.goto("/app/explorer");
        await expect(page.getByTestId("explorer-page")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("explorer-row").first()).toBeVisible({
            timeout: 15_000,
        });

        const before = new Set(await liveRowIds(page));

        await page.getByTestId("explorer-live-toggle").check();
        await expect(page).toHaveURL(/live=1/);

        await expect
            .poll(
                async () => {
                    const ids = await liveRowIds(page);
                    return ids.some((id) => !before.has(id));
                },
                { timeout: 2_000 },
            )
            .toBe(true);

        const viewport = page.getByTestId("data-table-viewport");
        await expect
            .poll(async () =>
                viewport.evaluate((el) => el.scrollHeight > el.clientHeight + 40),
            )
            .toBe(true);

        await viewport.evaluate((el) => {
            el.scrollTop = Math.min(240, el.scrollHeight - el.clientHeight);
        });
        await expect(page.getByTestId("explorer-live-paused")).toBeVisible({
            timeout: 5_000,
        });

        const pausedLiveIds = new Set(await liveRowIds(page));
        await expect(page.getByTestId("explorer-live-resume")).toBeVisible({
            timeout: 5_000,
        });
        const resumeLabel = (
            await page.getByTestId("explorer-live-resume").textContent()
        )?.trim();
        expect(resumeLabel).toMatch(/^\d+ new events?$/);
        const buffered = Number(resumeLabel?.match(/^(\d+)/)?.[1] ?? "0");
        expect(buffered).toBeGreaterThan(0);

        await page.getByTestId("explorer-live-resume").click();
        await expect(page.getByTestId("explorer-live-resume")).toHaveCount(0);
        await expect(page.getByTestId("explorer-live-paused")).toHaveCount(0);

        const afterResume = await liveRowIds(page);
        const newlyVisible = afterResume.filter((id) => !pausedLiveIds.has(id));
        expect(newlyVisible.length).toBeGreaterThanOrEqual(buffered);

        const unique = new Set(afterResume);
        expect(unique.size).toBe(afterResume.length);
    });
});
