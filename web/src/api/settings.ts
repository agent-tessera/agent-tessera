/**
 * Settings APIs: alerts, webhooks, baselines, custody, and admin keys.
 */

import { apiRequest, apiRequestText } from "./client";
import { queryString } from "./overview";

/** Alert metrics accepted by `POST /v1/alerts`. */
export const ALERT_METRICS = [
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "model_calls",
    "tool_calls",
    "denials",
] as const;

export type AlertMetric = (typeof ALERT_METRICS)[number];

/** API action values for alerts (`notify` in the UI maps to `webhook`). */
export type AlertAction = "webhook" | "suspend";

/** One alert rule from `GET /v1/alerts`. */
export type AlertRule = {
    id: string;
    name: string;
    metric: AlertMetric | string;
    threshold: number;
    window: string;
    selector: {
        labels?: Record<string, string>;
        autonomy?: string | null;
    };
    action: AlertAction | string;
};

/** Body for `POST /v1/alerts`. */
export type CreateAlertBody = {
    name: string;
    metric: AlertMetric;
    threshold: number;
    window: string;
    selector?: {
        labels?: Record<string, string>;
        autonomy?: string | null;
    };
    action: AlertAction;
};

/** Event names offered on the webhooks create form (matches classic UI). */
export const WEBHOOK_EVENT_OPTIONS = [
    "approval.requested",
    "approval.escalated",
    "agent.suspended",
    "agent.unsupervised",
    "run.revoked",
    "decision.denied",
    "quota.warning",
    "quota.exhausted",
    "circuit_breaker.tripped",
    "footprint.new_resource",
    "monitor.alert",
    "posture.degraded",
    "webhook.ping",
] as const;

/** One webhook from `GET /v1/webhooks`. */
export type WebhookEndpoint = {
    id: string;
    url: string;
    events: string[];
    active: boolean;
    has_secret: boolean;
    created_at: string;
};

/** Body for `POST /v1/webhooks`. */
export type CreateWebhookBody = {
    url: string;
    events?: string[];
    secret?: string | null;
    active?: boolean;
};

/** Body for `PUT /v1/webhooks/{id}`. */
export type UpdateWebhookBody = {
    url?: string;
    events?: string[];
    secret?: string | null;
    active?: boolean;
    clear_secret?: boolean;
};

/** Baseline list row from `GET /v1/baselines`. */
export type BaselineListItem = {
    name: string;
    sha256: string;
};

/** Custody secret public fields (never plaintext or ciphertext). */
export type CustodySecret = {
    id: string;
    name: string;
    provider: string;
};

/** Body for `POST /v1/custody/secrets`. */
export type CreateCustodyBody = {
    name: string;
    provider: string;
    secret: string;
};

/** Admin key row from `GET /v1/admin-keys` (secret never included). */
export type AdminKeyRow = {
    id: string;
    name: string;
    created_at: string;
    last_used_at: string | null;
    revoked: boolean;
};

/** Create response — secret returned once. */
export type AdminKeyCreated = {
    token: string;
    name: string;
};

/**
 * List alert rules.
 */
export async function fetchAlerts(): Promise<{ items: AlertRule[] }> {
    return apiRequest<{ items: AlertRule[] }>("/v1/alerts");
}

/**
 * Create an alert rule.
 *
 * @param body - Name, metric, threshold, window, selector, action.
 */
export async function createAlert(body: CreateAlertBody): Promise<AlertRule> {
    return apiRequest<AlertRule>("/v1/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/**
 * Delete an alert rule.
 *
 * @param alertId - Rule id.
 */
export async function deleteAlert(alertId: string): Promise<{ id: string }> {
    return apiRequest<{ id: string }>(`/v1/alerts/${encodeURIComponent(alertId)}`, {
        method: "DELETE",
    });
}

/**
 * List webhook endpoints.
 */
export async function fetchWebhooks(): Promise<{ items: WebhookEndpoint[] }> {
    return apiRequest<{ items: WebhookEndpoint[] }>("/v1/webhooks");
}

/**
 * Create a webhook endpoint (secret is write-only).
 *
 * @param body - URL, events, optional secret.
 */
export async function createWebhook(body: CreateWebhookBody): Promise<WebhookEndpoint> {
    return apiRequest<WebhookEndpoint>("/v1/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            url: body.url,
            events: body.events ?? [],
            secret: body.secret?.trim() ? body.secret : null,
            active: body.active ?? true,
        }),
    });
}

/**
 * Update a webhook endpoint (`PUT /v1/webhooks/{id}`).
 *
 * @param webhookId - Endpoint id.
 * @param body - Fields to replace; omitted fields stay as they are.
 */
