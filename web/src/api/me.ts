/** Current admin identity helpers for the console shell. */

import { apiRequest } from "./client";

/** Shape of `GET /v1/me` used by the shell. */
export type MeResponse = {
    actor: string;
    method: "key" | "oidc" | "dev" | string;
    admin_key_id: string | null;
    /** Present for cookie sessions; used for logout CSRF. */
    csrf?: string;
    /**
     * External tracing URL template (`{trace_id}` / `{span_id}`) when
     * `TESSERA_SERVER_TRACE_URL` is set.
     */
    trace_url?: string | null;
};

/** Fetch the signed-in admin (`GET /v1/me`). */
export async function fetchMe(): Promise<MeResponse> {
    return apiRequest<MeResponse>("/v1/me");
}
