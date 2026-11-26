/**
 * Pure helpers for Insights totals, denial grouping, and posture labels.
 */

import type { AgentPosture } from "@/api/agents";
import type { EventItem } from "@/api/events";
import type {
    DenialsDashboard,
    InsightsPostureRow,
    SpendDashboard,
} from "@/api/insights";
import { weakestPostureComponent } from "@/lib/posture-hint";
import { sumSpend } from "@/lib/overview-metrics";

export { sumSpend };

const WEAKEST_LABELS: Record<keyof AgentPosture["components"], string> = {
    owner: "Owner",
    guardrails_declared: "Declared",
    coverage: "Coverage",
    denial_rate: "Denial rate",
    ungoverned: "Ungoverned",
    probes: "Probes",
};

/** One row in a spend breakdown table / bar list. */
export type SpendRow = {
    key: string;
    label: string;
    cost_usd: number;
};

/** One denial breakdown row (agent or rule). */
export type DenialRow = {
    key: string;
    label: string;
    denials: number;
};

/**
 * Normalize spend dashboard items into labelled rows.
 *
 * @param data - Spend API response.
 * @param allowedAgentIds - Optional environment filter (agent group only).
 */
export function spendRows(
    data: SpendDashboard | undefined,
    allowedAgentIds?: Set<string> | null,
): SpendRow[] {
    if (!data?.items?.length) {
        return [];
    }
    const rows: SpendRow[] = [];
    for (const item of data.items) {
        if (data.group_by === "model") {
            const model = item.model ?? "(unknown)";
            rows.push({
                key: model,
                label: model || "(unnamed model)",
                cost_usd: Number(item.cost_usd) || 0,
            });
            continue;
        }
        const agentId = item.agent_id ?? "";
        if (
            allowedAgentIds &&
            allowedAgentIds.size > 0 &&
            !allowedAgentIds.has(agentId)
        ) {
            continue;
        }
        rows.push({
            key: agentId || item.name || "agent",
            label: item.name || agentId || "(unknown)",
            cost_usd: Number(item.cost_usd) || 0,
        });
    }
    return rows.sort(
        (a, b) => b.cost_usd - a.cost_usd || a.label.localeCompare(b.label),
    );
}

/**
 * Filter denial-by-agent rows to an environment agent set.
 *
 * @param data - Denials API response.
 * @param allowedAgentIds - Optional environment filter.
 */
export function denialAgentRows(
    data: DenialsDashboard | undefined,
    allowedAgentIds?: Set<string> | null,
): DenialRow[] {
    if (!data?.items?.length) {
        return [];
    }
    const rows: DenialRow[] = [];
    for (const item of data.items) {
        if (
            allowedAgentIds &&
            allowedAgentIds.size > 0 &&
            !allowedAgentIds.has(item.agent_id)
        ) {
            continue;
        }
        rows.push({
            key: item.agent_id,
            label: item.name || item.agent_id,
            denials: Number(item.denials) || 0,
        });
    }
    return rows.sort((a, b) => b.denials - a.denials || a.label.localeCompare(b.label));
}

/**
 * Group deny events by rule id.
 *
 * @param events - Decision events (prefer `effect=deny`).
 * @param allowedAgentIds - Optional environment filter.
 */
export function denialRuleRows(
    events: EventItem[],
    allowedAgentIds?: Set<string> | null,
): DenialRow[] {
    const counts = new Map<string, number>();
    for (const event of events) {
        if (event.effect !== "deny") {
            continue;
        }
        const agentId =
            (typeof event.agent_id === "string" && event.agent_id) ||
            (typeof event.agent === "string" && event.agent) ||
            "";
        if (
            allowedAgentIds &&
            allowedAgentIds.size > 0 &&
            agentId &&
            !allowedAgentIds.has(agentId)
        ) {
            continue;
        }
        const rule =
            typeof event.rule === "string" && event.rule.trim()
                ? event.rule.trim()
                : "(no rule)";
        counts.set(rule, (counts.get(rule) ?? 0) + 1);
    }
    return [...counts.entries()]
        .map(([key, denials]) => ({ key, label: key, denials }))
        .sort((a, b) => b.denials - a.denials || a.label.localeCompare(b.label));
}

/**
 * Human label for the weakest posture component.
 *
 * @param row - Posture board row with optional components.
 */
