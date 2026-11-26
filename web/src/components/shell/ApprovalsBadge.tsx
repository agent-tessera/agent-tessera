import { useQuery } from "@tanstack/react-query";

import { fetchPendingApprovals, formatPendingBadge } from "@/api/approvals";
import { approvalsKeys } from "@/api/keys";
import { cn } from "@/lib/utils";

export type ApprovalsBadgeProps = {
    className?: string;
};

/** Pending approvals count for the sidebar, refreshed every 10 seconds. */
export function ApprovalsBadge({ className }: ApprovalsBadgeProps) {
    const query = useQuery({
        queryKey: approvalsKeys.list({ status: "pending", limit: 100 }),
        queryFn: () => fetchPendingApprovals(),
        refetchInterval: 10_000,
        refetchOnWindowFocus: true,
    });

    const label = query.data ? formatPendingBadge(query.data) : null;
    if (!label) {
        return null;
    }

    return (
        <span
            className={cn(
                "bg-effect-approval-bg text-effect-approval-fg inline-flex min-w-5 items-center justify-center rounded-md px-1.5 font-mono text-[11px] font-medium",
                className,
            )}
            aria-label={`${label} pending approvals`}
        >
            {label}
        </span>
    );
}
