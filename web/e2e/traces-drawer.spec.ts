import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    revoke_run_id?: string;
    chain_run_id?: string;
    chain_root_span?: string;
    chain_trace_id?: string;
};

const TRACE_TEMPLATE = "https://tracing.example/traces/{trace_id}?span={span_id}";

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

test.describe("trace drawer and revoke", () => {
    test("revoke from console sets run revoked and audits the reason", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.revoke_run_id).toBeTruthy();
        const reason = "e2e-console-revoke";

        await login(page, adminKey);
        await page.goto(`/app/traces/${seed.revoke_run_id}`);
        await expect(page.getByTestId("trace-detail")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("trace-status")).toContainText("running");

        await page.getByTestId("trace-revoke").click();
        await expect(page.getByTestId("confirm-dialog")).toBeVisible();
        await page.getByTestId("confirm-reason").fill(reason);
        await page.getByTestId("confirm-submit").click();

        await expect(page.getByTestId("trace-status")).toContainText("revoked", {
            timeout: 15_000,
        });

        const audit = await page.request.get("/v1/audit/log?limit=100");
        expect(audit.ok()).toBeTruthy();
        const body = (await audit.json()) as {
            items: Array<{ type: string; payload: Record<string, unknown> }>;
        };
        const match = body.items.find(
            (item) =>
                item.type === "run_revoked" &&
                item.payload.id === seed.revoke_run_id &&
                item.payload.reason === reason,
        );
        expect(match).toBeTruthy();
    });

    test("external tracing link uses template when set and is hidden otherwise", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.chain_run_id).toBeTruthy();
        expect(seed.chain_root_span).toBeTruthy();
        expect(seed.chain_trace_id).toBeTruthy();

        await login(page, adminKey);

        await page.goto(
            `/app/traces/${seed.chain_run_id}?span=${seed.chain_root_span}`,
        );
        await expect(page.getByTestId("span-drawer")).toBeVisible({
            timeout: 15_000,
        });
        const expected = TRACE_TEMPLATE.replace(
            "{trace_id}",
            seed.chain_trace_id!,
        ).replace("{span_id}", seed.chain_root_span!);
        const link = page.getByTestId("span-external-trace");
        await expect(link).toBeVisible();
        await expect(link).toHaveAttribute("href", expected);

        await page.route("**/v1/me", async (route) => {
            const response = await route.fetch();
            const json = (await response.json()) as Record<string, unknown>;
            delete json.trace_url;
            await route.fulfill({
                status: response.status(),
                headers: response.headers(),
                body: JSON.stringify(json),
            });
        });
        await page.reload();
        await expect(page.getByTestId("span-drawer")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("span-external-trace")).toHaveCount(0);
    });
});
