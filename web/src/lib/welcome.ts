/** Welcome stepper helpers (URL step state and YAML preview fill). */

/** Valid onboarding steps (create → policy → connect → first event). */
export type WelcomeStep = 1 | 2 | 3 | 4;

export const WELCOME_STEPS: ReadonlyArray<{
    step: WelcomeStep;
    label: string;
}> = [
    { step: 1, label: "Create agent" },
    { step: 2, label: "Choose policy" },
    { step: 3, label: "Connect" },
    { step: 4, label: "First event" },
];

/**
 * Coerce a search value into a welcome step.
 *
 * @param value - Raw search `step`.
 * @returns Step 1–4, defaulting to 1.
 */
export function parseWelcomeStep(value: unknown): WelcomeStep {
    const n =
        typeof value === "number"
            ? value
            : typeof value === "string"
              ? Number(value)
              : NaN;
    if (n === 2 || n === 3 || n === 4) {
        return n;
    }
    return 1;
}

/**
 * Fill template placeholders with the agent's name and environment.
 *
 * @param yamlText - Template YAML with `AGENT` / `ENV` placeholders.
 * @param agentName - Agent slug.
 * @param environment - Agent environment.
 */
export function fillTemplatePreview(
    yamlText: string,
    agentName: string,
    environment: string,
): string {
    return yamlText
        .replace(/agent:\s*AGENT\b/, `agent: ${agentName}`)
        .replace(/environment:\s*ENV\b/, `environment: ${environment}`);
}

/**
 * Resolve a run id from an event document for the trace link.
 *
 * @param event - Event list item (payload-shaped).
 */
export function runIdFromEvent(event: {
    run?: unknown;
    task?: unknown;
    run_id?: unknown;
}): string | null {
    for (const key of ["run", "task", "run_id"] as const) {
        const value = event[key];
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return null;
}
