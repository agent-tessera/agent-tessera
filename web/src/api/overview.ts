/** Overview page data fetchers (`GET /v1/dashboards/*`, events, runs, …). */

import { apiRequest } from "./client";

/** Inventory and 24h rollups from `GET /v1/dashboards/summary`. */
export type DashboardSummary = {
    inventory: {
        active: number;
        suspended: number;
        draft: number;
        retired: number;
        total: number;
    };
    orphaned: number;
    dormant: number;
    pending_approvals: number;
    spend_24h: number;
    denials_24h: number;
};

/** One histogram bucket from `GET /v1/events/histogram`. */
export type HistogramBucket = {
    t: string;
    total: number;
    deny: number;
    require_approval: number;
    error: number;
};

/** Histogram document. */
export type EventHistogram = {
    from: string;
    to: string;
    bucket_seconds: number;
    series: HistogramBucket[];
};

/** Denial totals by agent. */
export type DenialsDashboard = {
    total: number;
    items: Array<{ agent_id: string; name: string; denials: number }>;
};

/** Spend totals by agent or model. */
export type SpendDashboard = {
    group_by: string;
    items: Array<{
        agent_id?: string;
        name?: string;
        model?: string;
        cost_usd: number;
    }>;
};

/** Agent list row used by Needs attention and shared list fetchers. */
export type AgentListItem = {
    id: string;
    name: string;
    environment: string;
    status: string;
    orphaned: boolean;
    last_seen_at: string | null;
    owner_id?: string;
    risk_tier?: string;
    autonomy?: string;
};

/** Pending approval row. */
export type ApprovalListItem = {
    id: string;
    agent_id: string;
    status: string;
    created_at: string;
    action?: { type?: string; name?: string; resource?: string };
};

/** Run list row with console counts. */
export type RunListItem = {
    id: string;
    status: string;
    first_seen_at: string;
    last_seen_at: string;
    participants: Array<{ instance_id: string; agent_id: string }>;
    span_count?: number;
    denial_count?: number;
    error_count?: number;
    approval_count?: number;
    event_count?: number;
};

/** Posture board row. */
export type PostureRow = {
    id: string;
    name: string;
    score: number;
    coverage?: number;
    permissive?: boolean;
};

/** Audit chain verification. */
export type AuditVerify = {
    ok: boolean;
    events: number;
    error_seq?: number;
    error?: string;
};

export type AgentsPage = {
    items: AgentListItem[];
    next_cursor: string | null;
};

export type ApprovalsPage = {
    items: ApprovalListItem[];
    next_cursor: string | null;
};

export type RunsPage = {
    items: RunListItem[];
    next_cursor: string | null;
};

function iso(date: Date): string {
    return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Build a query string from defined entries.
 *
 * @param entries - Key/value pairs; `undefined` / `null` / empty string skipped.
 */
export function queryString(
    entries: Record<string, string | number | boolean | undefined | null>,
): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(entries)) {
        if (value === undefined || value === null || value === "") {
            continue;
        }
        params.set(key, String(value));
    }
    const text = params.toString();
    return text ? `?${text}` : "";
}

/** Fetch inventory and 24h dashboard summary. */
export async function fetchDashboardSummary(): Promise<DashboardSummary> {
    return apiRequest<DashboardSummary>("/v1/dashboards/summary");
}

/**
 * Fetch a stacked event histogram.
 *
 * @param options - Time window, bucket count, and optional filters.
 */
export async function fetchEventHistogram(options: {
    since?: Date | string;
    until?: Date | string;
    buckets?: number;
    effect?: string;
    status?: string;
    agent?: string;
}): Promise<EventHistogram> {
    const since = options.since instanceof Date ? iso(options.since) : options.since;
    const until = options.until instanceof Date ? iso(options.until) : options.until;
    return apiRequest<EventHistogram>(
        `/v1/events/histogram${queryString({
            since,
            until,
            buckets: options.buckets ?? 24,
            effect: options.effect,
            status: options.status,
            agent: options.agent,
        })}`,
    );
}

/**
 * Fetch denial totals by agent for a window.
 *
 * @param from - Inclusive start.
 * @param to - Inclusive end.
 */
