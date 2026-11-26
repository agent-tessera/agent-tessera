import { useMemo } from "react";

import { prepareStackedSeries, type StackedSpendPoint } from "@/lib/insights-metrics";
import { formatMoney } from "@/lib/money";
import { cn } from "@/lib/utils";

export type StackedSpendChartProps = {
    points: StackedSpendPoint[];
    loading?: boolean;
    className?: string;
};

const SERIES_COLORS = [
    "var(--primary)",
    "var(--effect-approval)",
    "var(--effect-allow)",
    "var(--status-running)",
    "var(--effect-deny)",
    "var(--muted-foreground)",
];

const HEIGHT = 160;
const PAD_Y = 8;

/**
 * Lean SVG stacked bars for spend-by-agent/model over time (no Recharts).
 */
export function StackedSpendChart({
    points,
    loading = false,
    className,
}: StackedSpendChartProps) {
    const series = useMemo(() => prepareStackedSeries(points, 5), [points]);
    const maxTotal = useMemo(() => {
        let max = 0;
        for (const point of series.points) {
            if (point.total > max) {
                max = point.total;
            }
        }
        return Math.max(max, 0.000001);
    }, [series.points]);

    const width = Math.max(series.points.length * 18, 240);
    const barGap = series.points.length > 40 ? 0 : 2;
    const barWidth =
        series.points.length > 0
            ? Math.max(
                  2,
                  (width - barGap * series.points.length) / series.points.length,
              )
            : 0;

    if (loading) {
        return (
            <div
                className={cn(
                    "border-border bg-panel h-48 animate-pulse rounded-md border",
                    className,
                )}
                aria-busy="true"
                data-testid="insights-spend-chart-loading"
            />
        );
    }

    if (series.points.every((point) => point.total <= 0)) {
        return null;
    }

    return (
        <div
            className={cn(
                "border-border bg-panel space-y-3 rounded-md border p-3",
                className,
            )}
            data-testid="insights-spend-chart"
        >
            <svg
                viewBox={`0 0 ${width} ${HEIGHT}`}
                className="text-muted-foreground h-40 w-full"
                role="img"
                aria-label="Spend stacked over time"
            >
                {series.points.map((point, index) => {
                    const x = index * (barWidth + barGap);
                    let y = HEIGHT - PAD_Y;
                    return (
                        <g key={`${point.label}-${index}`}>
                            {point.values.map((value, seriesIndex) => {
                                if (value <= 0) {
                                    return null;
                                }
                                const h = (value / maxTotal) * (HEIGHT - PAD_Y * 2);
                                y -= h;
                                return (
                                    <rect
                                        key={`${series.keys[seriesIndex]}-${index}`}
                                        x={x}
                                        y={y}
                                        width={barWidth}
                                        height={h}
                                        fill={
                                            SERIES_COLORS[
                                                seriesIndex % SERIES_COLORS.length
                                            ]
                                        }
                                    >
                                        <title>
                                            {point.label}: {series.keys[seriesIndex]}{" "}
                                            {formatMoney(value)}
                                        </title>
                                    </rect>
                                );
                            })}
                        </g>
                    );
                })}
            </svg>
            <ul
                className="text-muted-foreground flex flex-wrap gap-3 text-xs"
                aria-label="Spend series"
            >
                {series.keys.map((key, index) => (
                    <li key={key} className="inline-flex items-center gap-1.5">
                        <span
                            className="size-2.5 rounded-sm"
                            style={{
                                background: SERIES_COLORS[index % SERIES_COLORS.length],
                            }}
                            aria-hidden
                        />
                        <span className="text-foreground">{key}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}
