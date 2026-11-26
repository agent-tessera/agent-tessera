/**
 * Agent policy editor APIs (`GET/PUT …/policy`, validate, dry-run, templates).
 */

import { apiRequest } from "./client";

/** Result of `POST /v1/agents/{id}/policy:validate`. */
export type PolicyValidateResult = {
    valid: boolean;
    error: string | null;
    diff: string | null;
};

/** One built-in template from `GET /v1/policy-templates`. */
export type PolicyTemplate = {
    id: string;
    title: string;
    blurb: string;
    /** Placeholder YAML (`agent: AGENT`, `environment: ENV`). */
    yaml: string;
};

/** List document from `GET /v1/policy-templates`. */
export type PolicyTemplatesPage = {
    items: PolicyTemplate[];
};

/** Dry-run action kinds accepted by the parity route. */
export type PolicyDryRunKind = "model" | "tool" | "data" | "agent";

/** Result of `POST /v1/agents/{id}/policy:dry-run`. */
export type PolicyDryRunResult = {
    effect: string;
    reason: string;
    rule: string | null;
};

/**
 * Validate draft YAML without saving.
 *
 * @param agentId - Agent id.
 * @param yamlText - Draft document.
 */
export async function validateAgentPolicy(
    agentId: string,
    yamlText: string,
): Promise<PolicyValidateResult> {
    return apiRequest<PolicyValidateResult>(
        `/v1/agents/${encodeURIComponent(agentId)}/policy:validate`,
        {
            method: "POST",
            headers: { "Content-Type": "application/yaml" },
            body: yamlText,
        },
    );
}

/**
 * Replace the stored agent policy.
 *
 * @param agentId - Agent id.
 * @param yamlText - Document to store.
 */
export async function putAgentPolicy(
    agentId: string,
    yamlText: string,
): Promise<{ version: string }> {
    return apiRequest<{ version: string }>(
        `/v1/agents/${encodeURIComponent(agentId)}/policy`,
        {
            method: "PUT",
            headers: { "Content-Type": "application/yaml" },
            body: yamlText,
        },
    );
}

/**
 * Evaluate one action against the stored (not draft) policy.
 *
 * @param agentId - Agent id.
 * @param body - Kind and resource.
 */
export async function dryRunAgentPolicy(
    agentId: string,
    body: { kind: PolicyDryRunKind; resource: string },
): Promise<PolicyDryRunResult> {
    return apiRequest<PolicyDryRunResult>(
        `/v1/agents/${encodeURIComponent(agentId)}/policy:dry-run`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        },
    );
}

/**
 * List built-in onboarding policy templates.
 */
export async function fetchPolicyTemplates(): Promise<PolicyTemplatesPage> {
    return apiRequest<PolicyTemplatesPage>("/v1/policy-templates");
}

/**
 * Apply a built-in template as the agent's policy.
 *
 * @param agentId - Target agent.
 * @param templateId - Template id from `GET /v1/policy-templates`.
 */
export async function applyPolicyTemplate(
    agentId: string,
    templateId: string,
): Promise<{ version?: string }> {
    return apiRequest<{ version?: string }>(
        `/v1/agents/${encodeURIComponent(agentId)}/policy:from-template`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ template_id: templateId }),
        },
    );
}
