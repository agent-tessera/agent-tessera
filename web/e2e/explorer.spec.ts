import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    agent_name: string;
    spike_agent_name?: string;
};

type FacetValue = { value: string; count: number; id?: string };

type FacetsResponse = {
    facets: {
        agent?: FacetValue[];
        type?: FacetValue[];
        effect?: FacetValue[];
        status?: FacetValue[];
        kind?: FacetValue[];
    };
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

async function rowIds(page: Page): Promise<string[]> {
    const rows = page.getByTestId("explorer-row");
    const count = await rows.count();
    const ids: string[] = [];
    for (let index = 0; index < count; index += 1) {
        const id = await rows.nth(index).getAttribute("data-event-id");
        if (id) {
            ids.push(id);
        }
    }
    return ids;
}

test.describe("explorer query bar and facets", () => {
    test("filters update the URL, reload restores rows, facets match API", async ({
        page,
    }) => {
        const adminKey = readAdminKey();
        const seed = readSeed();
        expect(adminKey.startsWith("tsa_")).toBe(true);
        expect(seed.spike_agent_name).toBe("denial-spike");

        await login(page, adminKey);
        await page.goto("/app/explorer");
        await expect(page.getByTestId("explorer-page")).toBeVisible({
            timeout: 15_000,
        });
        await expect(page.getByTestId("explorer-results")).toBeVisible({
            timeout: 15_000,
        });

        const denyFacet = page.locator(
            '[data-facet-key="effect"][data-facet-value="deny"]',
        );
        await expect(denyFacet).toBeVisible({ timeout: 15_000 });
        await denyFacet.click();

        await expect(page).toHaveURL(/effect=deny/);
        await expect(page.getByTestId("explorer-chips")).toContainText("effect:deny");

        await expect
            .poll(async () => rowIds(page), { timeout: 15_000 })
            .not.toEqual([]);

        const filteredIds = await rowIds(page);
        expect(filteredIds.length).toBeGreaterThan(0);
        for (const row of await page.getByTestId("explorer-row").all()) {
            await expect(row).toHaveAttribute("data-effect", "deny");
        }

        const apiFacets = (await page.evaluate(async () => {
            const response = await fetch("/v1/events/facets?effect=deny", {
                credentials: "include",
                headers: { Accept: "application/json" },
            });
            if (!response.ok) {
                throw new Error(`facets ${response.status}`);
            }
            return response.json();
        })) as FacetsResponse;

        for (const key of ["agent", "type", "effect", "status", "kind"] as const) {
            const values = apiFacets.facets[key] ?? [];
            for (const entry of values) {
                const countEl = page.getByTestId(`facet-count-${key}-${entry.value}`);
                await expect(countEl).toHaveText(String(entry.count));
            }
        }

        await page.reload();
        await expect(page).toHaveURL(/effect=deny/);
        await expect(page.getByTestId("explorer-chips")).toContainText("effect:deny");
        await expect
            .poll(async () => rowIds(page), { timeout: 15_000 })
            .toEqual(filteredIds);

        const input = page.getByTestId("explorer-query-input");
        await input.fill("agent:denial-spike");
        await input.press("Enter");
        await expect(page).toHaveURL(/agent=denial-spike/);
        await expect(page).toHaveURL(/effect=deny/);
        await expect
            .poll(async () => {
                const rows = page.getByTestId("explorer-row");
                const count = await rows.count();
                if (count === 0) {
                    return false;
                }
                for (let index = 0; index < count; index += 1) {
                    const agent = await rows.nth(index).getAttribute("data-agent");
                    if (agent !== "denial-spike") {
                        return false;
                    }
                }
                return true;
            })
            .toBe(true);
    });
});
