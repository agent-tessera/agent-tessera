import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    agent_id?: string;
    chain_run_id?: string;
};

type Theme = "light" | "dark";

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

async function applyTheme(page: Page, theme: Theme): Promise<void> {
    await page.evaluate((next) => {
        localStorage.setItem("tessera-theme", next);
        document.documentElement.classList.toggle("dark", next === "dark");
    }, theme);
}

function consoleRoutes(seed: Seed): { name: string; path: string }[] {
    const agentId = seed.agent_id ?? "";
    const runId = seed.chain_run_id ?? "";
    return [
        { name: "overview", path: "/app/overview" },
        { name: "explorer", path: "/app/explorer" },
        { name: "traces", path: "/app/traces" },
        { name: "trace-detail", path: `/app/traces/${runId}` },
        { name: "agents", path: "/app/agents" },
        { name: "agent-overview", path: `/app/agents/${agentId}` },
        {
            name: "agent-activity",
            path: `/app/agents/${agentId}?section=activity`,
        },
        { name: "agent-traces", path: `/app/agents/${agentId}?section=traces` },
        {
            name: "agent-instances",
            path: `/app/agents/${agentId}?section=instances`,
        },
        {
            name: "agent-connect",
            path: `/app/agents/${agentId}?section=connect`,
        },
        { name: "agent-policy", path: `/app/agents/${agentId}?section=policy` },
        { name: "approvals", path: "/app/approvals" },
        { name: "insights", path: "/app/insights" },
        { name: "settings", path: "/app/settings" },
        { name: "settings-alerts", path: "/app/settings/alerts" },
        { name: "settings-webhooks", path: "/app/settings/webhooks" },
        { name: "settings-baselines", path: "/app/settings/baselines" },
        { name: "settings-custody", path: "/app/settings/custody" },
        { name: "settings-admin-keys", path: "/app/settings/admin-keys" },
        { name: "welcome", path: "/app/welcome" },
        { name: "not-found", path: "/app/no-such-console-route" },
    ];
}

async function assertNoSeriousAxe(page: Page, label: string): Promise<void> {
    const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
    const blocking = results.violations.filter(
        (violation) =>
            violation.impact === "serious" || violation.impact === "critical",
    );
    expect(
        blocking,
        `${label}: ${blocking
            .map((v) => `${v.id} (${v.impact}): ${v.help}`)
            .join("; ")}`,
    ).toEqual([]);
}

for (const theme of ["light", "dark"] as const) {
    test.describe(`console axe (${theme})`, () => {
        test(`every route has zero serious/critical violations (${theme})`, async ({
            page,
            baseURL,
        }) => {
            test.setTimeout(180_000);
            const port = portFromBaseURL(baseURL);
            const adminKey = readAdminKey(port);
            const seed = readSeed(port);
            expect(adminKey.startsWith("tsa_")).toBe(true);
            expect(seed.agent_id).toBeTruthy();
            expect(seed.chain_run_id).toBeTruthy();

            await login(page, adminKey);
            await applyTheme(page, theme);
            await page.emulateMedia({ colorScheme: theme });

            for (const route of consoleRoutes(seed)) {
                await page.goto(route.path);
                await expect(page.locator("#root")).toBeVisible({
                    timeout: 15_000,
                });
                // Let route data settle so empty/loading skeletons don't mask issues.
                await page.waitForTimeout(250);
                await assertNoSeriousAxe(page, `${theme} ${route.name}`);
            }
        });
    });
}
