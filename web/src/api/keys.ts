/**
 * TanStack Query key factories for console resources.
 *
 * Keep list/detail filters in the key so cache entries stay isolated.
 */

/** Current admin identity (`GET /v1/me`). */
export const meKeys = {
    all: ["me"] as const,
};

/** Event explorer queries and streams. */
export const eventsKeys = {
    all: ["events"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...eventsKeys.all, "list", filters] as const,
    facets: (filters: Record<string, unknown> = {}) =>
        [...eventsKeys.all, "facets", filters] as const,
    histogram: (filters: Record<string, unknown> = {}) =>
        [...eventsKeys.all, "histogram", filters] as const,
};

/** Run list and governance traces. */
export const runsKeys = {
    all: ["runs"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...runsKeys.all, "list", filters] as const,
    detail: (runId: string) => [...runsKeys.all, "detail", runId] as const,
    trace: (runId: string) => [...runsKeys.all, "trace", runId] as const,
    effects: (runId: string) => [...runsKeys.all, "effects", runId] as const,
};

/** Agent registry and instances. */
export const agentsKeys = {
    all: ["agents"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...agentsKeys.all, "list", filters] as const,
    detail: (agentId: string) => [...agentsKeys.all, "detail", agentId] as const,
    instances: (agentId: string) => [...agentsKeys.all, "instances", agentId] as const,
};

/** Owner registry for create-agent and ownership filters. */
export const ownersKeys = {
    all: ["owners"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...ownersKeys.all, "list", filters] as const,
};

/** Pending and historical approvals. */
export const approvalsKeys = {
    all: ["approvals"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...approvalsKeys.all, "list", filters] as const,
    detail: (approvalId: string) =>
        [...approvalsKeys.all, "detail", approvalId] as const,
};

/** Command-palette search. */
export const searchKeys = {
    all: ["search"] as const,
    query: (q: string, filters: Record<string, unknown> = {}) =>
        [...searchKeys.all, q, filters] as const,
};

/** Guardrail / posture dashboard. */
export const postureKeys = {
    all: ["posture"] as const,
    summary: (filters: Record<string, unknown> = {}) =>
        [...postureKeys.all, "summary", filters] as const,
};

/** Inventory, spend, and denial dashboards. */
export const dashboardsKeys = {
    all: ["dashboards"] as const,
    summary: (filters: Record<string, unknown> = {}) =>
        [...dashboardsKeys.all, "summary", filters] as const,
    spend: (filters: Record<string, unknown> = {}) =>
        [...dashboardsKeys.all, "spend", filters] as const,
    denials: (filters: Record<string, unknown> = {}) =>
        [...dashboardsKeys.all, "denials", filters] as const,
};

/** Quota usage snapshot. */
export const quotasKeys = {
    all: ["quotas"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...quotasKeys.all, "list", filters] as const,
};

/** Server audit log reads and verification. */
export const auditKeys = {
    all: ["audit"] as const,
    verify: () => [...auditKeys.all, "verify"] as const,
};

/** Admin revocations list (fleet activity). */
export const revocationsKeys = {
    all: ["revocations"] as const,
    list: (filters: Record<string, unknown> = {}) =>
        [...revocationsKeys.all, "list", filters] as const,
};

/** Settings: alerts, webhooks, baselines, custody, admin keys, owners, audit. */
export const settingsKeys = {
    all: ["settings"] as const,
    alerts: () => [...settingsKeys.all, "alerts"] as const,
    webhooks: () => [...settingsKeys.all, "webhooks"] as const,
    baselines: (filters: Record<string, unknown> = {}) =>
        [...settingsKeys.all, "baselines", filters] as const,
    baseline: (name: string) => [...settingsKeys.all, "baseline", name] as const,
    custody: () => [...settingsKeys.all, "custody"] as const,
    adminKeys: () => [...settingsKeys.all, "admin-keys"] as const,
    owners: () => [...settingsKeys.all, "owners"] as const,
    auditLog: (filters: Record<string, unknown> = {}) =>
        [...settingsKeys.all, "audit-log", filters] as const,
};

/** Built-in onboarding policy templates. */
export const policyTemplateKeys = {
    all: ["policy-templates"] as const,
    list: () => [...policyTemplateKeys.all, "list"] as const,
};
