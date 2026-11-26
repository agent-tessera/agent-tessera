import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    retired_agent_id?: string;
    retired_agent_name?: string;
    retired_instance_id?: string;
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

test.describe("agent detail", () => {
    test("retired agent shows no lifecycle action buttons", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.retired_agent_id).toBeTruthy();
        expect(seed.retired_agent_name).toBe("retired-shell");

        await login(page, adminKey);
        await page.goto(`/app/agents/${seed.retired_agent_id}`);
        await expect(page.getByTestId("agent-detail")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("lifecycle-badge")).toHaveAttribute(
            "data-status",
            "retired",
        );
        await expect(page.getByTestId("agent-lifecycle-actions")).toHaveCount(0);
        await expect(page.getByTestId("lifecycle-activate")).toHaveCount(0);
        await expect(page.getByTestId("lifecycle-suspend")).toHaveCount(0);
        await expect(page.getByTestId("lifecycle-retire")).toHaveCount(0);
    });

    test("revoke instance with confirm succeeds", async ({ page, baseURL }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.retired_agent_id).toBeTruthy();
        expect(seed.retired_instance_id).toBeTruthy();

        await login(page, adminKey);
        await page.goto(`/app/agents/${seed.retired_agent_id}?section=instances`);
        await expect(page.getByTestId("agent-instances")).toBeVisible({
            timeout: 15_000,
        });

        const revokeBtn = page.getByTestId(
            `instance-revoke-${seed.retired_instance_id}`,
        );
        await expect(revokeBtn).toBeVisible();
        await revokeBtn.click();
        await expect(page.getByTestId("confirm-dialog")).toBeVisible();
        await page.getByTestId("confirm-reason").fill("e2e-instance-revoke");
        await page.getByTestId("confirm-submit").click();

        await expect(revokeBtn).toHaveCount(0, { timeout: 15_000 });
        await expect(page.getByText("revoked").first()).toBeVisible();
    });
});
