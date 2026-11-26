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
    await expect(page).toHaveURL(/\/app\/overview\/?/);
}

test.describe("agent connect", () => {
    test("token shown once; poll switches to Connected after register", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.agent_id).toBeTruthy();
        expect(seed.agent_name).toBe("palette-probe");

        await login(page, adminKey);
        await page.goto(`/app/agents/${seed.agent_id}?section=connect`);
        await expect(page.getByTestId("agent-connect")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("connection-waiting")).toBeVisible();
        await expect(page.getByTestId("connection-status")).toHaveAttribute(
            "data-connected",
            "false",
        );

        await page.getByTestId("connect-create").click();
        await expect(page.getByTestId("connect-token-once")).toBeVisible({
            timeout: 15_000,
        });
        const token = (await page.getByTestId("registration-token").innerText()).trim();
        expect(token.startsWith("tsr_")).toBe(true);

        await expect(page.getByTestId("env-snippet")).toContainText("TESSERA_URL=");
        await expect(page.getByTestId("env-snippet")).toContainText(
            `TESSERA_AGENT_TOKEN=${token}`,
        );
        await expect(page.getByTestId("python-snippet")).toContainText(
            "from agent_tessera import Tessera",
        );

        // Token is client-only: leaving the once-view clears it from the form.
        await page.getByTestId("connect-back").click();
        await expect(page.getByTestId("connect-token-form")).toBeVisible();
        await expect(page.getByTestId("registration-token")).toHaveCount(0);

        // Mint again so we can register an instance with a fresh one-time token.
        await page.getByTestId("connect-create").click();
        await expect(page.getByTestId("registration-token")).toBeVisible({
            timeout: 15_000,
        });
        const registerToken = (
            await page.getByTestId("registration-token").innerText()
        ).trim();
        expect(registerToken.startsWith("tsr_")).toBe(true);

        const instanceId = "cd".repeat(16);
        const registered = await page.request.post("/v1/token", {
            data: {
                grant_type: "registration",
                registration_token: registerToken,
                instance_id: instanceId,
                host: "e2e-connect",
                runtime: { sdk: "0.6.0" },
            },
        });
        expect(registered.ok()).toBe(true);

        await expect(page.getByTestId("connection-connected")).toBeVisible({
            timeout: 10_000,
        });
        await expect(page.getByTestId("connection-connected")).toContainText(
            instanceId.slice(0, 8),
        );
        await expect(page.getByTestId("connection-status")).toHaveAttribute(
            "data-connected",
            "true",
        );
        await expect(page.getByTestId("connection-waiting")).toHaveCount(0);
    });
});
