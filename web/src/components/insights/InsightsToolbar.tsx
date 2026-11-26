import {
    INSIGHTS_RANGES,
    INSIGHTS_VIEWS,
    type InsightsRange,
    type InsightsView,
} from "@/lib/insights-search";
import { cn } from "@/lib/utils";

export type InsightsToolbarProps = {
    view: InsightsView;
    range: InsightsRange;
    onViewChange: (view: InsightsView) => void;
    onRangeChange: (range: InsightsRange) => void;
    className?: string;
};

const VIEW_LABELS: Record<InsightsView, string> = {
    spend: "Spend",
    denials: "Denials",
    footprint: "Footprint",
    posture: "Posture",
    quotas: "Quotas",
};

/**
 * Insights tab strip and global time-range presets.
 */
export function InsightsToolbar({
    view,
    range,
    onViewChange,
    onRangeChange,
    className,
}: InsightsToolbarProps) {
    return (
        <div
            className={cn(
                "flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between",
                className,
            )}
            data-testid="insights-toolbar"
        >
            <nav
                className="border-border flex flex-wrap gap-1 border-b pb-2"
                aria-label="Insights views"
                data-testid="insights-tabs"
            >
                {INSIGHTS_VIEWS.map((id) => {
                    const active = id === view;
                    return (
                        <button
                            key={id}
                            type="button"
                            className={cn(
                                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                                active
                                    ? "bg-accent-soft text-foreground"
                                    : "text-muted-foreground hover:text-foreground hover:bg-muted",
                            )}
                            aria-current={active ? "page" : undefined}
                            data-testid={`insights-tab-${id}`}
                            onClick={() => onViewChange(id)}
                        >
                            {VIEW_LABELS[id]}
                        </button>
                    );
                })}
            </nav>
            <label className="text-muted-foreground flex items-center gap-2 text-xs">
                <span>Time range</span>
                <select
                    className="border-border bg-panel text-foreground h-8 rounded-md border px-2 text-sm"
                    aria-label="Insights time range"
                    data-testid="insights-range"
                    value={range}
                    onChange={(event) =>
                        onRangeChange(event.target.value as InsightsRange)
                    }
                >
                    {INSIGHTS_RANGES.map((preset) => (
                        <option key={preset} value={preset}>
                            {preset}
                        </option>
                    ))}
                </select>
            </label>
        </div>
    );
}
