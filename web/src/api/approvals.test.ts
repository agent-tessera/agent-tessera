import { describe, expect, it } from "vitest";

import {
    approvalArgsBytes,
    approvalTraceHref,
    formatApprovalWhy,
    formatPendingBadge,
    waitingSpanId,
    type Approval,
} from "./approvals";

describe("formatPendingBadge", () => {
    it("returns null when there are no pending items", () => {
        expect(formatPendingBadge({ items: [], next_cursor: null })).toBeNull();
    });

    it("returns the exact count when the page is complete", () => {
        expect(
            formatPendingBadge({
                items: [stubApproval("1"), stubApproval("2")],
                next_cursor: null,
            }),
        ).toBe("2");
    });

    it("returns N+ when another page exists", () => {
        expect(
            formatPendingBadge({
                items: Array.from({ length: 100 }, (_, i) => stubApproval(String(i))),
                next_cursor: "next",
            }),
        ).toBe("100+");
    });
});

describe("formatApprovalWhy", () => {
    it("joins rule and reason", () => {
        expect(
            formatApprovalWhy({
                rule: "agent:tools.require_approval[0]",
                reason: "approval_rule",
            }),
        ).toBe("agent:tools.require_approval[0] — approval_rule");
    });

    it("returns a single part when the other is missing", () => {
        expect(formatApprovalWhy({ reason: "approval_rule" })).toBe("approval_rule");
        expect(formatApprovalWhy({ rule: "r1" })).toBe("r1");
        expect(formatApprovalWhy({})).toBe("");
    });
});

describe("approvalArgsBytes", () => {
    it("measures JSON args", () => {
        expect(approvalArgsBytes({ args: { amount: 10 } })).toBe(
            new TextEncoder().encode(JSON.stringify({ amount: 10 })).length,
        );
    });

    it("returns 0 when args are absent", () => {
        expect(approvalArgsBytes({})).toBe(0);
        expect(approvalArgsBytes(undefined)).toBe(0);
    });
});

describe("waitingSpanId", () => {
    it("reads action.span_id", () => {
        expect(waitingSpanId(stubApproval("a", { span_id: "3333333333333333" }))).toBe(
            "3333333333333333",
        );
        expect(waitingSpanId(stubApproval("a"))).toBeUndefined();
    });
});

describe("approvalTraceHref", () => {
    it("uses a raw span query without JSON quotes", () => {
        expect(approvalTraceHref("run-1", "3333333333333333")).toBe(
            "/app/traces/run-1?span=3333333333333333",
        );
        expect(approvalTraceHref("run-1")).toBe("/app/traces/run-1");
    });
});

function stubApproval(id: string, action: { span_id?: string } = {}): Approval {
    return {
        id,
        agent_id: "ag",
        instance_id: "ii",
        run_id: null,
        action,
        decision: {},
        route: { parties: [] },
        route_index: 0,
        status: "pending",
        requested_by_principal: null,
        resolved_by: null,
        expires_at: "2026-11-10T00:00:00Z",
        created_at: "2026-11-10T00:00:00Z",
        resolved_at: null,
    };
}
