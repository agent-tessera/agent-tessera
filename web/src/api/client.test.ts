import { http, HttpResponse } from "msw";
import {
    afterAll,
    afterEach,
    beforeAll,
    beforeEach,
    describe,
    expect,
    it,
    vi,
} from "vitest";

import {
    apiRequest,
    appPathForLoginNext,
    clearCsrfToken,
    mapFieldErrors,
    peekCsrfToken,
    TesseraApiError,
} from "./client";
import { server } from "../test/msw";

beforeAll(() => {
    server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
    server.resetHandlers();
    clearCsrfToken();
    vi.restoreAllMocks();
});

afterAll(() => {
    server.close();
});

beforeEach(() => {
    Object.defineProperty(window, "location", {
        configurable: true,
        value: {
            pathname: "/app/agents",
            search: "?env=prod",
            origin: "http://localhost",
            href: "http://localhost/app/agents?env=prod",
            assign: vi.fn(),
        },
    });
});

describe("mapFieldErrors", () => {
    it("maps details to a field → reason object", () => {
        expect(
            mapFieldErrors([
                { field: "email", reason: "required" },
                { field: "name", reason: "too short" },
                { field: "email", reason: "invalid" },
            ]),
        ).toEqual({ email: "invalid", name: "too short" });
    });

    it("returns an empty object when details are missing", () => {
        expect(mapFieldErrors(undefined)).toEqual({});
    });
});

describe("appPathForLoginNext", () => {
    it("keeps the current /app path and search", () => {
        expect(
            appPathForLoginNext({ pathname: "/app/runs/abc", search: "?tab=trace" }),
        ).toBe("/app/runs/abc?tab=trace");
    });

    it("falls back to /app/ outside the SPA", () => {
        expect(appPathForLoginNext({ pathname: "/login", search: "" })).toBe("/app/");
    });
});

describe("apiRequest", () => {
    it("redirects to login on 401 with the current /app path as next", async () => {
        server.use(
            http.get("/v1/agents", () => new HttpResponse(null, { status: 401 })),
        );

        const assign = vi.fn();
        Object.defineProperty(window, "location", {
            configurable: true,
            value: {
                pathname: "/app/agents",
                search: "",
                origin: "http://localhost",
                href: "http://localhost/app/agents",
                assign,
            },
        });

        await expect(apiRequest("/v1/agents")).rejects.toMatchObject({
            status: 401,
            code: "unauthorized",
        });
        expect(assign).toHaveBeenCalledWith(
            `/login?next=${encodeURIComponent("/app/agents")}`,
        );
    });

    it("maps 422 details to fieldErrors", async () => {
        server.use(
            http.post("/v1/owners", () =>
                HttpResponse.json(
                    {
                        error: {
                            code: "invalid_request",
                            message: "request validation failed",
                            details: [
                                { field: "email", reason: "Field required" },
                                {
                                    field: "name",
                                    reason: "ensure this value has at least 1 characters",
                                },
                            ],
                        },
                    },
                    { status: 422 },
                ),
            ),
        );

        let caught: unknown;
        try {
            await apiRequest("/v1/owners", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });
        } catch (err) {
            caught = err;
        }

        expect(caught).toBeInstanceOf(TesseraApiError);
        const error = caught as TesseraApiError;
        expect(error.status).toBe(422);
        expect(error.code).toBe("invalid_request");
        expect(error.fieldErrors).toEqual({
            email: "Field required",
            name: "ensure this value has at least 1 characters",
        });
    });

    it("handles 403 csrf_failed by clearing the cached token and throwing", async () => {
        let meCalls = 0;
        server.use(
            http.get("/v1/me", () => {
                meCalls += 1;
                return HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "cached-csrf",
                });
            }),
            http.post("/v1/owners", ({ request }) => {
                expect(request.headers.get("X-CSRF-Token")).toBe("cached-csrf");
                return HttpResponse.json(
                    {
                        error: {
                            code: "csrf_failed",
                            message: "csrf token is required",
                        },
                    },
                    { status: 403 },
                );
            }),
        );

        await expect(
            apiRequest("/v1/owners", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email: "a@b.c", name: "A" }),
            }),
        ).rejects.toMatchObject({
            status: 403,
            code: "csrf_failed",
            isCsrfFailed: true,
        });

        expect(meCalls).toBe(1);
        expect(peekCsrfToken()).toBeNull();
    });

    it("fetches CSRF on the first mutating call and reuses it", async () => {
        let meCalls = 0;
        const csrfHeaders: Array<string | null> = [];
        server.use(
            http.get("/v1/me", () => {
                meCalls += 1;
                return HttpResponse.json({
                    actor: "admin:test",
                    method: "key",
                    admin_key_id: "test",
                    csrf: "tok-1",
                });
            }),
            http.post("/v1/owners", ({ request }) => {
                csrfHeaders.push(request.headers.get("X-CSRF-Token"));
                return HttpResponse.json({ id: "o1" });
            }),
        );

        await apiRequest("/v1/owners", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: "a@b.c", name: "A" }),
        });
        await apiRequest("/v1/owners", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: "b@b.c", name: "B" }),
        });

        expect(meCalls).toBe(1);
        expect(csrfHeaders).toEqual(["tok-1", "tok-1"]);
        expect(peekCsrfToken()).toBe("tok-1");
    });
});
