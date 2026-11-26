import { describe, expect, it } from "vitest";

import type { TraceSpan } from "@/api/runs";
import {
    buildTraceTree,
    flattenWaterfallRows,
    panView,
    spanOutcome,
    traceTimeBounds,
    zoomView,
} from "./trace-waterfall";

function span(
    id: string,
    opts: Partial<TraceSpan> & { start?: string; effect?: string } = {},
): TraceSpan {
    const { effect, start, end, duration_ms, approval_wait_ms, status, ...rest } = opts;
    return {
        span_id: id,
        parent_span_id: null,
        agent_id: "a1",
        kind: "tool",
        resource: "files.read",
        start: start ?? "2026-11-09T18:00:00Z",
        end: end ?? null,
        duration_ms: duration_ms ?? null,
        approval_wait_ms: approval_wait_ms ?? null,
        status: status ?? "ok",
        decision: { effect: effect ?? "allow", rule: "r", reason: null },
        effects: [],
        event_ids: [],
        ...rest,
    };
}

describe("spanOutcome", () => {
    it("prefers error status over effect", () => {
        expect(
            spanOutcome(
                span("1", {
                    start: "2026-11-09T18:00:00Z",
                    status: "error",
                    effect: "allow",
                }),
            ),
        ).toBe("error");
    });

    it("maps deny and approval effects", () => {
        expect(
            spanOutcome(span("1", { start: "2026-11-09T18:00:00Z", effect: "deny" })),
        ).toBe("deny");
        expect(
            spanOutcome(
                span("1", {
                    start: "2026-11-09T18:00:00Z",
                    effect: "require_approval",
                }),
            ),
        ).toBe("require_approval");
    });
});

describe("flattenWaterfallRows", () => {
    it("indents children and inserts swimlanes on agent change", () => {
        const spans = [
            span("root", {
                start: "2026-11-09T18:00:00Z",
                end: "2026-11-09T18:00:02Z",
                duration_ms: 2000,
                agent_id: "a1",
            }),
            span("child", {
                start: "2026-11-09T18:00:01Z",
                parent_span_id: "root",
                agent_id: "a2",
                effect: "deny",
            }),
        ];
        const rows = flattenWaterfallRows(
            spans,
            { a1: "alpha", a2: "beta" },
            new Set(),
        );
        expect(rows.map((row) => row.kind)).toEqual([
            "swimlane",
            "span",
            "swimlane",
            "span",
        ]);
        const child = rows[3];
        expect(child).toBeDefined();
        expect(child!.kind).toBe("span");
        if (child?.kind === "span") {
            expect(child.depth).toBe(1);
            expect(child.span.span_id).toBe("child");
        }
    });

    it("hides descendants when a parent is collapsed", () => {
        const spans = [
            span("root", { start: "2026-11-09T18:00:00Z", agent_id: "a1" }),
            span("child", {
                start: "2026-11-09T18:00:01Z",
                parent_span_id: "root",
                agent_id: "a1",
            }),
        ];
        const rows = flattenWaterfallRows(spans, { a1: "alpha" }, new Set(["root"]));
        expect(rows.filter((row) => row.kind === "span")).toHaveLength(1);
    });
});

describe("buildTraceTree", () => {
    it("counts descendants", () => {
        const tree = buildTraceTree([
            span("root", { start: "2026-11-09T18:00:00Z" }),
            span("a", {
                start: "2026-11-09T18:00:01Z",
                parent_span_id: "root",
            }),
            span("b", {
                start: "2026-11-09T18:00:02Z",
                parent_span_id: "a",
            }),
        ]);
        expect(tree).toHaveLength(1);
        expect(tree[0]?.descendantCount).toBe(2);
        expect(tree[0]?.children[0]?.descendantCount).toBe(1);
    });
});

describe("traceTimeBounds", () => {
    it("covers approval wait and duration", () => {
        const bounds = traceTimeBounds([
            span("1", {
                start: "2026-11-09T18:00:00Z",
                end: "2026-11-09T18:00:05Z",
                duration_ms: 5000,
                approval_wait_ms: 2000,
            }),
        ]);
        expect(bounds.startMs).toBe(Date.parse("2026-11-09T18:00:00Z"));
        expect(bounds.endMs).toBe(Date.parse("2026-11-09T18:00:05Z"));
    });
});

describe("zoomView and panView", () => {
    it("zooms in around the anchor without leaving the full range", () => {
        const next = zoomView(0, 1000, 0.5, 0.5, 0, 1000);
        expect(next.end - next.start).toBe(500);
        expect(next.start).toBeGreaterThanOrEqual(0);
        expect(next.end).toBeLessThanOrEqual(1000);
    });

    it("pans within bounds", () => {
        const next = panView(100, 300, 50, 200, 0, 1000);
        expect(next.end - next.start).toBe(200);
        expect(next.start).toBeGreaterThanOrEqual(0);
        expect(next.end).toBeLessThanOrEqual(1000);
    });
});
