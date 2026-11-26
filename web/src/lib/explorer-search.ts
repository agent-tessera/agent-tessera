/**
 * Explorer URL search parsing and query-token helpers.
 *
 * Search params hold every filter, time range, selected event, and live flag
 * so filtered views are shareable.
 */

/** Structured query-bar prefixes that map onto event API filters. */
export const EXPLORER_TOKEN_KEYS = [
    "agent",
    "type",
    "effect",
    "status",
    "rule",
    "run",
    "span",
    "kind",
] as const;

export type ExplorerTokenKey = (typeof EXPLORER_TOKEN_KEYS)[number];

/** Facet dimensions shown in the sidebar (with API counts). */
export const EXPLORER_FACET_KEYS = [
    "agent",
    "type",
    "effect",
    "status",
    "kind",
] as const;

export type ExplorerFacetKey = (typeof EXPLORER_FACET_KEYS)[number];

/** Filters that accept multiple OR values via comma-separated search params. */
export const EXPLORER_MULTI_KEYS = ["agent", "type", "effect"] as const;

export type ExplorerMultiKey = (typeof EXPLORER_MULTI_KEYS)[number];

/** Validated `/app/explorer` search params. */
export type ExplorerSearch = {
    environment?: string;
    resource?: string;
    agent?: string;
    type?: string;
    effect?: string;
    status?: string;
    kind?: string;
    rule?: string;
    run?: string;
    span?: string;
    since?: string;
    until?: string;
    /** Selected event id (drawer). */
    event?: string;
    /**
     * Live-tail flag. Stored as number `1` so TanStack's JSON search
     * serializer emits `live=1` (string `"1"` would become `%221%22`).
     */
    live?: number;
};

const STRING_KEYS = [
    "environment",
    "resource",
    "agent",
    "type",
    "effect",
    "status",
    "kind",
    "rule",
    "run",
    "span",
    "since",
    "until",
    "event",
] as const satisfies ReadonlyArray<keyof ExplorerSearch>;

/**
 * Whether explorer search has live tail enabled.
 *
 * @param search - Current explorer search.
 */
export function isLiveEnabled(search: Pick<ExplorerSearch, "live">): boolean {
    return search.live === 1;
}

/**
 * Parse explorer search params from a raw route search object.
 *
 * @param search - Raw route search object.
 * @returns Trimmed params; empty / blank values omitted.
 */
export function parseExplorerSearch(search: Record<string, unknown>): ExplorerSearch {
    const out: ExplorerSearch = {};
    for (const key of STRING_KEYS) {
        const value = search[key];
        if (typeof value === "string" && value.trim()) {
            out[key] = value.trim();
        }
    }
    const live = search.live;
    if (live === 1 || live === true || live === "1" || live === "true") {
        out.live = 1;
    }
    return out;
}

/**
 * Split a comma-separated multi-value filter.
 *
 * @param value - Raw search param, or `undefined`.
 * @returns Distinct non-empty values in order of first appearance.
 */
