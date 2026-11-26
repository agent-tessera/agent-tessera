import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

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

test.describe("explorer histogram, drawer, export", () => {
    test("brush zooms URL, drawer shows JSON, export is NDJSON", async ({ page }) => {
        const adminKey = readAdminKey();
        expect(adminKey.startsWith("tsa_")).toBe(true);

        await login(page, adminKey);
        await page.goto("/app/explorer");
        await expect(page.getByTestId("explorer-page")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("explorer-histogram-svg")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("explorer-row").first()).toBeVisible({
            timeout: 15_000,
        });

        const loadedText = await page
            .getByTestId("explorer-results")
            .getByText(/^\d+ loaded/)
            .textContent();
        const loadedCount = Number(/^(\d+)/.exec(loadedText ?? "")?.[1]);
        expect(loadedCount).toBeGreaterThan(0);

        const firstRow = page.getByTestId("explorer-row").first();
        const eventId = await firstRow.getAttribute("data-event-id");
        expect(eventId).toBeTruthy();
        await firstRow.click();

        await expect(page).toHaveURL(new RegExp(`event=${eventId}`));
        await expect(page.getByTestId("explorer-drawer")).toBeVisible();
        await expect(page.getByTestId("json-view")).toBeVisible();
        const storedJson = await page
            .getByTestId("json-view")
            .locator("pre.sr-only")
            .textContent();
        expect(storedJson).toBeTruthy();
        expect(JSON.parse(storedJson!)).toMatchObject({ id: eventId });
        await page.getByTestId("drawer-close").click();
        await expect(page.getByTestId("explorer-drawer")).toHaveCount(0);

        const [download] = await Promise.all([
            page.waitForEvent("download"),
            page.getByTestId("explorer-export").click(),
        ]);
        const downloadPath = await download.path();
        expect(downloadPath).toBeTruthy();
        const body = readFileSync(downloadPath!, "utf8");
        const lines = body.trimEnd() === "" ? [] : body.trimEnd().split("\n");
        expect(lines.length).toBe(loadedCount);
        for (const line of lines) {
            expect(JSON.parse(line)).toHaveProperty("id");
        }

        const svg = page.getByTestId("explorer-histogram-svg");
        const box = await svg.boundingBox();
        expect(box).toBeTruthy();
        if (!box) {
            return;
        }

        await page.mouse.move(box.x + box.width * 0.15, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width * 0.85, box.y + box.height / 2, {
            steps: 10,
        });
        await page.mouse.up();

        await expect(page).toHaveURL(/since=/, { timeout: 10_000 });
        await expect(page).toHaveURL(/until=/);
        const since = new URL(page.url()).searchParams.get("since");
        const until = new URL(page.url()).searchParams.get("until");
        expect(since).toBeTruthy();
        expect(until).toBeTruthy();
        expect(Date.parse(since!)).toBeLessThan(Date.parse(until!));
    });
});
