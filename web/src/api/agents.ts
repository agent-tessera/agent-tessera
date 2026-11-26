/**
 * Agent registry fetchers (`GET/POST /v1/agents`, lifecycle, instances).
 */

import {
    apiRequest,
    clearCsrfToken,
    redirectToLogin,
    TesseraApiError,
    appPathForLoginNext,
    type ApiErrorBody,
} from "./client";
import { queryString } from "./overview";

/** Agent list/detail document from `GET /v1/agents`. */
export type Agent = {
    id: string;
    name: string;
    environment: string;
    description: string;
    owner_id: string;
    status: string;
    risk_tier: string;
    labels: Record<string, string>;
    autonomy: string;
    supervisors: string[];
    created_at: string;
    last_seen_at: string | null;
    orphaned: boolean;
    policy_version?: string | null;
    instances?: AgentInstance[];
};

/** Instance row from agent detail or `GET /v1/agents/{id}/instances`. */
export type AgentInstance = {
    id: string;
    host: string;
    runtime: Record<string, unknown> | string;
    bound_supervisor?: string | null;
    started_at: string;
    last_heartbeat_at: string;
    revoked: boolean;
};

/** Paged instances list. */
export type InstancesPage = {
    items: AgentInstance[];
    next_cursor: string | null;
};

/** Single-agent posture from `GET /v1/agents/{id}/posture`. */
export type AgentPosture = {
    score: number;
    coverage: number;
    components: {
        owner: number;
        guardrails_declared: number;
        coverage: number;
        denial_rate: number;
        ungoverned: number;
        probes: number;
    };
};

/** Footprint totals from `GET /v1/footprint`. */
export type FootprintTotals = {
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    model_calls: number;
    tool_calls: number;
    denials: number;
    energy_j?: number | null;
    co2e_g?: number | null;
    estimate?: boolean;
};

/** Optional operator reason for lifecycle / revoke. */
export type ReasonBody = {
    reason?: string | null;
};

/** Paged agent list. */
export type AgentsPage = {
    items: Agent[];
    next_cursor: string | null;
};

/** Owner document from `GET /v1/owners`. */
export type Owner = {
    id: string;
    type: string;
    email: string;
    display_name: string;
    active: boolean;
    created_at: string;
};

/** Paged owner list. */
export type OwnersPage = {
    items: Owner[];
    next_cursor: string | null;
};

/** Body of `POST /v1/agents`. */
export type CreateAgentBody = {
    name: string;
    environment: string;
    owner_id: string;
    description?: string;
    risk_tier?: "low" | "medium" | "high";
    labels?: Record<string, string>;
    autonomy?: "assistive" | "interactive" | "supervised" | "autonomous";
    supervisors?: string[];
};

/** Body of `POST /v1/agents/{id}/registration-tokens`. */
export type CreateRegistrationTokenBody = {
    reusable?: boolean;
    expires_in?: number | null;
};

/** One-shot registration token from the create endpoint. */
export type RegistrationTokenCreated = {
    id: string;
    token: string;
};

/** Filters accepted by `GET /v1/agents`. */
export type AgentsQueryFilters = {
    environment?: string;
    status?: string;
    owner_id?: string;
    orphaned?: boolean;
    limit?: number;
    cursor?: string;
};

/**
 * List agents with optional registry filters and cursor pagination.
 *
 * @param filters - Environment, status, owner, orphaned, and page size.
 */
export async function fetchAgents(
    filters: AgentsQueryFilters = {},
): Promise<AgentsPage> {
    return apiRequest<AgentsPage>(
        `/v1/agents${queryString({
            environment: filters.environment,
            status: filters.status,
            owner_id: filters.owner_id,
            orphaned: filters.orphaned,
            limit: filters.limit ?? 100,
            cursor: filters.cursor,
        })}`,
    );
}

/**
 * Register a draft agent (`POST /v1/agents`).
 *
 * @param body - Agent fields matching `_AgentBody`.
 */
