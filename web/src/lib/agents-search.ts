/**
 * Agents list URL search parsing and client-side facet helpers.
 *
 * Shell `environment` is preserved and sent to `GET /v1/agents`. Status and
 * autonomy facets are client-side (autonomy has no list API filter).
 */

/** Validated `/app/agents` search params. */
export type AgentsSearch = {
    environment?: string;
    status?: string;
    autonomy?: string;
    /** Filter by owner id (`GET /v1/agents?owner_id=`). */
    owner_id?: string;
    /**
     * Open the create dialog. Stored as number `1` so TanStack's JSON search
     * serializer emits `create=1` (same pattern as traces `has_denials`).
     */
    create?: number;
    /** Filter to orphaned agents (`orphaned=1`). */
    orphaned?: number;
    /** Filter to dormant agents (`dormant=1`). */
    dormant?: number;
};

export const AGENT_STATUSES = ["draft", "active", "suspended", "retired"] as const;

export const AGENT_AUTONOMY = [
    "assistive",
    "interactive",
    "supervised",
    "autonomous",
] as const;

export type AgentsFacetKey = "status" | "environment" | "autonomy";

/**
 * Parse agents list search params from a raw route search object.
 *
 * JSON search treats bare `1` / `true` as number / boolean (same as traces
 * `has_denials=1`), so flag keys accept those shapes and normalize to `1`.
 *
 * @param search - Raw route search object.
 * @returns Trimmed params; empty / blank values omitted.
 */
export function parseAgentsSearch(search: Record<string, unknown>): AgentsSearch {
    const out: AgentsSearch = {};
    for (const key of ["environment", "status", "autonomy", "owner_id"] as const) {
        const value = search[key];
        if (typeof value === "string" && value.trim()) {
            out[key] = value.trim();
        }
    }
    for (const key of ["create", "orphaned", "dormant"] as const) {
        if (isAgentsFlagEnabled(search[key])) {
            out[key] = 1;
        }
    }
    return out;
}

/**
 * Whether a flag search param is enabled (`1` / `true` / `yes`).
 *
 * Accepts the JSON-search number / boolean forms of bare `1` / `true`.
 *
 * @param value - Search flag value (string, number, boolean, or undefined).
 */
export function isAgentsFlagEnabled(value: unknown): boolean {
    if (value === true || value === 1) {
        return true;
    }
    if (typeof value !== "string") {
        return false;
    }
    const normalized = value.trim().toLowerCase();
    return normalized === "1" || normalized === "true" || normalized === "yes";
}

/**
 * Split a CSV search value into unique trimmed tokens.
 *
 * @param value - Comma-separated values, or undefined.
 */
export function splitAgentsCsv(value: string | undefined): string[] {
    if (!value) {
        return [];
    }
    const seen = new Set<string>();
    const out: string[] = [];
    for (const part of value.split(",")) {
        const trimmed = part.trim();
        if (!trimmed || seen.has(trimmed)) {
            continue;
        }
        seen.add(trimmed);
        out.push(trimmed);
    }
    return out;
}

/**
 * Toggle a facet value in the agents search (CSV for multi-select).
 *
 * @param search - Current search.
 * @param key - Facet key.
 * @param value - Value to add or remove.
 */
export function toggleAgentsFacet(
    search: AgentsSearch,
    key: AgentsFacetKey,
    value: string,
): AgentsSearch {
    const selected = new Set(splitAgentsCsv(search[key]));
    if (selected.has(value)) {
        selected.delete(value);
    } else {
        selected.add(value);
    }
    const next = [...selected];
    const out: AgentsSearch = { ...search };
    if (next.length === 0) {
        delete out[key];
    } else {
        out[key] = next.join(",");
    }
    return out;
}

/**
 * Count facet values from agent rows.
 *
 * @param items - Agent rows.
 * @param key - Facet field on each row.
 */
export function countAgentFacet(
    items: Array<{ status: string; environment: string; autonomy: string }>,
    key: AgentsFacetKey,
): Array<{ value: string; count: number }> {
    const counts = new Map<string, number>();
    for (const item of items) {
        const value = item[key];
        if (!value) {
            continue;
        }
        counts.set(value, (counts.get(value) ?? 0) + 1);
    }
    return [...counts.entries()]
        .map(([value, count]) => ({ value, count }))
        .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}

const DORMANT_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Whether an agent looks dormant (no last-seen in seven days).
 *
 * Approximates the dashboard dormant definition when event history is not
 * loaded on the list page.
 *
 * @param lastSeenAt - Agent `last_seen_at`, or null.
 * @param now - Reference clock.
 */
export function isDormantAgent(
    lastSeenAt: string | null,
    now: Date = new Date(),
): boolean {
    if (!lastSeenAt) {
        return true;
    }
    const stamp = Date.parse(lastSeenAt);
    if (!Number.isFinite(stamp)) {
        return true;
    }
    return now.getTime() - stamp >= DORMANT_MS;
}