export function weakestComponentLabel(row: InsightsPostureRow): string {
    if (!row.components) {
        return "—";
    }
    const key = weakestPostureComponent({
        score: Number(row.score) || 0,
        coverage: Number(row.coverage) || 0,
        components: {
            owner: Number(row.components.owner) || 0,
            guardrails_declared: Number(row.components.guardrails_declared) || 0,
            coverage: Number(row.components.coverage) || 0,
            denial_rate: Number(row.components.denial_rate) || 0,
            ungoverned: Number(row.components.ungoverned) || 0,
            probes: Number(row.components.probes) || 0,
        },
    });
    return WEAKEST_LABELS[key] ?? key;
}

/**
 * Filter posture rows to agents in the environment set.
 *
 * @param rows - Posture board.
 * @param allowedAgentIds - Optional environment filter.
 */
export function filterPostureRows(
    rows: InsightsPostureRow[],
    allowedAgentIds?: Set<string> | null,
): InsightsPostureRow[] {
    if (!allowedAgentIds || allowedAgentIds.size === 0) {
        return rows;
    }
    return rows.filter((row) => allowedAgentIds.has(row.id));
}

/**
 * Format joules for the footprint tile.
 *
 * @param value - Joules, or null when unknown.
 */
export function formatEnergyJ(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
        return "—";
    }
    if (value >= 1_000_000) {
        return `${(value / 1_000_000).toFixed(2)} MJ`;
    }
    if (value >= 1_000) {
        return `${(value / 1_000).toFixed(2)} kJ`;
    }
    return `${value.toFixed(1)} J`;
}

/**
 * Format grams CO₂e for the footprint tile.
 *
 * @param value - Grams, or null when unknown.
 */
export function formatCo2eG(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
        return "—";
    }
    if (value >= 1_000) {
        return `${(value / 1_000).toFixed(2)} kg`;
    }
    return `${value.toFixed(2)} g`;
}

/** Stacked series point for one time bucket. */
export type StackedSpendPoint = {
    label: string;
    total: number;
    parts: Record<string, number>;
};

/**
 * Build stacked spend points from per-bucket spend responses.
 *
 * @param buckets - Bucket labels in order.
 * @param responses - Spend documents aligned with `buckets`.
 * @param groupBy - Which field labels the stack segments.
 * @param allowedAgentIds - Optional environment filter for agent grouping.
 */
export function stackSpendBuckets(
    buckets: Array<{ label: string }>,
    responses: Array<SpendDashboard | undefined>,
    groupBy: "agent" | "model",
    allowedAgentIds?: Set<string> | null,
): StackedSpendPoint[] {
    return buckets.map((bucket, index) => {
        const rows = spendRows(responses[index], allowedAgentIds);
        const parts: Record<string, number> = {};
        let total = 0;
        for (const row of rows) {
            const key = groupBy === "model" ? row.label : row.label || row.key;
            parts[key] = (parts[key] ?? 0) + row.cost_usd;
            total += row.cost_usd;
        }
        return { label: bucket.label, total, parts };
    });
}

/**
 * Collect series keys across stacked points (largest total first).
 *
 * @param points - Stacked chart points.
 * @param limit - Max series keys to keep (rest collapse to Other).
 */
export type StackedSeries = {
    keys: string[];
    points: Array<{ label: string; total: number; values: number[] }>;
};

/**
 * Limit stack series to the top contributors for a readable chart.
 *
 * @param points - Raw stacked points.
 * @param limit - Maximum named series (plus optional Other).
 */
export function prepareStackedSeries(
    points: StackedSpendPoint[],
    limit = 5,
): StackedSeries {
    const totals = new Map<string, number>();
    for (const point of points) {
        for (const [key, value] of Object.entries(point.parts)) {
            totals.set(key, (totals.get(key) ?? 0) + value);
        }
    }
    const ranked = [...totals.entries()].sort(
        (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
    );
    const top = ranked.slice(0, limit).map(([key]) => key);
    const topSet = new Set(top);
    const hasOther = ranked.length > top.length;
    const keys = hasOther ? [...top, "Other"] : top;

    const prepared = points.map((point) => {
        const values = keys.map((key) => {
            if (key === "Other") {
                let other = 0;
                for (const [name, value] of Object.entries(point.parts)) {
                    if (!topSet.has(name)) {
                        other += value;
                    }
                }
                return other;
            }
            return point.parts[key] ?? 0;
        });
        return {
            label: point.label,
            total: point.total,
            values,
        };
    });

    return { keys, points: prepared };
}
