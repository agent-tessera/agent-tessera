/** Pure helpers for Overview KPIs, deltas, and denial-spike detection. */

import type {
    ApprovalListItem,
    DenialsDashboard,
    HistogramBucket,
    PostureRow,
} from "@/api/overview";

/** Hours in the primary KPI window. */
export const WINDOW_HOURS = 24;

/** Posture scores below this threshold appear in Needs attention. */
export const LOW_POSTURE_THRESHOLD = 60;

/** Spike when 1h denials exceed this multiple of the 24h hourly mean. */
export const DENIAL_SPIKE_FACTOR = 3;

export type DenialSpike = {
    agent_id: string;
    name: string;
    denials_1h: number;
    denials_24h: number;
    hourly_mean: number;
};

export type AttentionKind =
    "approval" | "denial_spike" | "orphaned" | "dormant" | "posture" | "audit";

/** Destinations used by Needs attention rows. */
export type AttentionTo =
    | "/approvals"
    | "/agents"
    | "/agents/$agentId"
    | "/insights"
    | "/settings"
    | "/settings/audit"
    | "/explorer";

export type AttentionItem = {
    id: string;
    kind: AttentionKind;
    title: string;
    detail: string;
    to: AttentionTo;
    params?: { agentId: string };
    search?: Record<string, string | number>;
};

/**
 * Sum one numeric series field across histogram buckets.
 *
 * @param series - Histogram buckets.
 * @param field - Field to sum.
 */
export function sumSeries(
    series: HistogramBucket[] | undefined,
    field: keyof Pick<HistogramBucket, "total" | "deny" | "require_approval" | "error">,
): number {
    if (!series?.length) {
        return 0;
    }
    return series.reduce((acc, row) => acc + (Number(row[field]) || 0), 0);
}

/**
 * Extract sparkline values from a histogram series.
 *
 * @param series - Histogram buckets.
 * @param field - Field to plot.
 */
export function sparklineValues(
    series: HistogramBucket[] | undefined,
    field: keyof Pick<HistogramBucket, "total" | "deny" | "require_approval" | "error">,
): number[] {
    if (!series?.length) {
        return [];
    }
    return series.map((row) => Number(row[field]) || 0);
}

/**
 * Error rate over a histogram window (`error / total`).
 *
 * @param series - Histogram buckets.
 * @returns Rate in `[0, 1]`; `0` when there are no events.
 */
export function errorRate(series: HistogramBucket[] | undefined): number {
    const total = sumSeries(series, "total");
    if (total <= 0) {
        return 0;
    }
    return sumSeries(series, "error") / total;
}

/**
 * Relative delta of `current` vs `previous` (`(current - previous) / previous`).
 *
 * @param current - Current window value.
 * @param previous - Previous window value.
 * @returns Fraction change, or `null` when previous is zero and current is too.
 */
export function relativeDelta(current: number, previous: number): number | null {
    if (previous === 0) {
        if (current === 0) {
            return null;
        }
        return null;
    }
    return (current - previous) / previous;
}

/**
 * Format a relative delta for KPI display.
 *
 * @param delta - Fraction from {@link relativeDelta}, or `null`.
 */
export function formatDelta(delta: number | null): string {
    if (delta === null || Number.isNaN(delta)) {
        return "—";
    }
    const pct = Math.round(delta * 100);
    if (pct === 0) {
        return "0%";
    }
    return `${pct > 0 ? "+" : ""}${pct}%`;
}

/**
 * Format USD spend for a KPI tile.
 *
 * @param amount - Cost in USD.
 */
export function formatSpend(amount: number): string {
    if (!Number.isFinite(amount)) {
        return "$0";
    }
    if (amount === 0) {
        return "$0";
    }
    if (amount < 0.01) {
        return `$${amount.toFixed(4)}`;
    }
    return `$${amount.toFixed(2)}`;
}

/**
 * Format an error rate as a percentage string.
 *
 * @param rate - Rate in `[0, 1]`.
 */
export function formatRate(rate: number): string {
    if (!Number.isFinite(rate) || rate <= 0) {
        return "0%";
    }
    const pct = rate * 100;
    if (pct < 0.1) {
        return "<0.1%";
    }
    const rounded = Math.round(pct * 10) / 10;
    if (Number.isInteger(rounded)) {
        return `${rounded}%`;
    }
    return `${rounded.toFixed(1)}%`;
}

/**
 * Format a millisecond duration for the recent-runs table.
 *
 * @param ms - Duration in milliseconds.
 */
export function formatDuration(ms: number): string {
    if (!Number.isFinite(ms) || ms < 0) {
        return "—";
    }
    if (ms < 1000) {
        return `${Math.round(ms)}ms`;
    }
    const seconds = ms / 1000;
    if (seconds < 60) {
        return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
    }
    const minutes = Math.floor(seconds / 60);
    const rem = Math.round(seconds % 60);
    return `${minutes}m ${rem}s`;
}

/**
 * Detect agents whose last-hour denials exceed 3× their 24h hourly mean.
 *
 * @param lastHour - Denials dashboard for the last hour.
 * @param lastDay - Denials dashboard for the last 24 hours.
 */
export function findDenialSpikes(
    lastHour: DenialsDashboard,
    lastDay: DenialsDashboard,
): DenialSpike[] {
    const dayById = new Map(
        lastDay.items.map((item) => [item.agent_id, item] as const),
    );
    const spikes: DenialSpike[] = [];
    for (const hour of lastHour.items) {
        if (hour.denials <= 0) {
            continue;
        }
        const day = dayById.get(hour.agent_id);
        const denials24h = day?.denials ?? hour.denials;
        const hourlyMean = denials24h / WINDOW_HOURS;
        if (hour.denials > DENIAL_SPIKE_FACTOR * hourlyMean) {
            spikes.push({
                agent_id: hour.agent_id,
                name: hour.name || day?.name || hour.agent_id,
                denials_1h: hour.denials,
                denials_24h: denials24h,
                hourly_mean: hourlyMean,
            });
        }
    }
    spikes.sort((a, b) => b.denials_1h - a.denials_1h || a.name.localeCompare(b.name));
    return spikes;
}

/**
 * Sort pending approvals oldest-first for Needs attention.
 *
 * @param items - Approvals as returned by the API (newest first).
 */
export function approvalsOldestFirst(items: ApprovalListItem[]): ApprovalListItem[] {
    return [...items].sort((a, b) => {
        const ta = Date.parse(a.created_at);
        const tb = Date.parse(b.created_at);
        if (ta !== tb) {
            return ta - tb;
        }
        return a.id.localeCompare(b.id);
    });
}

/**
 * Filter posture rows below the attention threshold.
 *
 * @param rows - Posture board (already lowest-first).
 */
export function lowPostureAgents(rows: PostureRow[]): PostureRow[] {
    return rows.filter((row) => Number(row.score) < LOW_POSTURE_THRESHOLD);
}

/**
 * Sum spend rows for a dashboard response.
 *
 * @param items - Spend dashboard items.
 */
export function sumSpend(items: Array<{ cost_usd: number }> | undefined): number {
    if (!items?.length) {
        return 0;
    }
    return items.reduce((acc, row) => acc + (Number(row.cost_usd) || 0), 0);
}

/**
 * Map a run status string onto the design-system status chip.
 *
 * @param status - Run status from the API.
 */
export function runStatusKind(status: string): "ok" | "error" | "revoked" | "running" {
    switch (status) {
        case "completed":
        case "ok":
            return "ok";
        case "error":
        case "failed":
            return "error";
        case "revoked":
            return "revoked";
        default:
            return "running";
    }
}
