/**
 * Traces list URL search parsing and API filter helpers.
 *
 * Search params hold status, agent, environment, denial/error flags, and time
 * range so filtered views are shareable.
 */

/** Validated `/app/traces` search params. */
export type TracesSearch = {
    environment?: string;
    status?: string;
    agent?: string;
    since?: string;
    until?: string;
    /**
     * Require at least one deny span. Stored as number `1` so TanStack's JSON
     * search serializer emits `has_denials=1`.
     */
    has_denials?: number;
    /** Require at least one error-status event. Same encoding as `has_denials`. */
    has_errors?: number;
};

const STRING_KEYS = [
    "environment",
    "status",
    "agent",
    "since",
    "until",
] as const satisfies ReadonlyArray<keyof TracesSearch>;

/**
 * Whether a numeric `1` flag search param is enabled.
 *
 * @param value - Search flag value.
 */
export function isFlagEnabled(value: number | undefined): boolean {
    return value === 1;
}

/**
 * Parse traces list search params from a raw route search object.
 *
 * @param search - Raw route search object.
 * @returns Trimmed params; empty / blank values omitted.
 */
export function parseTracesSearch(search: Record<string, unknown>): TracesSearch {
    const out: TracesSearch = {};
    for (const key of STRING_KEYS) {
        const value = search[key];
        if (typeof value === "string" && value.trim()) {
            out[key] = value.trim();
        }
    }
    if (parseFlag(search.has_denials)) {
        out.has_denials = 1;
    }
    if (parseFlag(search.has_errors)) {
        out.has_errors = 1;
    }
    return out;
}

/**
 * Map URL search onto `GET /v1/runs` query filters.
 *
 * @param search - Current traces search.
 */
export function tracesApiFilters(search: TracesSearch): {
    status?: string;
    agent?: string;
    environment?: string;
    since?: string;
    until?: string;
    has_denials?: boolean;
    has_errors?: boolean;
} {
    const filters: {
        status?: string;
        agent?: string;
        environment?: string;
        since?: string;
        until?: string;
        has_denials?: boolean;
        has_errors?: boolean;
    } = {};
    if (search.status) {
        filters.status = search.status;
    }
    if (search.agent) {
        filters.agent = search.agent;
    }
    if (search.environment) {
        filters.environment = search.environment;
    }
    if (search.since) {
        filters.since = search.since;
    }
    if (search.until) {
        filters.until = search.until;
    }
    if (isFlagEnabled(search.has_denials)) {
        filters.has_denials = true;
    }
    if (isFlagEnabled(search.has_errors)) {
        filters.has_errors = true;
    }
    return filters;
}

function parseFlag(value: unknown): boolean {
    return value === 1 || value === true || value === "1" || value === "true";
}