export async function updateWebhook(
    webhookId: string,
    body: UpdateWebhookBody,
): Promise<WebhookEndpoint> {
    const payload: Record<string, unknown> = {};
    if (body.url !== undefined) {
        payload.url = body.url;
    }
    if (body.events !== undefined) {
        payload.events = body.events;
    }
    if (body.secret !== undefined) {
        payload.secret = body.secret?.trim() ? body.secret : null;
    }
    if (body.active !== undefined) {
        payload.active = body.active;
    }
    if (body.clear_secret) {
        payload.clear_secret = true;
    }
    return apiRequest<WebhookEndpoint>(
        `/v1/webhooks/${encodeURIComponent(webhookId)}`,
        {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

/**
 * Delete a webhook endpoint.
 *
 * @param webhookId - Endpoint id.
 */
export async function deleteWebhook(webhookId: string): Promise<{ id: string }> {
    return apiRequest<{ id: string }>(`/v1/webhooks/${encodeURIComponent(webhookId)}`, {
        method: "DELETE",
    });
}

/**
 * Deliver a signed `webhook.ping` to one endpoint.
 *
 * @param webhookId - Endpoint id.
 */
export async function pingWebhook(webhookId: string): Promise<{ ok: boolean }> {
    return apiRequest<{ ok: boolean }>(
        `/v1/webhooks/${encodeURIComponent(webhookId)}:ping`,
        { method: "POST" },
    );
}

/**
 * List baseline names.
 *
 * @param filters - Pagination.
 */
export async function fetchBaselines(
    filters: { limit?: number; cursor?: string } = {},
): Promise<{ items: BaselineListItem[]; next_cursor: string | null }> {
    return apiRequest<{ items: BaselineListItem[]; next_cursor: string | null }>(
        `/v1/baselines${queryString({
            limit: filters.limit ?? 100,
            cursor: filters.cursor,
        })}`,
    );
}

/**
 * Load one baseline YAML document.
 *
 * @param name - Baseline name.
 */
export async function fetchBaselineYaml(name: string): Promise<string> {
    return apiRequestText(`/v1/baselines/${encodeURIComponent(name)}`);
}

/**
 * Create or replace a baseline YAML document.
 *
 * @param name - Baseline name.
 * @param yamlText - Document body.
 */
export async function putBaselineYaml(name: string, yamlText: string): Promise<string> {
    return apiRequestText(`/v1/baselines/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/yaml" },
        body: yamlText,
    });
}

/**
 * Delete a baseline.
 *
 * @param name - Baseline name.
 */
export async function deleteBaseline(name: string): Promise<void> {
    await apiRequest(`/v1/baselines/${encodeURIComponent(name)}`, {
        method: "DELETE",
    });
}

/** Agent that matches a baseline selector. */
export type BaselineAgent = {
    id: string;
    name: string;
    environment: string;
    status: string;
};

/** Dry-run impact row for one agent policy. */
export type BaselineDryRunItem = {
    agent_id: string;
    name: string;
    environment: string;
    ok: boolean;
    would_change: boolean;
    current_version: string | null;
    new_version: string | null;
    error: string | null;
};

/**
 * List agents that inherit a baseline via its selector.
 *
 * @param name - Baseline name.
 */
export async function fetchBaselineAgents(
    name: string,
): Promise<{ items: BaselineAgent[] }> {
    return apiRequest<{ items: BaselineAgent[] }>(
        `/v1/baselines/${encodeURIComponent(name)}/agents`,
    );
}

/**
 * Validate baseline YAML without writing.
 *
 * @param name - Baseline name.
 * @param yamlText - Candidate document.
 */
export async function validateBaselineYaml(
    name: string,
    yamlText: string,
): Promise<{ ok: boolean; name: string; sha256: string }> {
    return apiRequest<{ ok: boolean; name: string; sha256: string }>(
        `/v1/baselines/${encodeURIComponent(name)}:validate`,
        {
            method: "POST",
            headers: { "Content-Type": "application/yaml" },
            body: yamlText,
        },
    );
}

/**
 * Preview baseline replace impact without writing.
 *
 * @param name - Baseline name.
 * @param yamlText - Candidate document.
 */
export async function dryRunBaselineYaml(
    name: string,
    yamlText: string,
): Promise<{ matching_agents: number; items: BaselineDryRunItem[] }> {
    return apiRequest<{ matching_agents: number; items: BaselineDryRunItem[] }>(
        `/v1/baselines/${encodeURIComponent(name)}:dry-run`,
        {
            method: "POST",
            headers: { "Content-Type": "application/yaml" },
            body: yamlText,
        },
    );
}

/**
 * List custody secrets (public fields only).
 */
export async function fetchCustodySecrets(): Promise<{
    items: CustodySecret[];
}> {
    return apiRequest<{ items: CustodySecret[] }>("/v1/custody/secrets");
}

/**
 * Store a provider key. Plaintext is never returned.
 *
 * @param body - Name, provider, and secret.
 */
export async function createCustodySecret(
    body: CreateCustodyBody,
): Promise<CustodySecret> {
    return apiRequest<CustodySecret>("/v1/custody/secrets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

/**
 * Replace a custody secret's plaintext (and optional labels).
 *
 * @param secretId - Row id.
 * @param body - Fields to update; omit unchanged fields.
 */
export async function updateCustodySecret(
    secretId: string,
    body: { name?: string; provider?: string; secret?: string },
): Promise<CustodySecret> {
    return apiRequest<CustodySecret>(
        `/v1/custody/secrets/${encodeURIComponent(secretId)}`,
        {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        },
    );
}

/**
 * Delete a custody secret.
 *
 * @param secretId - Row id.
 */
export async function deleteCustodySecret(secretId: string): Promise<{ id: string }> {
    return apiRequest<{ id: string }>(
        `/v1/custody/secrets/${encodeURIComponent(secretId)}`,
        { method: "DELETE" },
    );
}

/**
 * List admin keys (secrets never included).
 */
export async function fetchAdminKeys(): Promise<{ items: AdminKeyRow[] }> {
    return apiRequest<{ items: AdminKeyRow[] }>("/v1/admin-keys");
}

/**
 * Create an admin key; the secret is returned once.
 *
 * @param name - Label for the key.
 */
export async function createAdminKey(name: string): Promise<AdminKeyCreated> {
    return apiRequest<AdminKeyCreated>("/v1/admin-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    });
}

/**
 * Revoke an admin key and end its UI sessions.
 *
 * @param keyId - Admin key id.
 */
export async function revokeAdminKey(
    keyId: string,
): Promise<{ id: string; revoked: boolean }> {
    return apiRequest<{ id: string; revoked: boolean }>(
        `/v1/admin-keys/${encodeURIComponent(keyId)}:revoke`,
        { method: "POST" },
    );
}
