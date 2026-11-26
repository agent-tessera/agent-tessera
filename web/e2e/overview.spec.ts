import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    mode?: string;
    spike_agent_name?: string;
    spike_agent_id?: string;
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
    // Empty seed enables settings.dev; loopback auto-auth skips the login form.
    const keyInput = page.locator('input[name="admin_key"]');
    const shell = page.getByRole("complementary", { name: "Console" });
    await expect(keyInput.or(shell)).toBeVisible({
        timeout: 15_000,
    });
    if (await keyInput.isVisible()) {
        await keyInput.fill(adminKey);
        await page.getByRole("button", { name: "Log in" }).click();
    }
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

test.describe("overview spike seed", () => {
    test("Needs attention lists the denial-spike agent", async ({ page, baseURL }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.spike_agent_name).toBe("denial-spike");

        await login(page, adminKey);

        await expect(page.getByTestId("overview-populated")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("kpi-row")).toBeVisible();
        await expect(page.getByTestId("onboarding-card")).toHaveCount(0);

        const needs = page.getByTestId("needs-attention");
        await expect(needs).toBeVisible();
        await expect(needs.getByText(/Denial spike · denial-spike/)).toBeVisible({
            timeout: 15_000,
        });
        await expect(
            needs.locator(`[data-attention-id="spike:${seed.spike_agent_id}"]`),
        ).toBeVisible();
    });
});

test.describe("overview empty seed @empty", () => {
    test("fresh DB shows only the onboarding card @empty", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.mode).toBe("empty");

        await login(page, adminKey);

        await expect(page.getByTestId("overview-first-run")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("onboarding-card")).toBeVisible();
        await expect(page.getByTestId("kpi-row")).toHaveCount(0);
        await expect(page.getByTestId("needs-attention")).toHaveCount(0);
        await expect(
            page.getByRole("link", { name: "Start onboarding" }),
        ).toHaveAttribute("href", "/app/welcome");
    });
});
