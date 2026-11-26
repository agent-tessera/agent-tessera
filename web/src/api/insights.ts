/**
 * Insights fetchers for spend, denials, footprint, posture, and quotas.
 */

import { apiRequest } from "./client";
import {
    fetchDenialsDashboard,
    fetchPostureBoard,
    queryString,
    type DenialsDashboard,
    type PostureRow,
    type SpendDashboard,
} from "./overview";

export type { DenialsDashboard, PostureRow, SpendDashboard };

/** One row from `GET /v1/quotas`. */
export type QuotaSnapshotItem = {
    agent_id: string;
    name: string;
    metric: string;
    window: string;
    scope: string;
    scope_key: string;
    max: number;
    used: number;
    outstanding: number;
    window_start: string;
};

/** Footprint totals including optional energy estimates. */
export type InsightsFootprint = {
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    model_calls: number;
    tool_calls: number;
    denials: number;
    energy_j: number | null;
    co2e_g: number | null;
    estimate: boolean;
};

/** Posture board row with component scores for weakest-column display. */
export type InsightsPostureRow = PostureRow & {
    components?: {
        owner?: number;
        guardrails_declared?: number;
        coverage?: number;
        denial_rate?: number;
        ungoverned?: number;
        probes?: number;
    };
};

function iso(date: Date): string {
    return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Fetch spend totals grouped by agent or model.
 *
 * @param from - Inclusive start.
 * @param to - Inclusive end.
 * @param groupBy - `agent` or `model`.
 */
export async function fetchSpendDashboard(
    from: Date,
    to: Date,
    groupBy: "agent" | "model" = "agent",
): Promise<SpendDashboard> {
    return apiRequest<SpendDashboard>(
        `/v1/dashboards/spend${queryString({
            from: iso(from),
            to: iso(to),
            group_by: groupBy,
        })}`,
    );
}

/**
 * Fetch denial totals by agent for a window.
 *
 * @param from - Inclusive start.
 * @param to - Inclusive end.
 */
export async function fetchInsightsDenials(
    from: Date,
    to: Date,
): Promise<DenialsDashboard> {
    return fetchDenialsDashboard(from, to);
}

/**
 * Fetch org footprint totals for a window.
 *
 * @param from - Inclusive start.
 * @param to - Inclusive end.
 */
export async function fetchInsightsFootprint(
    from: Date,
    to: Date,
): Promise<InsightsFootprint> {
    return apiRequest<InsightsFootprint>(
        `/v1/footprint${queryString({
            from: iso(from),
            to: iso(to),
        })}`,
    );
}

/** Fetch the posture board (lowest score first). */
export async function fetchInsightsPosture(): Promise<InsightsPostureRow[]> {
    return fetchPostureBoard();
}

/**
 * Fetch quota usage vs ceilings (`GET /v1/quotas`).
 *
 * @param filters - Optional environment or agent id.
 */
export async function fetchInsightsQuotas(
    filters: { environment?: string; agent_id?: string } = {},
): Promise<{ items: QuotaSnapshotItem[] }> {
    return apiRequest<{ items: QuotaSnapshotItem[] }>(
        `/v1/quotas${queryString({
            environment: filters.environment,
            agent_id: filters.agent_id,
        })}`,
    );
}
