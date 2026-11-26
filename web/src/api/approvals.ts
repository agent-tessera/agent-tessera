/** Approvals list, detail, and resolve helpers for the console inbox. */

import { apiRequest } from "./client";
import { queryString } from "./overview";

/** Stored action document on an approval. */
export type ApprovalAction = {
    kind?: string;
    resource?: string;
    args?: unknown;
    span_id?: string;
    [key: string]: unknown;
};

/** Stored decision document on an approval. */
export type ApprovalDecision = {
    effect?: string;
    reason?: string;
    rule?: string | null;
    policy_version?: string | null;
    [key: string]: unknown;
};

/** Escalation route stored on an approval. */
export type ApprovalRoute = {
    parties?: string[];
    forbid_self_approval?: boolean;
    timeout_seconds?: number;
    [key: string]: unknown;
};

/** One approval from `GET /v1/approvals` / `GET /v1/approvals/{id}`. */
export type Approval = {
    id: string;
    agent_id: string;
    instance_id: string;
    run_id: string | null;
    action: ApprovalAction;
    decision: ApprovalDecision;
    route: ApprovalRoute;
    route_index: number;
    status: string;
    requested_by_principal: string | null;
    resolved_by: string | null;
    expires_at: string;
    created_at: string;
    resolved_at: string | null;
};

/** Paginated approvals list. */
export type ApprovalsPage = {
    items: Approval[];
    next_cursor: string | null;
};

/** Filters for `GET /v1/approvals`. */
export type ApprovalsListFilters = {
    status?: string;
    agent_id?: string;
    limit?: number;
    cursor?: string;
};

/** How many pending rows to fetch for the badge (shows `N+` when truncated). */
export const PENDING_BADGE_LIMIT = 100;

/**
 * List approvals for the inbox.
 *
 * @param filters - Status, agent, pagination.
 */
export async function fetchApprovals(
    filters: ApprovalsListFilters = {},
): Promise<ApprovalsPage> {
    return apiRequest<ApprovalsPage>(
        `/v1/approvals${queryString({
            status: filters.status,
            agent_id: filters.agent_id,
            limit: filters.limit ?? 50,
            cursor: filters.cursor,
        })}`,
    );
}

/**
 * List pending approvals for the nav badge.
 *
 * @param limit - Page size (default {@link PENDING_BADGE_LIMIT}).
 */
export async function fetchPendingApprovals(
    limit: number = PENDING_BADGE_LIMIT,
): Promise<ApprovalsPage> {
    return fetchApprovals({ status: "pending", limit });
}

/**
 * Fetch one approval by id.
 *
 * @param approvalId - Approval id.
 */
export async function fetchApproval(approvalId: string): Promise<Approval> {
    return apiRequest<Approval>(`/v1/approvals/${encodeURIComponent(approvalId)}`);
}

/** Optional comment on approve / deny. */
export type ApprovalCommentBody = {
    comment?: string | null;
};

/**
 * Approve a pending approval.
 *
 * @param approvalId - Approval id.
 * @param body - Optional operator comment.
 */
export async function approveApproval(
    approvalId: string,
    body: ApprovalCommentBody = {},
): Promise<Approval> {
    return apiRequest<Approval>(
        `/v1/approvals/${encodeURIComponent(approvalId)}:approve`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                comment: body.comment?.trim() ? body.comment.trim() : null,
            }),
        },
    );
}

/**
 * Deny a pending approval.
 *
 * @param approvalId - Approval id.
 * @param body - Optional operator comment.
 */
export async function denyApproval(
    approvalId: string,
    body: ApprovalCommentBody = {},
): Promise<Approval> {
    return apiRequest<Approval>(
        `/v1/approvals/${encodeURIComponent(approvalId)}:deny`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                comment: body.comment?.trim() ? body.comment.trim() : null,
            }),
        },
    );
}

/**
 * Format a pending count for the badge.
 *
 * @param page - List response from {@link fetchPendingApprovals}.
 * @returns Visible count, or `N+` when another page exists.
 */
export function formatPendingBadge(page: ApprovalsPage): string | null {
    const count = page.items.length;
    if (count === 0) {
        return null;
    }
    if (page.next_cursor) {
        return `${count}+`;
    }
    return String(count);
}

/**
 * Format rule id and reason for the Why column / detail.
 *
 * @param decision - Stored decision object.
 * @returns `rule — reason`, or whichever part is present.
 */
export function formatApprovalWhy(decision: ApprovalDecision | undefined): string {
    if (!decision) {
        return "";
    }
    const rule = typeof decision.rule === "string" ? decision.rule : "";
    const reason = typeof decision.reason === "string" ? decision.reason : "";
    return [rule, reason].filter(Boolean).join(" — ");
}

/**
 * Estimate UTF-8 byte length of action args for the detail panel.
 *
 * @param action - Stored action object.
 * @returns Byte length of JSON args, or 0.
 */
export function approvalArgsBytes(action: ApprovalAction | undefined): number {
    if (!action) {
        return 0;
    }
    const args = action.args;
    if (args === undefined || args === null) {
        return 0;
    }
    if (typeof args === "number" && Number.isFinite(args)) {
        return Math.max(0, Math.trunc(args));
    }
    try {
        return new TextEncoder().encode(JSON.stringify(args)).length;
    } catch {
        return 0;
    }
}

/**
 * Waiting span id for a trace deep-link (`?span=`).
 *
 * Prefers `action.span_id` when the SDK or seed stored it.
 *
 * @param approval - Approval document.
 */
export function waitingSpanId(approval: Approval): string | undefined {
    const raw = approval.action?.span_id;
    if (typeof raw === "string" && raw.trim()) {
        return raw.trim();
    }
    return undefined;
}

/**
 * Console trace path with an optional span query (string search codec).
 *
 * @param runId - Run id.
 * @param span - Optional waiting span id.
 * @deprecated Prefer `<Link to="/traces/$runId" search={{ span }} />`.
 */
export function approvalTraceHref(runId: string, span?: string): string {
    const base = `/app/traces/${encodeURIComponent(runId)}`;
    if (!span) {
        return base;
    }
    return `${base}?span=${encodeURIComponent(span)}`;
}
