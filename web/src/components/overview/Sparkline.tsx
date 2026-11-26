import { cn } from "@/lib/utils";

export type SparklineProps = {
    values: number[];
    className?: string;
    /** Accessible label describing the series. */
    label?: string;
};

/**
 * Compact SVG sparkline for KPI tiles.
 *
 * @param props - Series values and styling.
 */
export function Sparkline({ values, className, label = "Trend" }: SparklineProps) {
    const width = 96;
    const height = 28;
    const pad = 1;

    if (values.length === 0) {
        return (
            <svg
                width={width}
                height={height}
                className={cn("text-muted-foreground/40", className)}
                role="img"
                aria-label={label}
            >
                <line
                    x1={pad}
                    y1={height / 2}
                    x2={width - pad}
                    y2={height / 2}
                    stroke="currentColor"
                    strokeWidth={1}
                    strokeDasharray="2 2"
                />
            </svg>
        );
    }

    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = Math.max(max - min, 1);
    const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;
    const points = values
        .map((value, index) => {
            const x = pad + index * step;
            const y = height - pad - ((value - min) / span) * (height - pad * 2);
            return `${x},${y}`;
        })
        .join(" ");

    return (
        <svg
            width={width}
            height={height}
            className={cn("text-primary", className)}
            role="img"
            aria-label={label}
        >
            <polyline
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                strokeLinejoin="round"
                strokeLinecap="round"
                points={points}
            />
        </svg>
    );
}
