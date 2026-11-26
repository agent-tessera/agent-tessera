import { render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import type { TraceSpan } from "@/api/runs";
import { TraceWaterfall } from "./TraceWaterfall";

beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
        configurable: true,
        value: 480,
    });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
        configurable: true,
        value: 900,
    });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
        configurable: true,
        value: 480,
    });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
        configurable: true,
        value: 680,
    });
    class ResizeObserverStub {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
    }
    Object.defineProperty(window, "ResizeObserver", {
        configurable: true,
        writable: true,
        value: ResizeObserverStub,
    });
});

function makeSpans(count: number): TraceSpan[] {
    const start = Date.parse("2026-11-09T18:00:00Z");
    return Array.from({ length: count }, (_, index) => {
        const id = index.toString(16).padStart(16, "0");
        const stamp = new Date(start + index * 10).toISOString();
        return {
            span_id: id,
            parent_span_id: null,
            agent_id: "a1",
            kind: "tool",
            resource: `tool.${index}`,
            start: stamp,
            end: new Date(start + index * 10 + 5).toISOString(),
            duration_ms: 5,
            approval_wait_ms: null,
            status: "ok",
            decision: { effect: "allow", rule: "r", reason: null },
            effects: [],
            event_ids: [],
        };
    });
}

describe("TraceWaterfall", () => {
    it("virtualizes 5,000 spans to fewer than 100 DOM rows", () => {
        const spans = makeSpans(5_000);
        render(
            <TraceWaterfall spans={spans} agentNames={{ a1: "alpha" }} height={480} />,
        );
        const viewport = screen.getByTestId("trace-waterfall-viewport");
        const rows = within(viewport).getAllByTestId("trace-span-row");
        expect(rows.length).toBeLessThan(100);
        expect(rows.length).toBeGreaterThan(0);
    });
});