export async function createAgent(body: CreateAgentBody): Promise<Agent> {
    return apiRequest<Agent>("/v1/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/**
 * Mint a registration token (`POST /v1/agents/{id}/registration-tokens`).
 *
 * The plaintext `token` is returned once; store it only in UI state.
 *
 * @param agentId - Agent the token registers.
 * @param body - Reuse and optional expiry in seconds.
 */
export async function createRegistrationToken(
    agentId: string,
    body: CreateRegistrationTokenBody = {},
): Promise<RegistrationTokenCreated> {
    return apiRequest<RegistrationTokenCreated>(
        `/v1/agents/${encodeURIComponent(agentId)}/registration-tokens`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                reusable: body.reusable ?? false,
                expires_in: body.expires_in ?? null,
            }),
        },
    );
}

/**
 * List owners for the create-agent owner select.
 *
 * @param filters - Optional email exact match and page size.
 */
export async function fetchOwners(
    filters: {
        email?: string;
        limit?: number;
        cursor?: string;
    } = {},
): Promise<OwnersPage> {
    return apiRequest<OwnersPage>(
        `/v1/owners${queryString({
            email: filters.email,
            limit: filters.limit ?? 200,
            cursor: filters.cursor,
        })}`,
    );
}

/** Body of `POST /v1/owners`. */
export type CreateOwnerBody = {
    type: "user" | "team";
    email: string;
    display_name: string;
};

/**
 * Register an owner (`POST /v1/owners`).
 *
 * @param body - Owner type, email, and display name.
 */
