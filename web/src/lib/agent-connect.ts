/**
 * Connect-tab helpers: first-instance short id and copyable snippets.
 */

import type { AgentInstance } from "@/api/agents";

/** Six-line Python bootstrap matching the classic Connect token page. */
export const CONNECT_PYTHON_SNIPPET = `from agent_tessera import Tessera

tessera = Tessera()
with tessera.task("demo"):
    # agent calls go here
    pass
`;

/**
 * Short id of the first live (non-revoked) instance, if any.
 *
 * @param instances - Instance rows for the agent (any order).
 * @returns Eight-character id prefix, or `null` when none are live.
 */
export function firstConnectedShortId(
    instances: readonly AgentInstance[],
): string | null {
    for (const item of instances) {
        if (item.revoked) {
            continue;
        }
        if (typeof item.id === "string" && item.id !== "") {
            return item.id.slice(0, 8);
        }
    }
    return null;
}

/**
 * Env block for the registration token shown once.
 *
 * @param serverUrl - Public base URL (`TESSERA_URL`), no trailing slash.
 * @param token - Full registration token (`tsr_...`).
 */
export function connectEnvSnippet(serverUrl: string, token: string): string {
    return `TESSERA_URL=${serverUrl}\nTESSERA_AGENT_TOKEN=${token}`;
}
