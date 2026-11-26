import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    agent_name: string;
    agent_id: string;
    run_id: string;
    span_id: string;
};

function readAdminKey(): string {
    const port = process.env.TESSERA_E2E_PORT ?? "18700";
    const portPath = path.join(here, `.admin-key.${port}`);
    const legacy = path.join(here, ".admin-key");
    return readFileSync(existsSync(portPath) ? portPath : legacy, "utf8").trim();
}

function readSeed(): Seed {
    const port = process.env.TESSERA_E2E_PORT ?? "18700";
    const portPath = path.join(here, `.seed.${port}.json`);
    const legacy = path.join(here, ".seed.json");
    return JSON.parse(
        readFileSync(existsSync(portPath) ? portPath : legacy, "utf8"),
    ) as Seed;
}

async function login(page: Page, adminKey: string): Promise<void> {
    await page.goto("/app/overview");
    await expect(page).toHaveURL(/\/login\?next=/, { timeout: 15_000 });
    await page.locator('input[name="admin_key"]').fill(adminKey);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

test.describe("command palette", () => {
    test("⌘K search opens agent; paste span id opens trace with span selected", async ({
        page,
    }) => {
        const adminKey = readAdminKey();
        const seed = readSeed();
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.agent_name).toBe("palette-probe");
        expect(seed.span_id).toMatch(/^[0-9a-f]{16}$/);

        await login(page, adminKey);

        await page.keyboard.press("ControlOrMeta+K");
        const dialog = page.getByRole("dialog", { name: "Command palette" });
        await expect(dialog).toBeVisible();
        const combobox = dialog.getByRole("combobox", {
            name: "Command palette search",
        });
        await expect(combobox).toBeVisible();

        await combobox.fill(seed.agent_name);
        await expect(
            dialog.getByRole("option", { name: new RegExp(seed.agent_name, "i") }),
        ).toBeVisible({ timeout: 10_000 });

        await page.keyboard.press("Enter");
        await expect(page).toHaveURL(new RegExp(`/app/agents/${seed.agent_id}/?`));
        await expect(
            page.getByRole("heading", { level: 1, name: seed.agent_name }),
        ).toBeVisible();

        await page.keyboard.press("ControlOrMeta+K");
        const paletteInput = page
            .getByRole("dialog", { name: "Command palette" })
            .getByRole("combobox", { name: "Command palette search" });
        await expect(paletteInput).toBeVisible();
        await paletteInput.evaluate((el, spanId) => {
            const input = el as HTMLInputElement;
            const data = new DataTransfer();
            data.setData("text/plain", spanId);
            input.dispatchEvent(
                new ClipboardEvent("paste", {
                    clipboardData: data,
                    bubbles: true,
                    cancelable: true,
                }),
            );
        }, seed.span_id);

        await expect(page).toHaveURL(
            new RegExp(`/app/traces/${seed.run_id}\\?span=${seed.span_id}`),
            { timeout: 10_000 },
        );
        await expect(page.getByTestId("span-drawer")).toContainText(seed.span_id, {
            timeout: 15_000,
        });
    });
});
