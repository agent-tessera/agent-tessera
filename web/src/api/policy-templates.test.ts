import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { clearCsrfToken } from "./client";
import { applyPolicyTemplate, fetchPolicyTemplates } from "./policy";
import { server } from "../test/msw";

beforeAll(() => {
    server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
    server.resetHandlers();
    clearCsrfToken();
});

afterAll(() => {
    server.close();
});

describe("policy templates API", () => {
    it("lists templates including yaml preview text", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.get("/v1/policy-templates", () =>
                HttpResponse.json({
                    items: [
                        {
                            id: "read-only",
                            title: "Read-only",
                            blurb: "Models and read tools only",
                            yaml: "apiVersion: tessera/v1\n",
                        },
                    ],
                }),
            ),
        );

        const page = await fetchPolicyTemplates();
        expect(page.items).toHaveLength(1);
        expect(page.items[0]?.id).toBe("read-only");
        expect(page.items[0]?.yaml).toContain("apiVersion");
    });

    it("applies a template via from-template", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post("/v1/agents/a1/policy:from-template", async ({ request }) => {
                const body = (await request.json()) as { template_id: string };
                expect(body.template_id).toBe("dev-permissive");
                return HttpResponse.json({ version: "1" });
            }),
        );

        const result = await applyPolicyTemplate("a1", "dev-permissive");
        expect(result.version).toBe("1");
    });
});