export async function createOwner(body: CreateOwnerBody): Promise<Owner> {
    return apiRequest<Owner>("/v1/owners", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/** Body of `PATCH /v1/owners/{id}`. */
export type PatchOwnerBody = {
    display_name?: string;
    active?: boolean;
};

/**
 * Update an owner (`PATCH /v1/owners/{id}`).
 *
 * @param ownerId - Owner id.
 * @param body - Fields to change (`display_name` and/or `active`).
 */
export async function patchOwner(
    ownerId: string,
    body: PatchOwnerBody,
): Promise<Owner> {
    return apiRequest<Owner>(`/v1/owners/${encodeURIComponent(ownerId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/**
 * Load one agent (`GET /v1/agents/{id}`), including recent instances.
 *
 * @param agentId - Agent id.
 */
export async function fetchAgent(agentId: string): Promise<Agent> {
    return apiRequest<Agent>(`/v1/agents/${encodeURIComponent(agentId)}`);
}

/**
 * List instances for one agent.
 *
 * @param agentId - Agent id.
 * @param filters - Optional page size and cursor.
 */
export async function fetchAgentInstances(
    agentId: string,
    filters: { limit?: number; cursor?: string } = {},
): Promise<InstancesPage> {
    return apiRequest<InstancesPage>(
        `/v1/agents/${encodeURIComponent(agentId)}/instances${queryString({
            limit: filters.limit ?? 100,
            cursor: filters.cursor,
        })}`,
    );
}

/**
 * Score one agent (`GET /v1/agents/{id}/posture`).
 *
 * @param agentId - Agent id.
 */
export async function fetchAgentPosture(agentId: string): Promise<AgentPosture> {
    return apiRequest<AgentPosture>(
        `/v1/agents/${encodeURIComponent(agentId)}/posture`,
    );
}

/**
 * Fetch footprint totals, optionally scoped to one agent and time window.
 *
 * @param filters - Agent id and ISO bounds (`from` / `to`).
 */
export async function fetchFootprint(
    filters: {
        agent_id?: string;
        from?: string;
        to?: string;
    } = {},
): Promise<FootprintTotals> {
    return apiRequest<FootprintTotals>(
        `/v1/footprint${queryString({
            agent_id: filters.agent_id,
            from: filters.from,
            to: filters.to,
        })}`,
    );
}

/** Declared grant row from access footprint. */
export type AccessFootprintPattern = {
    kind: string;
    pattern: string;
    rule?: string;
};

/** Observed or denied resource row from access footprint. */
export type AccessFootprintResource = {
    kind: string;
    resource: string;
    count: number;
};

/** `GET /v1/agents/{id}/footprint/access` report (30-day window). */
export type AccessFootprint = {
    declared: AccessFootprintPattern[];
    observed: AccessFootprintResource[];
    unused: AccessFootprintPattern[];
    denied: AccessFootprintResource[];
};

/**
 * Compare declared grants with what the agent used
 * (`GET /v1/agents/{id}/footprint/access`).
 *
 * @param agentId - Agent id.
 */
export async function fetchAccessFootprint(agentId: string): Promise<AccessFootprint> {
    return apiRequest<AccessFootprint>(
        `/v1/agents/${encodeURIComponent(agentId)}/footprint/access`,
    );
}

/**
 * Load stored agent policy YAML (empty string when 404 / missing).
 *
 * @param agentId - Agent id.
 */
export async function fetchAgentPolicyYaml(agentId: string): Promise<string> {
    const response = await fetch(`/v1/agents/${encodeURIComponent(agentId)}/policy`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/yaml, text/plain, */*" },
    });
    if (response.status === 401) {
        redirectToLogin(appPathForLoginNext());
        throw new TesseraApiError(401, "unauthorized", "authentication required");
    }
    if (response.status === 404) {
        return "";
    }
    if (!response.ok) {
        let body: ApiErrorBody | null = null;
        try {
            body = (await response.json()) as ApiErrorBody;
        } catch {
            body = null;
        }
        const code = body?.error?.code ?? "http_error";
        const message = body?.error?.message ?? `HTTP ${response.status}`;
        if (response.status === 403 && code === "csrf_failed") {
            clearCsrfToken();
        }
        throw new TesseraApiError(response.status, code, message);
    }
    return response.text();
}

/**
 * Count pending approvals for one agent.
 *
 * @param agentId - Agent id.
 */
export async function fetchAgentPendingApprovalsCount(
    agentId: string,
): Promise<number> {
    const page = await apiRequest<{ items: unknown[]; next_cursor: string | null }>(
        `/v1/approvals${queryString({
            status: "pending",
            agent_id: agentId,
            limit: 100,
        })}`,
    );
    return page.items.length;
}

function reasonPayload(body: ReasonBody = {}): string {
    return JSON.stringify({
        reason: body.reason?.trim() ? body.reason.trim() : null,
    });
}

/** Body of `PATCH /v1/agents/{id}`. */
export type PatchAgentBody = {
    description?: string;
    owner_id?: string;
    risk_tier?: "low" | "medium" | "high";
    labels?: Record<string, string>;
    autonomy?: "assistive" | "interactive" | "supervised" | "autonomous";
    supervisors?: string[];
};

/**
 * Update agent metadata (`PATCH /v1/agents/{id}`).
 *
 * @param agentId - Agent id.
 * @param body - Fields to change.
 */
export async function patchAgent(
    agentId: string,
    body: PatchAgentBody,
): Promise<Agent> {
    return apiRequest<Agent>(`/v1/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/**
 * Activate an agent (`POST /v1/agents/{id}:activate`).
 *
 * @param agentId - Agent id.
 * @param body - Optional operator reason.
 */
export async function activateAgent(
    agentId: string,
    body: ReasonBody = {},
): Promise<Agent> {
    return apiRequest<Agent>(`/v1/agents/${encodeURIComponent(agentId)}:activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: reasonPayload(body),
    });
}

/**
 * Suspend an agent (`POST /v1/agents/{id}:suspend`).
 *
 * @param agentId - Agent id.
 * @param body - Optional operator reason.
 */
export async function suspendAgent(
    agentId: string,
    body: ReasonBody = {},
): Promise<Agent> {
    return apiRequest<Agent>(`/v1/agents/${encodeURIComponent(agentId)}:suspend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: reasonPayload(body),
    });
}

/**
 * Retire an agent (`POST /v1/agents/{id}:retire`).
 *
 * @param agentId - Agent id.
 * @param body - Optional operator reason.
 */
export async function retireAgent(
    agentId: string,
    body: ReasonBody = {},
): Promise<Agent> {
    return apiRequest<Agent>(`/v1/agents/${encodeURIComponent(agentId)}:retire`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: reasonPayload(body),
    });
}

/**
 * Revoke an instance (`POST /v1/instances/{id}:revoke`).
 *
 * @param instanceId - Instance id.
 * @param body - Optional operator reason.
 */
export async function revokeInstance(
    instanceId: string,
    body: ReasonBody = {},
): Promise<{ id?: string; kind?: string }> {
    return apiRequest(`/v1/instances/${encodeURIComponent(instanceId)}:revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: reasonPayload(body),
    });
}
