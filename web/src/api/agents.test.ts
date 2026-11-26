import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
    createAgent,
    createRegistrationToken,
    fetchAccessFootprint,
    fetchAgent,
    fetchAgentInstances,
    fetchAgents,
    fetchOwners,
    patchAgent,
    patchOwner,
    revokeInstance,
} from "./agents";
import { clearCsrfToken, TesseraApiError } from "./client";
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

describe("agents API", () => {
    it("lists agents with filters", async () => {
        server.use(
            http.get("/v1/agents", ({ request }) => {
                const url = new URL(request.url);
                expect(url.searchParams.get("environment")).toBe("prod");
                expect(url.searchParams.get("status")).toBe("active");
                return HttpResponse.json({
                    items: [
                        {
                            id: "a1",
                            name: "probe",
                            environment: "prod",
                            description: "",
                            owner_id: "o1",
                            status: "active",
                            risk_tier: "low",
                            labels: {},
                            autonomy: "interactive",
                            supervisors: [],
                            created_at: "2026-11-09T00:00:00Z",
                            last_seen_at: null,
                            orphaned: false,
                        },
                    ],
                    next_cursor: null,
                });
            }),
        );

        const page = await fetchAgents({ environment: "prod", status: "active" });
        expect(page.items).toHaveLength(1);
        expect(page.items[0]?.name).toBe("probe");
    });

    it("creates an agent", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post("/v1/agents", async ({ request }) => {
                expect(request.headers.get("X-CSRF-Token")).toBe("csrf-token");
                const body = (await request.json()) as { name: string };
                expect(body.name).toBe("new-agent");
                return HttpResponse.json({
                    id: "a2",
                    name: "new-agent",
                    environment: "dev",
                    description: "",
                    owner_id: "o1",
                    status: "draft",
                    risk_tier: "low",
                    labels: {},
                    autonomy: "interactive",
                    supervisors: [],
                    created_at: "2026-11-10T00:00:00Z",
                    last_seen_at: null,
                    orphaned: false,
                });
            }),
        );

        const created = await createAgent({
            name: "new-agent",
            environment: "dev",
            owner_id: "o1",
        });
        expect(created.id).toBe("a2");
        expect(created.status).toBe("draft");
    });

    it("surfaces 409 conflict message from create", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post("/v1/agents", () =>
                HttpResponse.json(
                    {
                        error: {
                            code: "conflict",
                            message:
                                "an agent with this name already exists in the environment",
                        },
                    },
                    { status: 409 },
                ),
            ),
        );

        let caught: unknown;
        try {
            await createAgent({
                name: "palette-probe",
                environment: "prod",
                owner_id: "o1",
            });
        } catch (err) {
            caught = err;
        }

        expect(caught).toBeInstanceOf(TesseraApiError);
        const error = caught as TesseraApiError;
        expect(error.status).toBe(409);
        expect(error.message).toBe(
            "an agent with this name already exists in the environment",
        );
    });

    it("lists owners", async () => {
        server.use(
            http.get("/v1/owners", () =>
                HttpResponse.json({
                    items: [
                        {
                            id: "o1",
                            type: "user",
                            email: "e2e@example.com",
                            display_name: "e2e",
                            active: true,
                            created_at: "2026-11-09T00:00:00Z",
                        },
                    ],
                    next_cursor: null,
                }),
            ),
        );

        const page = await fetchOwners();
        expect(page.items[0]?.email).toBe("e2e@example.com");
    });

    it("patches an owner", async () => {
        server.use(
            http.patch("/v1/owners/o1", async ({ request }) => {
                const body = (await request.json()) as {
                    display_name?: string;
                    active?: boolean;
                };
                return HttpResponse.json({
                    id: "o1",
                    type: "user",
                    email: "e2e@example.com",
                    display_name: body.display_name ?? "e2e",
                    active: body.active ?? true,
                    created_at: "2026-11-09T00:00:00Z",
                });
            }),
        );

        const updated = await patchOwner("o1", { active: false });
        expect(updated.active).toBe(false);
    });

    it("patches agent metadata", async () => {
        server.use(
            http.patch("/v1/agents/a1", async ({ request }) => {
                const body = (await request.json()) as {
                    description?: string;
                    risk_tier?: string;
                };
                return HttpResponse.json({
                    id: "a1",
                    name: "probe",
                    environment: "prod",
                    description: body.description ?? "",
                    owner_id: "o1",
                    status: "active",
                    risk_tier: body.risk_tier ?? "low",
                    labels: {},
                    autonomy: "interactive",
                    supervisors: [],
                    created_at: "2026-11-09T00:00:00Z",
                    last_seen_at: null,
                    orphaned: false,
                    policy_version: null,
                });
            }),
        );

        const updated = await patchAgent("a1", {
            description: "updated",
            risk_tier: "high",
        });
        expect(updated.description).toBe("updated");
        expect(updated.risk_tier).toBe("high");
    });

    it("loads access footprint", async () => {
        server.use(
            http.get("/v1/agents/a1/footprint/access", () =>
                HttpResponse.json({
                    declared: [{ kind: "tool", pattern: "files.read", rule: "allow" }],
                    observed: [{ kind: "tool", resource: "files.read", count: 2 }],
                    unused: [],
                    denied: [{ kind: "tool", resource: "files.delete", count: 1 }],
                }),
            ),
        );

        const report = await fetchAccessFootprint("a1");
        expect(report.declared[0]?.pattern).toBe("files.read");
        expect(report.denied[0]?.count).toBe(1);
    });

    it("loads one agent and its instances", async () => {
        server.use(
            http.get("/v1/agents/a1", () =>
                HttpResponse.json({
                    id: "a1",
                    name: "probe",
                    environment: "prod",
                    description: "",
                    owner_id: "o1",
                    status: "retired",
                    risk_tier: "low",
                    labels: {},
                    autonomy: "interactive",
                    supervisors: [],
                    created_at: "2026-11-09T00:00:00Z",
                    last_seen_at: null,
                    orphaned: false,
                    policy_version: null,
                    instances: [],
                }),
            ),
            http.get("/v1/agents/a1/instances", () =>
                HttpResponse.json({
                    items: [
                        {
                            id: "rere" + "rere" + "rere" + "rere",
                            host: "host",
                            runtime: {},
                            started_at: "2026-11-09T00:00:00Z",
                            last_heartbeat_at: "2026-11-09T00:00:00Z",
                            revoked: false,
                        },
                    ],
                    next_cursor: null,
                }),
            ),
        );

        const agent = await fetchAgent("a1");
        expect(agent.status).toBe("retired");
        const instances = await fetchAgentInstances("a1");
        expect(instances.items).toHaveLength(1);
    });

    it("creates a registration token once", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post("/v1/agents/a1/registration-tokens", async ({ request }) => {
                expect(request.headers.get("X-CSRF-Token")).toBe("csrf-token");
                const body = (await request.json()) as {
                    reusable: boolean;
                    expires_in: number | null;
                };
                expect(body.reusable).toBe(false);
                expect(body.expires_in).toBeNull();
                return HttpResponse.json({
                    id: "tok1",
                    token: "tsr_tok1_secret",
                });
            }),
        );

        const created = await createRegistrationToken("a1");
        expect(created.id).toBe("tok1");
        expect(created.token).toBe("tsr_tok1_secret");
    });

    it("revokes an instance with reason", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post("/v1/instances/inst1:revoke", async ({ request }) => {
                expect(request.headers.get("X-CSRF-Token")).toBe("csrf-token");
                const body = (await request.json()) as { reason: string };
                expect(body.reason).toBe("done");
                return HttpResponse.json({ id: 1, kind: "instance_revoked" });
            }),
        );

        const result = await revokeInstance("inst1", { reason: "done" });
        expect(result.kind).toBe("instance_revoked");
    });
});