export function splitCsv(value: string | undefined): string[] {
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
 * Join filter values for a multi-value search param.
 *
 * @param values - Values to store.
 * @returns Comma-separated string, or `undefined` when empty.
 */
export function joinCsv(values: readonly string[]): string | undefined {
    const cleaned = splitCsv(values.join(","));
    return cleaned.length > 0 ? cleaned.join(",") : undefined;
}

/**
 * Whether a key accepts multiple OR values in the URL.
 *
 * @param key - Token or facet key.
 */
export function isMultiKey(key: string): key is ExplorerMultiKey {
    return (EXPLORER_MULTI_KEYS as readonly string[]).includes(key);
}

/**
 * Whether a string is a structured explorer token key.
 *
 * @param key - Candidate prefix.
 */
export function isTokenKey(key: string): key is ExplorerTokenKey {
    return (EXPLORER_TOKEN_KEYS as readonly string[]).includes(key);
}

export type ParsedQueryInput =
    | { kind: "token"; key: ExplorerTokenKey; value: string }
    | { kind: "resource"; value: string }
    | { kind: "empty" };

/**
 * Parse a query-bar draft into a structured token or free-text resource.
 *
 * Structured form is `key:value` for known prefixes; anything else is a
 * resource substring. A trailing `key:` with no value is treated as empty.
 *
 * @param raw - Current input text.
 */
export function parseQueryInput(raw: string): ParsedQueryInput {
    const text = raw.trim();
    if (!text) {
        return { kind: "empty" };
    }
    const match = /^([a-zA-Z_]+):(.*)$/.exec(text);
    const rawPrefix = match?.[1];
    const rawValue = match?.[2];
    if (rawPrefix === undefined || rawValue === undefined) {
        return { kind: "resource", value: text };
    }
    const prefix = rawPrefix.toLowerCase();
    const value = rawValue.trim();
    if (!isTokenKey(prefix)) {
        return { kind: "resource", value: text };
    }
    if (!value) {
        return { kind: "empty" };
    }
    return { kind: "token", key: prefix, value };
}

/**
 * Detect an in-progress structured token for autocomplete.
 *
 * @param raw - Current input text.
 * @returns Token key and partial value when the draft looks like `key:…`.
 */
export function autocompleteDraft(
    raw: string,
): { key: ExplorerTokenKey; partial: string } | null {
    const text = raw.trimStart();
    const match = /^([a-zA-Z_]+):(.*)$/.exec(text);
    const rawPrefix = match?.[1];
    const rawPartial = match?.[2];
    if (rawPrefix === undefined || rawPartial === undefined) {
        return null;
    }
    const prefix = rawPrefix.toLowerCase();
    if (!isTokenKey(prefix)) {
        return null;
    }
    return { key: prefix, partial: rawPartial };
}

/**
 * Add or replace a filter value on explorer search.
 *
 * Multi-keys append; single-keys replace. Passing an empty value clears.
 *
 * @param search - Current search.
 * @param key - Filter key.
 * @param value - Value to set, or empty to clear.
 */
export function setFilterValue(
    search: ExplorerSearch,
    key: ExplorerTokenKey | "resource",
    value: string,
): ExplorerSearch {
    const next = { ...search };
    const trimmed = value.trim();
    if (!trimmed) {
        delete next[key];
        return next;
    }
    if (key === "resource" || !isMultiKey(key)) {
        next[key] = trimmed;
        return next;
    }
    const existing = splitCsv(next[key]);
    next[key] = joinCsv([...existing, trimmed]);
    return next;
}

/**
 * Remove one value from a filter (or clear a single-value filter).
 *
 * @param search - Current search.
 * @param key - Filter key.
 * @param value - Value to remove when the key is multi-valued.
 */
export function removeFilterValue(
    search: ExplorerSearch,
    key: ExplorerTokenKey | "resource",
    value?: string,
): ExplorerSearch {
    const next = { ...search };
    if (key === "resource" || !isMultiKey(key) || value === undefined) {
        delete next[key];
        return next;
    }
    const remaining = splitCsv(next[key]).filter((item) => item !== value);
    const joined = joinCsv(remaining);
    if (joined) {
        next[key] = joined;
    } else {
        delete next[key];
    }
    return next;
}

/**
 * Toggle a facet value on or off in the search params.
 *
 * @param search - Current search.
 * @param key - Facet key.
 * @param value - Facet value (agent name for the agent facet).
 */
export function toggleFacetValue(
    search: ExplorerSearch,
    key: ExplorerFacetKey,
    value: string,
): ExplorerSearch {
    const trimmed = value.trim();
    if (!trimmed) {
        return search;
    }
    if (isMultiKey(key)) {
        const existing = splitCsv(search[key]);
        if (existing.includes(trimmed)) {
            return removeFilterValue(search, key, trimmed);
        }
        return setFilterValue(search, key, trimmed);
    }
    if (search[key] === trimmed) {
        return removeFilterValue(search, key);
    }
    return setFilterValue(search, key, trimmed);
}

export type ExplorerChip = {
    key: ExplorerTokenKey | "resource";
    value: string;
    label: string;
};

/**
 * Build removable chip descriptors for the active filters.
 *
 * @param search - Current search.
 */
export function explorerChips(search: ExplorerSearch): ExplorerChip[] {
    const chips: ExplorerChip[] = [];
    for (const key of EXPLORER_TOKEN_KEYS) {
        if (isMultiKey(key)) {
            for (const value of splitCsv(search[key])) {
                chips.push({ key, value, label: `${key}:${value}` });
            }
        } else if (search[key]) {
            chips.push({
                key,
                value: search[key]!,
                label: `${key}:${search[key]}`,
            });
        }
    }
    if (search.resource) {
        chips.push({
            key: "resource",
            value: search.resource,
            label: search.resource,
        });
    }
    return chips;
}

/**
 * Build the API query object from explorer search (no environment).
 *
 * The events API has no environment filter; shell `?environment=` stays in
 * the URL only.
 *
 * @param search - Current explorer search.
 */
export function explorerApiFilters(search: ExplorerSearch): {
    agent?: string[];
    type?: string[];
    effect?: string[];
    status?: string;
    kind?: string;
    resource?: string;
    rule?: string;
    run?: string;
    span?: string;
    since?: string;
    until?: string;
} {
    const agent = splitCsv(search.agent);
    const type = splitCsv(search.type);
    const effect = splitCsv(search.effect);
    return {
        agent: agent.length > 0 ? agent : undefined,
        type: type.length > 0 ? type : undefined,
        effect: effect.length > 0 ? effect : undefined,
        status: search.status,
        kind: search.kind,
        resource: search.resource,
        rule: search.rule,
        run: search.run,
        span: search.span,
        since: search.since,
        until: search.until,
    };
}
