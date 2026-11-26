import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

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

async function login(page: Page, adminKey: string): Promise<void> {
    await page.goto("/app/overview");
    await expect(page).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
    await page.locator('input[name="admin_key"]').fill(adminKey);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

test.describe("settings", () => {
    test("custody never echoes secrets; admin key shown once; revoke ends sessions", async ({
        page,
        baseURL,
        request,
        browser,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        const origin = new URL(baseURL ?? `http://127.0.0.1:${port}/app/`).origin;

        await login(page, adminKey);

        const secretPlain = `sk-e2e-${Date.now()}-never-echo`;
        await page.goto("/app/settings/custody");
        await expect(page.getByTestId("settings-custody")).toBeVisible({
            timeout: 15_000,
        });

        const name = `e2e-custody-${Date.now()}`;
        await page.getByTestId("custody-name").fill(name);
        await page.getByTestId("custody-provider").fill("openai");
        await page.getByTestId("custody-secret").fill(secretPlain);
        await page.getByTestId("custody-create-submit").click();

        const row = page.locator(`[data-testid^="custody-row-"][data-name="${name}"]`);
        await expect(row).toBeVisible({ timeout: 15_000 });
        const custodyHtml = await page.getByTestId("settings-custody").innerHTML();
        expect(custodyHtml).not.toContain(secretPlain);
        expect(custodyHtml.toLowerCase()).not.toContain("ciphertext");
        await expect(page.getByTestId("custody-secret")).toHaveValue("");

        const listRes = await request.get(`${origin}/v1/custody/secrets`, {
            headers: { Authorization: `Bearer ${adminKey}` },
        });
        expect(listRes.ok()).toBeTruthy();
        const listBody = (await listRes.json()) as {
            items: Array<{ id: string; name: string; provider: string }>;
        };
        const created = listBody.items.find((row) => row.name === name);
        expect(created).toBeTruthy();
        expect(JSON.stringify(created)).not.toContain(secretPlain);
        expect(JSON.stringify(listBody)).not.toContain(secretPlain);

        const detailRes = await request.get(
            `${origin}/v1/custody/secrets/${created!.id}`,
            { headers: { Authorization: `Bearer ${adminKey}` } },
        );
        expect(detailRes.ok()).toBeTruthy();
        const detailText = await detailRes.text();
        expect(detailText).not.toContain(secretPlain);
        expect(detailText.toLowerCase()).not.toContain("ciphertext");

        await page.goto("/app/settings/admin-keys");
        await expect(page.getByTestId("settings-admin-keys")).toBeVisible({
            timeout: 15_000,
        });

        const keyName = `e2e-admin-${Date.now()}`;
        await page.getByTestId("admin-key-name").fill(keyName);
        await page.getByTestId("admin-key-create-submit").click();
        await expect(page.getByTestId("admin-key-once")).toBeVisible({
            timeout: 15_000,
        });
        const token = (await page.getByTestId("admin-key-token").innerText()).trim();
        expect(token.startsWith("tsa_")).toBe(true);
        const keyId = token.split("_")[1];
        expect(keyId).toBeTruthy();

        await page.reload();
        await expect(page.getByTestId("settings-admin-keys")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("admin-key-once")).toHaveCount(0);
        await expect(page.getByTestId(`admin-key-row-${keyId}`)).toBeVisible();

        const otherContext = await browser.newContext();
        const other = await otherContext.newPage();
        await other.goto("/app/overview");
        await expect(other).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
        await other.locator('input[name="admin_key"]').fill(token);
        await other.getByRole("button", { name: "Log in" }).click();
        await expect(other).toHaveURL(/\/app\/overview\/?/);

        await page.getByTestId(`admin-key-revoke-${keyId}`).click();
        await expect(page.getByTestId("confirm-dialog")).toBeVisible();
        await page.getByTestId("confirm-submit").click();
        await expect(
            page.locator(`[data-testid="admin-key-row-${keyId}"][data-revoked="true"]`),
        ).toBeVisible({ timeout: 15_000 });

        await other.goto("/app/settings/admin-keys");
        await expect(other).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
        await otherContext.close();
    });
});
