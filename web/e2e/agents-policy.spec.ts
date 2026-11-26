import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

type Seed = {
    agent_id?: string;
    agent_name?: string;
    policy_deny_tool?: string;
    policy_deny_rule?: string;
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

async function openPolicyTab(page: Page, agentId: string): Promise<void> {
    await page.goto(`/app/agents/${agentId}?section=policy`);
    await expect(page.getByTestId("agent-policy")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("policy-yaml-editor")).toBeVisible();
}

test.describe("agent policy editor", () => {
    test("invalid YAML shows PolicyError at the right line and saves nothing", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.agent_id).toBeTruthy();

        await login(page, adminKey);
        await openPolicyTab(page, seed.agent_id!);

        await expect(page.getByTestId("policy-yaml-editor")).toContainText(
            "files.read",
        );

        // Break YAML on a known later line so PyYAML reports line ≥ 2.
        const brokenMarker = "tessera_e2e_broken_bracket";
        const invalid =
            "apiVersion: tessera/v1\n" +
            "kind: AgentPolicy\n" +
            "metadata:\n" +
            `  agent: ${seed.agent_name}\n` +
            "  environment: prod\n" +
            "spec:\n" +
            "  tools:\n" +
            `    ${brokenMarker}: [\n`;

        const content = page.locator(".cm-content");
        await content.click();
        await page.keyboard.press(
            process.platform === "darwin" ? "Meta+A" : "Control+A",
        );
        await page.keyboard.insertText(invalid);

        await page.getByTestId("policy-save").click();
        await expect(page.getByTestId("policy-error")).toBeVisible({
            timeout: 15_000,
        });
        const errorText = await page.getByTestId("policy-error").innerText();
        expect(errorText.toLowerCase()).toMatch(/line\s+\d+/);

        const lintLine = page.getByTestId("policy-lint-line").first();
        await expect(lintLine).toBeVisible();
        const marked = Number(await lintLine.getAttribute("data-line"));
        expect(marked).toBeGreaterThanOrEqual(2);
        expect(errorText).toContain(`line ${marked}`);

        // Stored policy unchanged: reload and confirm original allow entry.
        await openPolicyTab(page, seed.agent_id!);
        await expect(page.getByTestId("policy-yaml-editor")).toContainText(
            "files.read",
            { timeout: 15_000 },
        );
        await expect(page.getByTestId("policy-yaml-editor")).not.toContainText(
            brokenMarker,
        );
    });

    test("dry run of a denied tool shows deny and the rule", async ({
        page,
        baseURL,
    }) => {
        const port = portFromBaseURL(baseURL);
        const adminKey = readAdminKey(port);
        const seed = readSeed(port);
        expect(seed.agent_id).toBeTruthy();
        expect(seed.policy_deny_tool).toBe("shell.exec");
        expect(seed.policy_deny_rule).toBe("agent:tools.deny[0]");

        await login(page, adminKey);
        await openPolicyTab(page, seed.agent_id!);

        await page.getByTestId("policy-dry-kind").selectOption("tool");
        await page.getByTestId("policy-dry-resource").fill(seed.policy_deny_tool!);
        await page.getByTestId("policy-dry-run").click();

        const result = page.getByTestId("policy-dry-result");
        await expect(result).toBeVisible({ timeout: 15_000 });
        await expect(result).toContainText("deny");
        await expect(page.getByTestId("policy-dry-rule")).toHaveText(
            seed.policy_deny_rule!,
        );
    });
});
