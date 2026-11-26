import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { clearCsrfToken } from "./client";
import { dryRunAgentPolicy, putAgentPolicy, validateAgentPolicy } from "./policy";
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

describe("policy API helpers", () => {
    it("validates draft YAML", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post(/\/v1\/agents\/[^/]+\/policy:validate$/, async ({ request }) => {
                expect(request.headers.get("X-CSRF-Token")).toBe("csrf-token");
                expect(request.headers.get("Content-Type")).toBe("application/yaml");
                expect(await request.text()).toContain("kind:");
                return HttpResponse.json({
                    valid: false,
                    error: "invalid YAML: line 3, column 1",
                    diff: null,
                });
            }),
        );

        const result = await validateAgentPolicy("agent-1", "kind: [\n");
        expect(result.valid).toBe(false);
        expect(result.error).toContain("line 3");
    });

    it("dry-runs a tool against the stored policy", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.post(/\/v1\/agents\/[^/]+\/policy:dry-run$/, async ({ request }) => {
                expect(await request.json()).toEqual({
                    kind: "tool",
                    resource: "shell.exec",
                });
                return HttpResponse.json({
                    effect: "deny",
                    reason: "deny_rule",
                    rule: "agent:tools.deny[0]",
                });
            }),
        );

        const result = await dryRunAgentPolicy("agent-1", {
            kind: "tool",
            resource: "shell.exec",
        });
        expect(result).toEqual({
            effect: "deny",
            reason: "deny_rule",
            rule: "agent:tools.deny[0]",
        });
    });

    it("puts policy YAML", async () => {
        server.use(
            http.get("/v1/me", () =>
                HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "csrf-token",
                }),
            ),
            http.put("/v1/agents/:id/policy", async ({ request }) => {
                expect(request.headers.get("Content-Type")).toBe("application/yaml");
                expect(await request.text()).toContain("apiVersion");
                return HttpResponse.json({ version: "sha256:abc" });
            }),
        );

        const result = await putAgentPolicy("agent-1", "apiVersion: tessera/v1\n");
        expect(result.version).toBe("sha256:abc");
    });
});
