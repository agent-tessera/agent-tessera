/** Run list fetchers for the Traces console (`GET /v1/runs`). */

import { apiRequest } from "./client";
import { queryString } from "./overview";

/** One run row with console counts from `GET /v1/runs`. */
export type RunListItem = {
    id: string;
    status: string;
    first_seen_at: string;
    last_seen_at: string;
    revoked_at?: string | null;
    revoked_by?: string | null;
    supervisor?: string | null;
    principal?: string | null;
    participants: Array<{ instance_id: string; agent_id: string }>;
    event_count?: number;
    span_count?: number;
    denial_count?: number;
    error_count?: number;
    approval_count?: number;
};

/** Paged run list. */
export type RunsPage = {
    items: RunListItem[];
    next_cursor: string | null;
};

/** Filters accepted by `GET /v1/runs`. */
export type RunsQueryFilters = {
    status?: string;
    agent?: string;
    environment?: string;
    since?: string;
    until?: string;
    has_denials?: boolean;
    has_errors?: boolean;
    limit?: number;
    cursor?: string;
};

/**
 * List runs with optional M10 filters and cursor pagination.
 *
 * @param filters - Status, agent, time, denial/error flags, and page size.
 */
export async function fetchRuns(filters: RunsQueryFilters = {}): Promise<RunsPage> {
    return apiRequest<RunsPage>(
        `/v1/runs${queryString({
            status: filters.status,
            agent: filters.agent,
            environment: filters.environment,
            since: filters.since,
            until: filters.until,
            has_denials: filters.has_denials,
            has_errors: filters.has_errors,
            limit: filters.limit ?? 50,
            cursor: filters.cursor,
        })}`,
    );
}

/** Agent summary embedded in a governance trace. */
export type TraceAgent = {
    id: string;
    name: string;
    environment: string;
};

/** Decision fields merged onto a span. */
export type TraceSpanDecision = {
    effect?: string | null;
    rule?: string | null;
    reason?: string | null;
};

/** One governance span from `GET /v1/runs/{id}/trace`. */
export type TraceSpan = {
    span_id: string;
    parent_span_id?: string | null;
    trace_id?: string | null;
    agent_id?: string | null;
    instance_id?: string | null;
    kind?: string | null;
    resource?: string | null;
    start: string;
    end?: string | null;
    duration_ms?: number | null;
    approval_wait_ms?: number | null;
    status?: string | null;
    error_type?: string | null;
    decision: TraceSpanDecision;
    principal?: string | null;
    approval_id?: string | null;
    effects: string[];
    event_ids: string[];
};

/** Pre-0.6 event without a span id. */
export type TraceUnparentedEvent = {
    id: string;
    type: string;
    ts: string;
    agent_id?: string | null;
    kind?: string | null;
    resource?: string | null;
    effect?: string | null;
    status?: string | null;
};

/** Full governance trace document. */
export type RunTrace = {
    run: RunListItem;
    agents: TraceAgent[];
    spans: TraceSpan[];
    unparented: TraceUnparentedEvent[];
    truncated: boolean;
};

/**
 * Load the governance span tree for one run.
 *
 * @param runId - Run id.
 */
export async function fetchRunTrace(runId: string): Promise<RunTrace> {
    return apiRequest<RunTrace>(`/v1/runs/${encodeURIComponent(runId)}/trace`);
}

/** One effect ledger row from `GET /v1/runs/{id}/effects`. */
export type RunEffectItem = {
    ref: string;
    reversible: boolean;
    undo: string | null;
    agent_id: string;
};

/** Effect ledger page. */
export type RunEffectsPage = {
    items: RunEffectItem[];
};

/**
 * List opaque effects recorded for a run.
 *
 * @param runId - Run id.
 */
export async function fetchRunEffects(runId: string): Promise<RunEffectsPage> {
    return apiRequest<RunEffectsPage>(`/v1/runs/${encodeURIComponent(runId)}/effects`);
}

/** Body for `POST /v1/runs/{id}:revoke`. */
export type RevokeRunBody = {
    reason?: string | null;
};

/**
 * Revoke a run with an optional operator reason.
 *
 * @param runId - Run id.
 * @param body - Optional reason (max 500 chars on the server).
 */
export async function revokeRun(
    runId: string,
    body: RevokeRunBody = {},
): Promise<RunListItem> {
    return apiRequest<RunListItem>(`/v1/runs/${encodeURIComponent(runId)}:revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            reason: body.reason?.trim() ? body.reason.trim() : null,
        }),
    });
}
