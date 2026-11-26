import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    agent_name?: string;
    agent_id?: string;
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

test.describe("agents list", () => {
    test("duplicate create name shows API conflict on the name field", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.agent_name).toBe("palette-probe");

        await login(page, adminKey);
        await page.goto("/app/agents?create=1");
        await expect(page.getByTestId("agents-page")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("create-agent-dialog")).toBeVisible();

        await page.getByTestId("create-agent-name").fill(seed.agent_name!);
        await page.getByTestId("create-agent-environment").fill("prod");
        await expect(page.getByTestId("create-agent-owner")).not.toHaveValue("", {
            timeout: 10_000,
        });

        await page.getByTestId("create-agent-submit").click();

        const nameError = page.getByTestId("field-error-create-agent-name");
        await expect(nameError).toBeVisible({ timeout: 15_000 });
        await expect(nameError).toContainText(
            "an agent with this name already exists in the environment",
        );
        await expect(page.getByTestId("create-agent-dialog")).toBeVisible();
        await expect(page).toHaveURL(/\/app\/agents/);
    });
});
