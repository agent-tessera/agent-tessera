/**
 * Agent detail URL search parsing (`section` + explorer activity filters).
 *
 * Uses `section` (not `tab`) so it does not collide with the Trace detail
 * `tab` search union under TanStack Router's shared search typing.
 *
 * Numeric flags follow the agents-list / traces pattern so TanStack's JSON
 * search serializer emits bare `1` rather than JSON-quoted strings.
 */

import { parseExplorerSearch, type ExplorerSearch } from "@/lib/explorer-search";

/** Tabs on `/app/agents/$agentId` (M11). */
export const AGENT_DETAIL_TABS = [
    "overview",
    "activity",
    "traces",
    "policy",
    "access",
    "instances",
    "connect",
] as const;

export type AgentDetailTab = (typeof AGENT_DETAIL_TABS)[number];

/** Validated `/app/agents/$agentId` search params. */
export type AgentDetailSearch = ExplorerSearch & {
    /** Active section; omitted means overview. */
    section?: AgentDetailTab;
};

/**
 * Whether a value is a known agent-detail section.
 *
 * @param value - Candidate section id.
 */
export function isAgentDetailTab(value: unknown): value is AgentDetailTab {
    return (
        typeof value === "string" &&
        (AGENT_DETAIL_TABS as readonly string[]).includes(value)
    );
}

/**
 * Parse agent detail search params from a raw route search object.
 *
 * @param search - Raw route search object.
 */
export function parseAgentDetailSearch(
    search: Record<string, unknown>,
): AgentDetailSearch {
    const explorer = parseExplorerSearch(search);
    const out: AgentDetailSearch = { ...explorer };
    // Accept legacy `tab=` bookmarks as an alias for `section=`.
    const raw = search.section ?? search.tab;
    if (isAgentDetailTab(raw) && raw !== "overview") {
        out.section = raw;
    }
    return out;
}

/**
 * Resolve the active section from search (default overview).
 *
 * @param search - Current agent detail search.
 */
export function activeAgentTab(search: AgentDetailSearch): AgentDetailTab {
    return search.section ?? "overview";
}
