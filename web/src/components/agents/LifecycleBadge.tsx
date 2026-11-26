import { cn } from "@/lib/utils";

export type LifecycleBadgeProps = {
    status: string;
    className?: string;
};

const STYLES: Record<string, string> = {
    active: "bg-status-ok-bg text-status-ok-fg border-status-ok/40",
    suspended: "bg-status-running-bg text-status-running-fg border-status-running/40",
    draft: "bg-muted text-muted-foreground border-border",
    retired: "bg-muted text-muted-foreground border-border",
};

/** Agent lifecycle status chip (draft / active / suspended / retired). */
export function LifecycleBadge({ status, className }: LifecycleBadgeProps) {
    const style = STYLES[status] ?? "bg-muted text-muted-foreground border-border";
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-medium capitalize",
                style,
                className,
            )}
            data-testid="lifecycle-badge"
            data-status={status}
        >
            {status}
        </span>
    );
}
