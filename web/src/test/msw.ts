import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/** Shared MSW handlers for unit tests. */
export const handlers = [
    http.get("/v1/me", () =>
        HttpResponse.json({
            actor: "admin:test",
            method: "key",
            admin_key_id: "test",
            csrf: "test-csrf",
        }),
    ),
];

export const server = setupServer(...handlers);
