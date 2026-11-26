import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    pending_approval_id?: string;
    chain_run_id?: string;
    chain_approval_span?: string;
    beta_agent_name?: string;
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

test.describe("approvals inbox", () => {
    test("a approves; deny confirms; run link highlights waiting span", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.pending_approval_id).toBeTruthy();
        expect(seed.chain_run_id).toBeTruthy();
        expect(seed.chain_approval_span).toBeTruthy();
        expect(seed.beta_agent_name).toBe("beta-worker");

        await login(page, adminKey);
        await page.goto(
            `/app/approvals?id=${encodeURIComponent(seed.pending_approval_id!)}`,
        );
        await expect(page.getByTestId("approvals-inbox")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("approval-detail")).toHaveAttribute(
            "data-approval-id",
            seed.pending_approval_id!,
        );
        await expect(page.getByTestId("approval-status")).toHaveText("pending");
        await expect(page.getByTestId("approval-why")).toContainText("approval_rule");
        await expect(
            page.getByTestId("approval-route").locator('[data-current="true"]'),
        ).toContainText("user:e2e");

        // Deny requires confirm (d opens dialog; cancel leaves it pending).
        await page.keyboard.press("d");
        await expect(page.getByTestId("confirm-dialog")).toBeVisible();
        await page.getByTestId("confirm-cancel").click();
        await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
        await expect(page.getByTestId("approval-status")).toHaveText("pending");

        // Run link opens the chain trace with the waiting span selected.
        const runLink = page.getByTestId("approval-run-link");
        await expect(runLink).toBeVisible();
        await expect(runLink).toHaveAttribute(
            "href",
            `/app/traces/${seed.chain_run_id}?span=${seed.chain_approval_span}`,
        );
        await runLink.click();
        await expect(page).toHaveURL(new RegExp(`/app/traces/${seed.chain_run_id}`));
        await expect(page.getByTestId("trace-waterfall")).toHaveAttribute(
            "data-selected-span",
            seed.chain_approval_span!,
            { timeout: 15_000 },
        );

        // Back to inbox; a approves the focused request.
        await page.goto(
            `/app/approvals?id=${encodeURIComponent(seed.pending_approval_id!)}`,
        );
        await expect(page.getByTestId("approval-detail")).toBeVisible({
            timeout: 15_000,
        });
        await page.getByTestId("approval-comment").fill("ship it");
        await page.locator("body").click({ position: { x: 8, y: 8 } });
        await page.keyboard.press("a");
        await expect
            .poll(async () => {
                const rows = page.getByTestId("approval-row");
                const count = await rows.count();
                if (count === 0) {
                    return "empty";
                }
                const focused = page.locator(
                    `[data-testid="approval-row"][data-approval-id="${seed.pending_approval_id}"]`,
                );
                return (await focused.count()) === 0 ? "gone" : "still";
            })
            .toMatch(/empty|gone/);

        const listed = await page.request.get(
            `/v1/approvals/${seed.pending_approval_id}`,
            { headers: { Authorization: `Bearer ${adminKey}` } },
        );
        expect(listed.ok()).toBe(true);
        const body = (await listed.json()) as { status: string; resolved_by: string };
        expect(body.status).toBe("approved");
        expect(body.resolved_by).toBeTruthy();
    });
});
