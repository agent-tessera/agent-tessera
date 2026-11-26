import { expect, test } from "@playwright/test";
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

test.describe("console auth flow", () => {
    test("unauthenticated /app/agents redirects to login and returns after sign-in; logout ends session", async ({
        page,
    }) => {
        const adminKey = readAdminKey();
        expect(adminKey.startsWith("tsa_")).toBe(true);

        // Absolute /app path — a leading "/agents" would resolve to the site root
        // and miss the SPA (Playwright baseURL path is ignored for absolute paths).
        await page.goto("/app/agents");

        await expect(page).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
        const next = new URL(page.url()).searchParams.get("next");
        expect(next).toMatch(/\/app\/agents\/?$/);

        await page.locator('input[name="admin_key"]').fill(adminKey);
        await page.getByRole("button", { name: "Log in" }).click();

        await expect(page).toHaveURL(/\/app\/agents\/?/);
        await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();

        await page.getByRole("button", { name: /Account menu/ }).click();
        await page.getByRole("menuitem", { name: "Log out" }).click();

        await expect(page).toHaveURL(/\/login/);

        await page.goto("/app/agents");
        await expect(page).toHaveURL(/\/login\?next=/);
    });
});
