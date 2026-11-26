import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));
const INITIAL_JS_BUDGET_BYTES = 250 * 1024;

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

function syntheticEvents(count: number) {
    const start = Date.parse("2026-11-09T18:00:00.000Z");
    const items = Array.from({ length: count }, (_, index) => {
        const id = index.toString(16).padStart(32, "0");
        const ts = new Date(start + index * 10).toISOString().replace(/\.\d{3}Z$/, "Z");
        return {
            id,
            type: "decision",
            ts,
            agent: "perf-agent",
            agent_name: "perf-agent",
            agent_id: "perf-agent-id",
            kind: "tool",
            resource: `tool.${index}`,
            effect: index % 17 === 0 ? "deny" : "allow",
            status: "ok",
            rule: "agent:tools.allow[0]",
            run: "01JE2E2EPERF0000000000000",
            span_id: (index % 0x100000000).toString(16).padStart(16, "0"),
            duration_ms: 5,
        };
    });
    return { items, next_cursor: null };
}

test.describe("console performance (M6)", () => {
    test("initial JS is ≤ 250 KB gzip (CodeMirror code-split)", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        await login(page, adminKey);

        const bodies: Buffer[] = [];
        page.on("response", (response) => {
            const url = response.url();
            if (!/\/app\/assets\/[^/?#]+\.js(?:$|\?)/.test(url)) {
                return;
            }
            // Lazy policy editor (CodeMirror) must not count toward initial JS.
            if (/PolicyYamlEditor|AgentPolicy/.test(url)) {
                return;
            }
            void response.body().then(
                (body) => {
                    bodies.push(Buffer.from(body));
                },
                () => {
                    /* ignore aborted responses */
                },
            );
        });

        await page.goto("/app/overview");
        await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible({
            timeout: 15_000,
        });
        await page.waitForLoadState("networkidle");
        // Allow late body() promises to settle.
        await page.waitForTimeout(200);

        const totalGzip = bodies.reduce((sum, body) => sum + gzipSync(body).length, 0);
        expect(
            totalGzip,
            `initial JS gzip ${totalGzip} bytes exceeds ${INITIAL_JS_BUDGET_BYTES}`,
        ).toBeLessThanOrEqual(INITIAL_JS_BUDGET_BYTES);
        expect(bodies.length).toBeGreaterThan(0);
    });

    test("explorer 10k rows scroll without long tasks over 200 ms", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        await login(page, adminKey);

        const payload = syntheticEvents(10_000);
        await page.route("**/v1/events**", async (route) => {
            const url = new URL(route.request().url());
            if (url.pathname !== "/v1/events") {
                await route.continue();
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify(payload),
            });
        });
        await page.route("**/v1/events/facets**", async (route) => {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    facets: {
                        agent: [{ value: "perf-agent", count: 10_000, id: "perf" }],
                        type: [{ value: "decision", count: 10_000 }],
                        effect: [
                            { value: "allow", count: 9_412 },
                            { value: "deny", count: 588 },
                        ],
                    },
                }),
            });
        });
        await page.route("**/v1/events/histogram**", async (route) => {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    buckets: [
                        {
                            start: "2026-11-09T18:00:00Z",
                            end: "2026-11-09T19:00:00Z",
                            total: 10_000,
                            allow: 9_412,
                            deny: 588,
                            require_approval: 0,
                            error: 0,
                        },
                    ],
                }),
            });
        });

        await page.goto("/app/explorer");
        await expect(page.getByTestId("explorer-results")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("explorer-row").first()).toBeVisible({
            timeout: 15_000,
        });
        await expect(
            page.getByTestId("data-table-viewport").locator("table"),
        ).toHaveAttribute("aria-rowcount", "10000");

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
            // Skip buffered entries so the initial 10k mount does not fail scroll.
            obs.observe({ type: "longtask", buffered: false });
            (
                window as unknown as { __longTaskObserver: PerformanceObserver }
            ).__longTaskObserver = obs;
        });

        const viewport = page.getByTestId("data-table-viewport");
        for (let step = 0; step < 10; step += 1) {
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
});
