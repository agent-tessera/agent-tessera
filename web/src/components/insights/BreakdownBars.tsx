import { cn } from "@/lib/utils";

export type BreakdownBarRow = {
    key: string;
    label: string;
    value: number;
    display: string;
};

export type BreakdownBarsProps = {
    rows: BreakdownBarRow[];
    emptyTitle: string;
    emptyBody: string;
    className?: string;
    testId?: string;
};

/**
 * Horizontal labelled bars for spend / denial breakdowns.
 */
export function BreakdownBars({
    rows,
    emptyTitle,
    emptyBody,
    className,
    testId = "insights-breakdown",
}: BreakdownBarsProps) {
    const max = Math.max(...rows.map((row) => row.value), 0);

    if (rows.length === 0) {
        return (
            <div
                className={cn(
                    "border-border bg-panel rounded-md border px-6 py-10 text-center",
                    className,
                )}
                data-testid={`${testId}-empty`}
            >
                <h2 className="text-foreground text-sm font-semibold">{emptyTitle}</h2>
                <p className="text-muted-foreground mt-1 text-sm">{emptyBody}</p>
            </div>
        );
    }

    return (
        <ul
            className={cn(
                "border-border bg-panel space-y-3 rounded-md border p-4",
                className,
            )}
            data-testid={testId}
        >
            {rows.map((row) => {
                const widthPct = max > 0 ? Math.max(2, (row.value / max) * 100) : 0;
                return (
                    <li
                        key={row.key}
                        className="space-y-1"
                        data-row-key={row.key}
                        data-row-value={row.value}
                    >
                        <div className="flex items-baseline justify-between gap-3 text-sm">
                            <span className="text-foreground truncate font-medium">
                                {row.label}
                            </span>
                            <span className="text-muted-foreground shrink-0 font-mono text-xs">
                                {row.display}
                            </span>
                        </div>
                        <div className="bg-muted h-2 overflow-hidden rounded-full">
                            <div
                                className="bg-primary h-full rounded-full"
                                style={{ width: `${widthPct}%` }}
                            />
                        </div>
                    </li>
                );
            })}
        </ul>
    );
}
