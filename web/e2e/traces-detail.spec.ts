import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    chain_run_id?: string;
    chain_root_span?: string;
    chain_deny_span?: string;
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

function syntheticTrace(spanCount: number) {
    const start = Date.parse("2026-11-09T18:00:00.000Z");
    const spans = Array.from({ length: spanCount }, (_, index) => {
        const id = index.toString(16).padStart(16, "0");
        const t0 = start + index * 20;
        return {
            span_id: id,
            parent_span_id: null,
            agent_id: "agent-synth",
            kind: "tool",
            resource: `tool.${index}`,
            start: new Date(t0).toISOString().replace(/\.\d{3}Z$/, "Z"),
            end: new Date(t0 + 10).toISOString().replace(/\.\d{3}Z$/, "Z"),
            duration_ms: 10,
            approval_wait_ms: null,
            status: "ok",
            decision: { effect: "allow", rule: "r", reason: null },
            effects: [],
            event_ids: [],
        };
    });
    return {
        run: {
            id: "synth-5k",
            status: "completed",
            first_seen_at: "2026-11-09T18:00:00Z",
            last_seen_at: "2026-11-09T18:01:40Z",
            supervisor: null,
            participants: [{ instance_id: "aa".repeat(16), agent_id: "agent-synth" }],
            event_count: spanCount,
            span_count: spanCount,
            denial_count: 0,
            error_count: 0,
            approval_count: 0,
        },
        agents: [{ id: "agent-synth", name: "synth", environment: "prod" }],
        spans,
        unparented: [],
        truncated: false,
    };
}

test.describe("trace detail", () => {
    test("two-agent waterfall visual regression", async ({ page, baseURL }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.chain_run_id).toBeTruthy();
        expect(seed.beta_agent_name).toBe("beta-worker");

        await login(page, adminKey);
        await page.emulateMedia({ colorScheme: "light" });
        await page.goto(`/app/traces/${seed.chain_run_id}`);
        await expect(page.getByTestId("trace-detail")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("trace-waterfall")).toBeVisible();
        await expect(
            page
                .getByTestId("trace-swimlane")
                .filter({ hasText: "palette-probe" })
                .first(),
        ).toBeVisible();
        await expect(
            page
                .getByTestId("trace-swimlane")
                .filter({ hasText: "beta-worker" })
                .first(),
        ).toBeVisible();
        await expect(page.getByTestId("trace-deny-marker").first()).toBeVisible();
        await expect(page.getByTestId("trace-approval-hatch").first()).toBeVisible();

        await expect(page.getByTestId("trace-detail")).toHaveScreenshot(
            "trace-two-agent-waterfall.png",
            {
                maxDiffPixelRatio: 0.002,
                animations: "disabled",
            },
        );
    });

    test("5,000-span waterfall scrolls without long tasks over 200 ms", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        await login(page, adminKey);

        await page.route("**/v1/runs/synth-5k/trace", async (route) => {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify(syntheticTrace(5_000)),
            });
        });

        await page.goto("/app/traces/synth-5k");
        await expect(page.getByTestId("trace-waterfall-viewport")).toBeVisible({
            timeout: 15_000,
        });

        await page.evaluate(() => {
            const w = window as unknown as { __longTasks: number[] };
            w.__longTasks = [];
            const obs = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (entry.duration > 200) {
                        w.__longTasks.push(entry.duration);
                    }
                }
            });
            obs.observe({ type: "longtask", buffered: true });
            (
                window as unknown as { __longTaskObserver: PerformanceObserver }
            ).__longTaskObserver = obs;
        });

        const viewport = page.getByTestId("trace-waterfall-viewport");
        for (let step = 0; step < 8; step += 1) {
            await viewport.evaluate((el) => {
                el.scrollTop += 2_400;
            });
            await page.waitForTimeout(50);
        }

        const longTasks = await page.evaluate(() => {
            const w = window as unknown as { __longTasks: number[] };
            return w.__longTasks ?? [];
        });
        expect(longTasks).toEqual([]);
    });

    test("honors ?span= selection from paste-jump", async ({ page, baseURL }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.chain_deny_span).toBeTruthy();

        await login(page, adminKey);
        await page.goto(
            `/app/traces/${seed.chain_run_id}?span=${seed.chain_deny_span}`,
        );
        await expect(page.getByTestId("trace-waterfall")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("span-drawer")).toBeVisible();
        await expect(page.getByTestId("span-drawer")).toContainText(
            seed.chain_deny_span!,
        );
        await expect(
            page.locator(
                `[data-testid="trace-span-row"][data-span-id="${seed.chain_deny_span}"][data-selected="true"]`,
            ),
        ).toBeVisible();
    });
});
