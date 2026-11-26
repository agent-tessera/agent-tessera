import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    type PointerEvent as ReactPointerEvent,
    type WheelEvent as ReactWheelEvent,
} from "react";

import type { TraceSpan } from "@/api/runs";
import { Button } from "@/components/ui/button";
import { Mono } from "@/components/Mono";
import { cn } from "@/lib/utils";
import {
    flattenWaterfallRows,
    outcomeBarClass,
    panView,
    spanOutcome,
    timeToX,
    traceTimeBounds,
    WATERFALL_LABEL_WIDTH,
    WATERFALL_MIN_BAR_PX,
    WATERFALL_ROW_HEIGHT,
    zoomView,
    type WaterfallRow,
} from "@/lib/trace-waterfall";

export type TraceWaterfallProps = {
    spans: TraceSpan[];
    agentNames: Record<string, string>;
    selectedSpanId?: string;
    onSelectSpan?: (spanId: string) => void;
    height?: number;
    className?: string;
};

type ViewWindow = { start: number; end: number };

/**
 * Virtualized governance waterfall with zoom, collapse, and swimlanes.
 */
export function TraceWaterfall({
    spans,
    agentNames,
    selectedSpanId,
    onSelectSpan,
    height = 480,
    className,
}: TraceWaterfallProps) {
    const parentRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<HTMLDivElement>(null);
    const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
    const [chartWidth, setChartWidth] = useState(640);
    const bounds = useMemo(() => traceTimeBounds(spans), [spans]);
    const [view, setView] = useState<ViewWindow>(() => ({
        start: bounds.startMs,
        end: bounds.endMs,
    }));
    const dragRef = useRef<{
        pointerId: number;
        originX: number;
        originView: ViewWindow;
    } | null>(null);

    useEffect(() => {
        setView({ start: bounds.startMs, end: bounds.endMs });
    }, [bounds.startMs, bounds.endMs]);

    useEffect(() => {
        const el = chartRef.current;
        if (!el || typeof ResizeObserver === "undefined") {
            return;
        }
        const ro = new ResizeObserver((entries) => {
            const entry = entries[0];
            if (entry) {
                setChartWidth(Math.max(120, Math.floor(entry.contentRect.width)));
            }
        });
        ro.observe(el);
        setChartWidth(Math.max(120, Math.floor(el.clientWidth)));
        return () => ro.disconnect();
    }, []);

    const rows = useMemo(
        () => flattenWaterfallRows(spans, agentNames, collapsed),
        [spans, agentNames, collapsed],
    );

    const virtualizer = useVirtualizer({
        count: rows.length,
        getScrollElement: () => parentRef.current,
        estimateSize: () => WATERFALL_ROW_HEIGHT,
        overscan: 12,
    });

    useEffect(() => {
        if (!selectedSpanId) {
            return;
        }
        const index = rows.findIndex(
            (row) => row.kind === "span" && row.span.span_id === selectedSpanId,
        );
        if (index < 0) {
            return;
        }
        // Defer so the virtualizer has measured before scrolling.
        const id = window.requestAnimationFrame(() => {
            virtualizer.scrollToIndex(index, { align: "center" });
        });
        return () => window.cancelAnimationFrame(id);
        // eslint-disable-next-line react-hooks/exhaustive-deps -- scroll once per selection
    }, [selectedSpanId, rows.length]);

    const toggleCollapse = useCallback((spanId: string) => {
        setCollapsed((prev) => {
            const next = new Set(prev);
            if (next.has(spanId)) {
                next.delete(spanId);
            } else {
                next.add(spanId);
            }
            return next;
        });
    }, []);

    const onWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
        if (!chartRef.current) {
            return;
        }
        event.preventDefault();
        const rect = chartRef.current.getBoundingClientRect();
        const anchor = (event.clientX - rect.left) / Math.max(rect.width, 1);
        const factor = event.deltaY > 0 ? 1.15 : 1 / 1.15;
        setView((prev) =>
            zoomView(
                prev.start,
                prev.end,
                factor,
                anchor,
                bounds.startMs,
                bounds.endMs,
            ),
        );
    };

    const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) {
            return;
        }
        (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
        dragRef.current = {
            pointerId: event.pointerId,
            originX: event.clientX,
            originView: view,
        };
    };

    const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) {
            return;
        }
        const delta = event.clientX - drag.originX;
        setView(
            panView(
                drag.originView.start,
                drag.originView.end,
                delta,
                chartWidth,
                bounds.startMs,
                bounds.endMs,
            ),
        );
    };

    const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (dragRef.current?.pointerId === event.pointerId) {
            dragRef.current = null;
        }
    };

    const fit = () => setView({ start: bounds.startMs, end: bounds.endMs });

    return (
        <div
            className={cn(
                "border-border bg-panel flex flex-col rounded-md border",
                className,
            )}
            data-testid="trace-waterfall"
            data-selected-span={
                selectedSpanId === undefined || selectedSpanId === ""
                    ? ""
                    : String(selectedSpanId)
            }
        >
            <div className="border-border flex items-center justify-between gap-2 border-b px-3 py-2">
                <p className="text-muted-foreground text-xs">
                    {rows.filter((row) => row.kind === "span").length} spans · scroll
                    wheel zooms · drag pans
                </p>
                <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={fit}
                    data-testid="trace-waterfall-fit"
                >
                    Fit
                </Button>
            </div>
            <div
                className="border-border text-muted-foreground sticky top-0 z-10 grid border-b text-[11px]"
                style={{
                    gridTemplateColumns: `${WATERFALL_LABEL_WIDTH}px 1fr`,
                }}
            >
                <div className="px-3 py-1">Span</div>
                <div className="relative px-2 py-1" ref={chartRef}>
                    <TimeAxis
                        viewStart={view.start}
                        viewEnd={view.end}
                        width={chartWidth}
                    />
                </div>
            </div>
            <div
                ref={parentRef}
                className="overflow-auto"
                style={{ height }}
                data-testid="trace-waterfall-viewport"
                onWheel={onWheel}
            >
                <div
                    style={{
                        height: virtualizer.getTotalSize(),
                        width: "100%",
                        position: "relative",
                    }}
                >
                    {virtualizer.getVirtualItems().map((virtualRow) => {
                        const row = rows[virtualRow.index];
                        if (!row) {
                            return null;
                        }
                        const rowSpanId = row.kind === "span" ? row.span.span_id : "";
                        const wantId =
                            selectedSpanId === undefined || selectedSpanId === ""
                                ? ""
                                : String(selectedSpanId);
                        const spanSelected = rowSpanId !== "" && rowSpanId === wantId;
                        return (
                            <div
                                key={row.key}
                                data-index={virtualRow.index}
                                className="absolute top-0 left-0 w-full"
                                style={{
                                    height: `${virtualRow.size}px`,
                                    transform: `translateY(${virtualRow.start}px)`,
                                }}
                            >
                                <WaterfallRowView
                                    row={row}
                                    collapsed={collapsed.has(
                                        row.kind === "span" ? row.span.span_id : "",
                                    )}
                                    isSelected={spanSelected}
                                    view={view}
                                    chartWidth={chartWidth}
                                    onToggle={toggleCollapse}
                                    onSelect={onSelectSpan}
                                    onPointerDown={onPointerDown}
                                    onPointerMove={onPointerMove}
                                    onPointerUp={onPointerUp}
                                />
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

function TimeAxis({
    viewStart,
    viewEnd,
    width,
}: {
    viewStart: number;
    viewEnd: number;
    width: number;
}) {
    const ticks = 5;
    const items = Array.from({ length: ticks }, (_, i) => {
        const t = viewStart + ((viewEnd - viewStart) * i) / (ticks - 1);
        const x = timeToX(t, viewStart, viewEnd, width);
        return { t, x, label: formatAxisTick(t, viewEnd - viewStart) };
    });
    return (
        <svg width="100%" height={18} aria-hidden className="overflow-visible">
            {items.map((tick) => (
                <text
                    key={tick.t}
                    x={tick.x}
                    y={12}
                    className="fill-muted-foreground"
                    fontSize={10}
                    textAnchor={
                        tick.x < 8 ? "start" : tick.x > width - 8 ? "end" : "middle"
                    }
                >
                    {tick.label}
                </text>
            ))}
        </svg>
    );
}

function formatAxisTick(ts: number, windowMs: number): string {
    const date = new Date(ts);
    if (windowMs < 5_000) {
        return `${date.getUTCSeconds()}.${String(date.getUTCMilliseconds()).padStart(3, "0")}s`;
    }
    if (windowMs < 120_000) {
        return `${String(date.getUTCMinutes()).padStart(2, "0")}:${String(date.getUTCSeconds()).padStart(2, "0")}`;
    }
    return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
}

function WaterfallRowView({
    row,
    collapsed,
    isSelected,
    view,
    chartWidth,
    onToggle,
    onSelect,
    onPointerDown,
    onPointerMove,
    onPointerUp,
}: {
    row: WaterfallRow;
    collapsed: boolean;
    isSelected: boolean;
    view: ViewWindow;
    chartWidth: number;
    onToggle: (spanId: string) => void;
    onSelect?: (spanId: string) => void;
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerUp: (event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
    if (row.kind === "swimlane") {
        return (
            <div
                className="bg-muted/60 text-muted-foreground flex h-full items-center px-3 text-[11px] font-medium tracking-wide uppercase"
                data-testid="trace-swimlane"
                data-agent-id={row.agentId}
            >
                {row.agentName}
            </div>
        );
    }

    const { span, depth, hasChildren } = row;
    const outcome = spanOutcome(span);
    const label = span.resource || span.kind || span.span_id;
    const start = Date.parse(span.start);
    const waitMs = span.approval_wait_ms ?? 0;
    const end = span.end
        ? Date.parse(span.end)
        : start + Math.max(waitMs, span.duration_ms ?? 0, 1);
    const callStart = start + Math.max(0, waitMs);
    const x0 = timeToX(start, view.start, view.end, chartWidth);
    const xWaitEnd = timeToX(callStart, view.start, view.end, chartWidth);
    const x1 = timeToX(end, view.start, view.end, chartWidth);
    const isDeny = outcome === "deny";

    return (
        <div
            className={cn(
                "grid h-full cursor-pointer border-b border-transparent",
                isSelected && "bg-accent-soft",
            )}
            style={{ gridTemplateColumns: `${WATERFALL_LABEL_WIDTH}px 1fr` }}
            data-testid="trace-span-row"
            data-span-id={span.span_id}
            data-selected={isSelected ? "true" : "false"}
            onClick={() => onSelect?.(span.span_id)}
        >
            <div
                className="flex min-w-0 items-center gap-1 px-2 text-xs"
                style={{ paddingLeft: 8 + depth * 12 }}
            >
                {hasChildren ? (
                    <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground shrink-0"
                        aria-label={collapsed ? "Expand subtree" : "Collapse subtree"}
                        data-testid="trace-span-collapse"
                        onClick={(event) => {
                            event.stopPropagation();
                            onToggle(span.span_id);
                        }}
                    >
                        {collapsed ? (
                            <ChevronRight className="size-3.5" aria-hidden />
                        ) : (
                            <ChevronDown className="size-3.5" aria-hidden />
                        )}
                    </button>
                ) : (
                    <span className="inline-block size-3.5 shrink-0" aria-hidden />
                )}
                <span className="truncate" title={label}>
                    {label}
                </span>
                <Mono className="text-muted-foreground hidden shrink-0 text-[10px] sm:inline">
                    {span.span_id.slice(0, 8)}
                </Mono>
            </div>
            <div
                className="relative h-full touch-none select-none"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
            >
                <svg
                    width="100%"
                    height="100%"
                    className="absolute inset-0"
                    aria-hidden
                >
                    <defs>
                        <pattern
                            id="approval-hatch"
                            width="6"
                            height="6"
                            patternUnits="userSpaceOnUse"
                            patternTransform="rotate(45)"
                        >
                            <line
                                x1="0"
                                y1="0"
                                x2="0"
                                y2="6"
                                className="stroke-effect-approval"
                                strokeWidth="2"
                            />
                        </pattern>
                    </defs>
                    {waitMs > 0 && xWaitEnd > x0 ? (
                        <rect
                            x={x0}
                            y={8}
                            width={Math.max(1, xWaitEnd - x0)}
                            height={12}
                            fill="url(#approval-hatch)"
                            className="opacity-80"
                            data-testid="trace-approval-hatch"
                        />
                    ) : null}
                    {isDeny ? (
                        <rect
                            x={x0 - 1}
                            y={4}
                            width={3}
                            height={20}
                            className="fill-effect-deny"
                            data-testid="trace-deny-marker"
                        />
                    ) : (
                        <rect
                            x={waitMs > 0 ? xWaitEnd : x0}
                            y={8}
                            width={Math.max(
                                WATERFALL_MIN_BAR_PX,
                                x1 - (waitMs > 0 ? xWaitEnd : x0),
                            )}
                            height={12}
                            rx={2}
                            className={cn(outcomeBarClass(outcome), "opacity-90")}
                            data-testid="trace-span-bar"
                        />
                    )}
                </svg>
            </div>
        </div>
    );
}
