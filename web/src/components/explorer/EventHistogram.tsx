import { useMemo, useRef, useState } from "react";

import type { EventHistogram as EventHistogramDoc } from "@/api/overview";
import {
    brushToTimeRange,
    stackBucket,
    type StackedBucket,
} from "@/lib/explorer-histogram";
import { cn } from "@/lib/utils";

export type EventHistogramProps = {
    data: EventHistogramDoc | undefined;
    loading?: boolean;
    onBrush: (range: { since: string; until: string }) => void;
    className?: string;
};

const HEIGHT = 96;
const PAD_Y = 4;

const LAYERS: Array<{
    key: keyof Pick<StackedBucket, "allow" | "approval" | "deny" | "error">;
    className: string;
    label: string;
}> = [
    { key: "allow", className: "fill-effect-allow", label: "allow" },
    { key: "approval", className: "fill-effect-approval", label: "approval" },
    { key: "deny", className: "fill-effect-deny", label: "deny" },
    { key: "error", className: "fill-status-error", label: "error" },
];

/**
 * Stacked event histogram with drag-to-zoom brush.
 *
 * Brushing a range calls `onBrush` with absolute `since` / `until` ISO times
 * for the URL.
 */
export function EventHistogram({
    data,
    loading = false,
    onBrush,
    className,
}: EventHistogramProps) {
    const svgRef = useRef<SVGSVGElement>(null);
    const [brush, setBrush] = useState<{ x0: number; x1: number } | null>(null);
    const dragging = useRef(false);

    const stacked = useMemo(
        () => (data?.series ?? []).map((bucket) => stackBucket(bucket)),
        [data],
    );
    const maxStack = useMemo(() => {
        let max = 1;
        for (const bucket of stacked) {
            const height = bucket.allow + bucket.approval + bucket.deny + bucket.error;
            if (height > max) {
                max = height;
            }
        }
        return max;
    }, [stacked]);

    const width = Math.max(stacked.length * 8, 240);
    const barGap = stacked.length > 80 ? 0 : 1;
    const barWidth =
        stacked.length > 0
            ? Math.max(1, (width - barGap * stacked.length) / stacked.length)
            : 0;

    const pointerX = (clientX: number): number => {
        const svg = svgRef.current;
        if (!svg) {
            return 0;
        }
        const rect = svg.getBoundingClientRect();
        const scale = width / Math.max(rect.width, 1);
        return (clientX - rect.left) * scale;
    };

    const commitBrush = (x0: number, x1: number) => {
        if (!data) {
            return;
        }
        const range = brushToTimeRange({
            x0,
            x1,
            width,
            from: data.from,
            to: data.to,
        });
        if (range) {
            onBrush(range);
        }
    };

    return (
        <div
            className={cn("border-border bg-panel rounded-md border p-3", className)}
            data-testid="explorer-histogram"
        >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                    Histogram
                </p>
                <ul className="text-muted-foreground flex flex-wrap gap-3 text-[11px]">
                    {LAYERS.map((layer) => (
                        <li key={layer.key} className="flex items-center gap-1">
                            <span
                                className={cn(
                                    "inline-block size-2 rounded-sm",
                                    layer.key === "allow" && "bg-effect-allow",
                                    layer.key === "approval" && "bg-effect-approval",
                                    layer.key === "deny" && "bg-effect-deny",
                                    layer.key === "error" && "bg-status-error",
                                )}
                                aria-hidden
                            />
                            {layer.label}
                        </li>
                    ))}
                </ul>
            </div>
            {loading && stacked.length === 0 ? (
                <div
                    className="bg-muted h-24 animate-pulse rounded-md"
                    data-testid="explorer-histogram-loading"
                />
            ) : stacked.length === 0 ? (
                <p
                    className="text-muted-foreground flex h-24 items-center justify-center text-sm"
                    data-testid="explorer-histogram-empty"
                >
                    No events in this range
                </p>
            ) : (
                <svg
                    ref={svgRef}
                    viewBox={`0 0 ${width} ${HEIGHT}`}
                    className="h-24 w-full touch-none"
                    role="img"
                    aria-label="Stacked event counts over time. Drag to zoom."
                    data-testid="explorer-histogram-svg"
                    onPointerDown={(event) => {
                        event.currentTarget.setPointerCapture(event.pointerId);
                        dragging.current = true;
                        const x = pointerX(event.clientX);
                        setBrush({ x0: x, x1: x });
                    }}
                    onPointerMove={(event) => {
                        if (!dragging.current) {
                            return;
                        }
                        const x = pointerX(event.clientX);
                        setBrush((current) =>
                            current ? { ...current, x1: x } : { x0: x, x1: x },
                        );
                    }}
                    onPointerUp={(event) => {
                        if (!dragging.current) {
                            return;
                        }
                        dragging.current = false;
                        const x = pointerX(event.clientX);
                        const next = brush ? { ...brush, x1: x } : { x0: x, x1: x };
                        setBrush(null);
                        commitBrush(next.x0, next.x1);
                    }}
                    onPointerCancel={() => {
                        dragging.current = false;
                        setBrush(null);
                    }}
                >
                    {stacked.map((bucket, index) => {
                        const x = index * (barWidth + barGap);
                        let y = HEIGHT - PAD_Y;
                        const segments = LAYERS.map((layer) => {
                            const value = bucket[layer.key];
                            const h =
                                value <= 0
                                    ? 0
                                    : Math.max(
                                          1,
                                          (value / maxStack) * (HEIGHT - PAD_Y * 2),
                                      );
                            y -= h;
                            return { layer, y, h };
                        });
                        return (
                            <g key={`${bucket.t}-${index}`}>
                                {segments.map(({ layer, y: top, h }) =>
                                    h > 0 ? (
                                        <rect
                                            key={layer.key}
                                            x={x}
                                            y={top}
                                            width={barWidth}
                                            height={h}
                                            className={layer.className}
                                        />
                                    ) : null,
                                )}
                            </g>
                        );
                    })}
                    {brush ? (
                        <rect
                            data-testid="explorer-histogram-brush"
                            x={Math.min(brush.x0, brush.x1)}
                            y={0}
                            width={Math.max(1, Math.abs(brush.x1 - brush.x0))}
                            height={HEIGHT}
                            className="fill-primary/25 stroke-primary"
                            strokeWidth={1}
                        />
                    ) : null}
                </svg>
            )}
            <p className="text-muted-foreground mt-1 text-[11px]">
                Drag a range to zoom the time filter.
            </p>
        </div>
    );
}
