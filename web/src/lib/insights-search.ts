/**
 * Insights URL search parsing (`view`, time range, grouping, environment).
 */

/** Validated `/app/insights` search params. */
export type InsightsSearch = {
    view?: string;
    range?: string;
    group_by?: string;
    denial_by?: string;
    environment?: string;
};

/** Insights tab ids. */
export const INSIGHTS_VIEWS = [
    "spend",
    "denials",
    "footprint",
    "posture",
    "quotas",
] as const;

export type InsightsView = (typeof INSIGHTS_VIEWS)[number];

/** Relative time range presets (M6 TimeRangePicker subset). */
export const INSIGHTS_RANGES = ["15m", "1h", "24h", "7d", "30d"] as const;

export type InsightsRange = (typeof INSIGHTS_RANGES)[number];

const STRING_KEYS = [
    "view",
    "range",
    "group_by",
    "denial_by",
    "environment",
] as const satisfies ReadonlyArray<keyof InsightsSearch>;

/**
 * Parse insights search params from a raw route search object.
 *
 * @param search - Raw route search object.
 * @returns Trimmed params; empty / blank values omitted.
 */
export function parseInsightsSearch(search: Record<string, unknown>): InsightsSearch {
    const out: InsightsSearch = {};
    for (const key of STRING_KEYS) {
        const value = search[key];
        if (typeof value === "string" && value.trim()) {
            out[key] = value.trim();
        }
    }
    return out;
}

/**
 * Resolve the active insights tab.
 *
 * @param view - URL `view` param.
 */
export function resolveInsightsView(view: string | undefined): InsightsView {
    if (view && (INSIGHTS_VIEWS as readonly string[]).includes(view)) {
        return view as InsightsView;
    }
    return "spend";
}

/**
 * Resolve the active time range preset.
 *
 * @param range - URL `range` param.
 */
export function resolveInsightsRange(range: string | undefined): InsightsRange {
    if (range && (INSIGHTS_RANGES as readonly string[]).includes(range)) {
        return range as InsightsRange;
    }
    return "24h";
}

/**
 * Resolve spend grouping.
 *
 * @param groupBy - URL `group_by` param.
 */
export function resolveSpendGroupBy(groupBy: string | undefined): "agent" | "model" {
    return groupBy === "model" ? "model" : "agent";
}

/**
 * Resolve denials grouping.
 *
 * @param denialBy - URL `denial_by` param.
 */
export function resolveDenialBy(denialBy: string | undefined): "agent" | "rule" {
    return denialBy === "rule" ? "rule" : "agent";
}
