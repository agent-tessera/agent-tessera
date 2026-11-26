import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { clearCsrfToken, TesseraApiError } from "./client";
import {
    createAdminKey,
    createCustodySecret,
    createWebhook,
    fetchAlerts,
    pingWebhook,
    putBaselineYaml,
    revokeAdminKey,
    updateWebhook,
} from "./settings";

const server = setupServer();

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

function withCsrf() {
    server.use(
        http.get("/v1/me", () =>
            HttpResponse.json({
                actor: "admin:test",
                csrf: "csrf-token",
            }),
        ),
    );
}

describe("settings API", () => {
    it("lists alerts", async () => {
        server.use(
            http.get("/v1/alerts", () =>
                HttpResponse.json({
                    items: [
                        {
                            id: "01ALERT",
                            name: "denials",
                            metric: "denials",
                            threshold: 5,
                            window: "1h",
                            selector: { labels: {}, autonomy: null },
                            action: "webhook",
                        },
                    ],
                }),
            ),
        );
        const page = await fetchAlerts();
        expect(page.items).toHaveLength(1);
        expect(page.items[0]?.name).toBe("denials");
    });

    it("maps 422 details on create webhook", async () => {
        withCsrf();
        server.use(
            http.post("/v1/webhooks", () =>
                HttpResponse.json(
                    {
                        error: {
                            code: "invalid_request",
                            message: "request validation failed",
                            details: [{ field: "url", reason: "Field required" }],
                        },
                    },
                    { status: 422 },
                ),
            ),
        );
        let caught: unknown;
        try {
            await createWebhook({ url: "" });
        } catch (err) {
            caught = err;
        }
        expect(caught).toBeInstanceOf(TesseraApiError);
        const error = caught as TesseraApiError;
        expect(error.status).toBe(422);
        expect(error.fieldErrors).toEqual({ url: "Field required" });
    });

    it("pings a webhook", async () => {
        withCsrf();
        server.use(
            http.post("/v1/webhooks/01HOOK:ping", () =>
                HttpResponse.json({ ok: true }),
            ),
        );
        await expect(pingWebhook("01HOOK")).resolves.toEqual({ ok: true });
    });

    it("updates a webhook", async () => {
        withCsrf();
        server.use(
            http.put("/v1/webhooks/01HOOK", async ({ request }) => {
                const body = (await request.json()) as {
                    url?: string;
                    active?: boolean;
                    clear_secret?: boolean;
                };
                return HttpResponse.json({
                    id: "01HOOK",
                    url: body.url ?? "https://hooks.example/old",
                    events: ["approval.requested"],
                    active: body.active ?? true,
                    has_secret: !body.clear_secret,
                    created_at: "2026-11-09T00:00:00Z",
                });
            }),
        );
        const updated = await updateWebhook("01HOOK", {
            url: "https://hooks.example/new",
            active: false,
            clear_secret: true,
        });
        expect(updated.url).toBe("https://hooks.example/new");
        expect(updated.active).toBe(false);
        expect(updated.has_secret).toBe(false);
    });

    it("puts baseline YAML as text", async () => {
        withCsrf();
        const yaml = "apiVersion: tessera/v1\nkind: BaselinePolicy\n";
        server.use(
            http.put("/v1/baselines/org-default", async ({ request }) => {
                expect(request.headers.get("Content-Type")).toBe("application/yaml");
                expect(await request.text()).toBe(yaml);
                return new HttpResponse(yaml, {
                    status: 200,
                    headers: { "Content-Type": "application/yaml" },
                });
            }),
        );
        await expect(putBaselineYaml("org-default", yaml)).resolves.toBe(yaml);
    });

    it("creates custody without echoing secret", async () => {
        withCsrf();
        server.use(
            http.post("/v1/custody/secrets", async ({ request }) => {
                const body = (await request.json()) as {
                    name: string;
                    provider: string;
                    secret: string;
                };
                expect(body.secret).toBe("sk-live-secret");
                return HttpResponse.json({
                    id: "01SECRET",
                    name: body.name,
                    provider: body.provider,
                });
            }),
        );
        const created = await createCustodySecret({
            name: "openai-prod",
            provider: "openai",
            secret: "sk-live-secret",
        });
        expect(created).toEqual({
            id: "01SECRET",
            name: "openai-prod",
            provider: "openai",
        });
        expect(JSON.stringify(created)).not.toContain("sk-live-secret");
    });

    it("creates and revokes admin keys", async () => {
        withCsrf();
        server.use(
            http.post("/v1/admin-keys", async ({ request }) => {
                const body = (await request.json()) as { name: string };
                expect(body.name).toBe("ops");
                return HttpResponse.json({
                    token: "tsa_01KEY_secretonce",
                    name: "ops",
                });
            }),
            http.post("/v1/admin-keys/01KEY:revoke", () =>
                HttpResponse.json({ id: "01KEY", revoked: true }),
            ),
        );
        const created = await createAdminKey("ops");
        expect(created.token).toBe("tsa_01KEY_secretonce");
        await expect(revokeAdminKey("01KEY")).resolves.toEqual({
            id: "01KEY",
            revoked: true,
        });
    });
});
