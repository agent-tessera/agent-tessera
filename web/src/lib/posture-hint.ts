/**
 * One-line operator hint for the weakest posture component (L5).
 */

import type { AgentPosture } from "@/api/agents";

const MAX: Record<keyof AgentPosture["components"], number> = {
    owner: 20,
    guardrails_declared: 20,
    coverage: 30,
    denial_rate: 15,
    ungoverned: 15,
    probes: 0,
};

const HINTS: Record<keyof AgentPosture["components"], string> = {
    owner: "Restore an active owner to recover owner points.",
    guardrails_declared: "Declare every required guardrail on the agent policy.",
    coverage: "Raise coverage by running required input guardrails.",
    denial_rate: "Lower the denial rate in the last seven days.",
    ungoverned: "Eliminate ungoverned calls in the last seven days.",
    probes: "Posture updates as events arrive.",
};

/**
 * Name the posture component with the largest gap from its maximum.
 *
 * @param posture - Score document from `GET /v1/agents/{id}/posture`.
 */
export function weakestPostureComponent(
    posture: AgentPosture,
): keyof AgentPosture["components"] {
    let weakest: keyof AgentPosture["components"] = "owner";
    let largestGap = -1;
    for (const key of Object.keys(MAX) as Array<keyof AgentPosture["components"]>) {
        if (key === "probes") {
            continue;
        }
        const value = Number(posture.components[key] ?? 0);
        const gap = MAX[key] - (Number.isFinite(value) ? value : 0);
        if (gap > largestGap) {
            largestGap = gap;
            weakest = key;
        }
    }
    return weakest;
}

/**
 * Operator hint for the weakest posture component.
 *
 * @param posture - Score document.
 */
export function postureHint(posture: AgentPosture): string {
    return (
        HINTS[weakestPostureComponent(posture)] ?? "Posture updates as events arrive."
    );
}
