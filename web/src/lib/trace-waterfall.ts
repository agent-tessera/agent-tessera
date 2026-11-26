/** Pure helpers for the governance trace waterfall and tree. */

import type { TraceAgent, TraceSpan } from "@/api/runs";

/** Row height used by the virtualized waterfall (px). */
export const WATERFALL_ROW_HEIGHT = 28;

/** Left gutter width for labels before the time axis (px). */
export const WATERFALL_LABEL_WIDTH = 220;

/** Minimum solid bar width so short spans stay visible (px). */
export const WATERFALL_MIN_BAR_PX = 2;

export type SpanOutcome = "allow" | "deny" | "require_approval" | "error" | "unknown";

/** Flattened waterfall row: swimlane header or span. */
export type WaterfallRow =
    | {
          kind: "swimlane";
          key: string;
          agentId: string;
          agentName: string;
      }
    | {
          kind: "span";
          key: string;
          span: TraceSpan;
          depth: number;
          hasChildren: boolean;
          childCount: number;
      };

/** Nested tree node for the Tree tab. */
export type TraceTreeNode = {
    span: TraceSpan;
    children: TraceTreeNode[];
    descendantCount: number;
};

/**
 * Classify a span for bar colouring.
 *
 * @param span - Trace span document.
 */
export function spanOutcome(span: TraceSpan): SpanOutcome {
    if (span.status === "error") {
        return "error";
    }
    const effect = span.decision?.effect;
    if (effect === "deny") {
        return "deny";
    }
    if (effect === "require_approval") {
        return "require_approval";
    }
    if (effect === "allow") {
        return "allow";
    }
    return "unknown";
}

/**
 * Parse an ISO timestamp to epoch ms, or `NaN` when invalid.
 *
 * @param value - ISO-8601 string.
 */
export function parseTs(value: string | null | undefined): number {
    if (!value) {
        return Number.NaN;
    }
    return Date.parse(value);
}

/**
 * Inclusive time bounds covering every span (start → end or start alone).
 *
 * @param spans - Trace spans.
 */
export function traceTimeBounds(spans: TraceSpan[]): {
    startMs: number;
    endMs: number;
} {
    let startMs = Number.POSITIVE_INFINITY;
    let endMs = Number.NEGATIVE_INFINITY;
    for (const span of spans) {
        const start = parseTs(span.start);
        if (!Number.isFinite(start)) {
            continue;
        }
        startMs = Math.min(startMs, start);
        let end = parseTs(span.end ?? undefined);
        if (!Number.isFinite(end)) {
            const wait = span.approval_wait_ms ?? 0;
            const duration = span.duration_ms ?? 0;
            end = start + Math.max(wait, duration, 1);
        }
        endMs = Math.max(endMs, end);
    }
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
        const now = Date.now();
        return { startMs: now, endMs: now + 1 };
    }
    if (endMs <= startMs) {
        endMs = startMs + 1;
    }
    return { startMs, endMs };
}

/**
 * Build parent → children adjacency (start-time ordered).
 *
 * @param spans - Trace spans.
 */
export function buildChildMap(spans: TraceSpan[]): Map<string | null, TraceSpan[]> {
    const byId = new Map(spans.map((span) => [span.span_id, span]));
    const children = new Map<string | null, TraceSpan[]>();
    for (const span of spans) {
        const rawParent = span.parent_span_id ?? null;
        const parent = rawParent && byId.has(rawParent) ? rawParent : null;
        const list = children.get(parent) ?? [];
        list.push(span);
        children.set(parent, list);
    }
    for (const list of children.values()) {
        list.sort((a, b) => {
            const da = parseTs(a.start) - parseTs(b.start);
            if (da !== 0) {
                return da;
            }
            return a.span_id.localeCompare(b.span_id);
        });
    }
    return children;
}

/**
 * Nested tree for the Tree tab (roots first).
 *
 * @param spans - Trace spans.
 */
export function buildTraceTree(spans: TraceSpan[]): TraceTreeNode[] {
    const children = buildChildMap(spans);

    const walk = (span: TraceSpan): TraceTreeNode => {
        const kids = (children.get(span.span_id) ?? []).map(walk);
        const descendantCount = kids.reduce(
            (sum, kid) => sum + 1 + kid.descendantCount,
            0,
        );
        return { span, children: kids, descendantCount };
    };

    return (children.get(null) ?? []).map(walk);
}

/**
 * Flatten spans into virtualized waterfall rows with swimlanes and collapse.
 *
 * @param spans - Trace spans.
 * @param agents - Agent id → name map.
 * @param collapsed - Span ids whose subtrees are hidden.
 */