export async function fetchDenialsDashboard(
    from: Date,
    to: Date,
): Promise<DenialsDashboard> {
    return apiRequest<DenialsDashboard>(
        `/v1/dashboards/denials${queryString({
            from: iso(from),
            to: iso(to),
        })}`,
    );
}

/**
 * Fetch spend totals for a window.
 *
 * @param from - Inclusive start.
 * @param to - Inclusive end.
 */
export async function fetchSpendDashboard(
    from: Date,
    to: Date,
): Promise<SpendDashboard> {
    return apiRequest<SpendDashboard>(
        `/v1/dashboards/spend${queryString({
            from: iso(from),
            to: iso(to),
            group_by: "agent",
        })}`,
    );
}

/**
 * List agents with optional environment / orphaned filters.
 *
 * @param filters - List filters.
 */
export async function fetchAgents(filters: {
    environment?: string;
    orphaned?: boolean;
    status?: string;
    limit?: number;
}): Promise<AgentsPage> {
    return apiRequest<AgentsPage>(
        `/v1/agents${queryString({
            environment: filters.environment,
            orphaned: filters.orphaned,
            status: filters.status,
            limit: filters.limit ?? 100,
        })}`,
    );
}

/**
 * List pending approvals (newest first from the API).
 *
 * @param limit - Page size.
 */
export async function fetchPendingApprovalsList(limit = 50): Promise<ApprovalsPage> {
    return apiRequest<ApprovalsPage>(
        `/v1/approvals${queryString({ status: "pending", limit })}`,
    );
}

/**
 * List recent runs with span/denial counts.
 *
 * @param filters - Optional time and size filters.
 */
export async function fetchRuns(filters: {
    since?: Date | string;
    until?: Date | string;
    limit?: number;
    has_denials?: boolean;
    has_errors?: boolean;
}): Promise<RunsPage> {
    const since = filters.since instanceof Date ? iso(filters.since) : filters.since;
    const until = filters.until instanceof Date ? iso(filters.until) : filters.until;
    return apiRequest<RunsPage>(
        `/v1/runs${queryString({
            since,
            until,
            limit: filters.limit ?? 10,
            has_denials: filters.has_denials,
            has_errors: filters.has_errors,
        })}`,
    );
}

/** Fetch the posture board (lowest score first). */
export async function fetchPostureBoard(): Promise<PostureRow[]> {
    const body = await apiRequest<{ items: PostureRow[] }>("/v1/posture");
    return body.items ?? [];
}

/** Recompute the server audit chain. */
export async function fetchAuditVerify(): Promise<AuditVerify> {
    return apiRequest<AuditVerify>("/v1/audit/verify");
}

/** One row from `GET /v1/audit/log`. */
export type AuditLogEntry = {
    seq: number;
    ts: string;
    actor: string;
    type: string;
    payload: Record<string, unknown>;
    prev_hash: string;
    hash: string;
};

/** Page of server audit chain rows. */
export type AuditLogPage = {
    items: AuditLogEntry[];
    next_cursor: string | null;
};

/**
 * List the server hash-chained audit log (`GET /v1/audit/log`).
 *
 * @param filters - Page size and sequence cursor (rows after this seq).
 */
export async function fetchAuditLog(
    filters: { limit?: number; cursor?: string } = {},
): Promise<AuditLogPage> {
    return apiRequest<AuditLogPage>(
        `/v1/audit/log${queryString({
            limit: filters.limit ?? 50,
            cursor: filters.cursor,
        })}`,
    );
}

/** One row from `GET /v1/revocations`. */
export type RevocationItem = {
    id: number;
    kind: string;
    agent_id: string | null;
    subject: string;
    created_at: string;
};

/** Page of admin revocations. */
export type RevocationsPage = {
    items: RevocationItem[];
    next_cursor: string | null;
};

/**
 * List recent revocations newest first (`GET /v1/revocations`).
 *
 * @param filters - Page size, cursor, kind, and agent id.
 */
export async function fetchRevocations(
    filters: {
        limit?: number;
        cursor?: string;
        kind?: string;
        agent_id?: string;
    } = {},
): Promise<RevocationsPage> {
    return apiRequest<RevocationsPage>(
        `/v1/revocations${queryString({
            limit: filters.limit ?? 20,
            cursor: filters.cursor,
            kind: filters.kind,
            agent_id: filters.agent_id,
        })}`,
    );
}
