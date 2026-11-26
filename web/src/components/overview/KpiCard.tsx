import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Sparkline } from "@/components/overview/Sparkline";
import { cn } from "@/lib/utils";

export type KpiTo = "/agents" | "/approvals" | "/traces" | "/explorer" | "/insights";

export type KpiCardProps = {
    label: string;
    value: ReactNode;
    deltaLabel: string;
    deltaPositive?: boolean | null;
    sparkline: number[];
    sparklineLabel: string;
    to: KpiTo;
    search?: Record<string, string | undefined>;
    className?: string;
};

/** One Overview KPI tile with sparkline and period delta. */
export function KpiCard({
    label,
    value,
    deltaLabel,
    deltaPositive,
    sparkline,
    sparklineLabel,
    to,
    search,
    className,
}: KpiCardProps) {
    const deltaClass =
        deltaPositive === true
            ? "text-effect-allow-fg"
            : deltaPositive === false
              ? "text-effect-deny-fg"
              : "text-muted-foreground";

    return (
        <Link
            to={to}
            search={(prev) => ({ ...prev, ...search })}
            className={cn(
                "bg-panel border-border hover:border-border-strong focus-visible:ring-ring flex flex-col gap-2 rounded-md border p-3 transition-colors focus-visible:ring-2 focus-visible:outline-none",
                className,
            )}
        >
            <div className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                {label}
            </div>
            <div className="flex items-end justify-between gap-2">
                <div className="text-foreground text-2xl font-semibold tracking-tight tabular-nums">
                    {value}
                </div>
                <Sparkline values={sparkline} label={sparklineLabel} />
            </div>
            <div className={cn("text-xs tabular-nums", deltaClass)}>
                {deltaLabel}
                <span className="text-muted-foreground"> vs prior 24h</span>
            </div>
        </Link>
    );
}