export function flattenWaterfallRows(
    spans: TraceSpan[],
    agents: Record<string, string>,
    collapsed: ReadonlySet<string>,
): WaterfallRow[] {
    const children = buildChildMap(spans);
    const rows: WaterfallRow[] = [];
    let lastAgent: string | null | undefined = undefined;

    const visit = (span: TraceSpan, depth: number) => {
        const agentId = span.agent_id ?? "";
        if (agentId !== lastAgent) {
            rows.push({
                kind: "swimlane",
                key: `swimlane:${agentId}:${rows.length}`,
                agentId,
                agentName: agents[agentId] ?? (agentId || "unknown agent"),
            });
            lastAgent = agentId;
        }
        const kids = children.get(span.span_id) ?? [];
        rows.push({
            kind: "span",
            key: span.span_id,
            span,
            depth,
            hasChildren: kids.length > 0,
            childCount: kids.length,
        });
        if (collapsed.has(span.span_id)) {
            return;
        }
        for (const child of kids) {
            visit(child, depth + 1);
        }
    };

    for (const root of children.get(null) ?? []) {
        visit(root, 0);
    }
    return rows;
}

/**
 * Count nodes under each agent for the Tree tab header.
 *
 * @param spans - Trace spans.
 * @param agents - Trace agent list.
 */
export function agentSpanCounts(
    spans: TraceSpan[],
    agents: TraceAgent[],
): Array<{ agent: TraceAgent; count: number }> {
    const counts = new Map<string, number>();
    for (const span of spans) {
        if (!span.agent_id) {
            continue;
        }
        counts.set(span.agent_id, (counts.get(span.agent_id) ?? 0) + 1);
    }
    return agents
        .map((agent) => ({ agent, count: counts.get(agent.id) ?? 0 }))
        .sort(
            (a, b) =>
                a.agent.name.localeCompare(b.agent.name) ||
                a.agent.id.localeCompare(b.agent.id),
        );
}

/**
 * Map a timestamp into the chart x range for the current view window.
 *
 * @param tsMs - Instant.
 * @param viewStart - Visible window start.
 * @param viewEnd - Visible window end.
 * @param widthPx - Chart width in pixels.
 */
export function timeToX(
    tsMs: number,
    viewStart: number,
    viewEnd: number,
    widthPx: number,
): number {
    const span = Math.max(viewEnd - viewStart, 1);
    return ((tsMs - viewStart) / span) * widthPx;
}

/**
 * Zoom the view window around an anchor (0–1 within the chart).
 *
 * @param viewStart - Current start.
 * @param viewEnd - Current end.
 * @param factor - Zoom factor (`< 1` zooms in).
 * @param anchor - Fraction along the chart (0 left, 1 right).
 * @param fullStart - Full-range start (fit bounds).
 * @param fullEnd - Full-range end.
 */
export function zoomView(
    viewStart: number,
    viewEnd: number,
    factor: number,
    anchor: number,
    fullStart: number,
    fullEnd: number,
): { start: number; end: number } {
    const duration = Math.max(viewEnd - viewStart, 1);
    const nextDuration = Math.min(fullEnd - fullStart, Math.max(1, duration * factor));
    const anchorTs = viewStart + duration * Math.min(1, Math.max(0, anchor));
    let start = anchorTs - nextDuration * Math.min(1, Math.max(0, anchor));
    let end = start + nextDuration;
    if (start < fullStart) {
        start = fullStart;
        end = start + nextDuration;
    }
    if (end > fullEnd) {
        end = fullEnd;
        start = end - nextDuration;
    }
    if (start < fullStart) {
        start = fullStart;
    }
    return { start, end };
}

/**
 * Pan the view window by a pixel delta.
 *
 * @param viewStart - Current start.
 * @param viewEnd - Current end.
 * @param deltaPx - Horizontal drag in pixels (positive = content moves right).
 * @param widthPx - Chart width.
 * @param fullStart - Full-range start.
 * @param fullEnd - Full-range end.
 */
export function panView(
    viewStart: number,
    viewEnd: number,
    deltaPx: number,
    widthPx: number,
    fullStart: number,
    fullEnd: number,
): { start: number; end: number } {
    const duration = Math.max(viewEnd - viewStart, 1);
    const shift = (-deltaPx / Math.max(widthPx, 1)) * duration;
    let start = viewStart + shift;
    let end = viewEnd + shift;
    if (start < fullStart) {
        end += fullStart - start;
        start = fullStart;
    }
    if (end > fullEnd) {
        start -= end - fullEnd;
        end = fullEnd;
    }
    if (start < fullStart) {
        start = fullStart;
    }
    return { start, end };
}

/**
 * CSS class for a span outcome bar fill.
 *
 * @param outcome - Classified outcome.
 */
export function outcomeBarClass(outcome: SpanOutcome): string {
    switch (outcome) {
        case "allow":
            return "fill-effect-allow";
        case "deny":
            return "fill-effect-deny";
        case "require_approval":
            return "fill-effect-approval";
        case "error":
            return "fill-status-error";
        default:
            return "fill-muted-foreground";
    }
}
