import { Link } from "@tanstack/react-router";
import {
    AlertTriangle,
    Ghost,
    Scale,
    ShieldAlert,
    UserX,
    type LucideIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import type { AttentionItem, AttentionKind } from "@/lib/overview-metrics";
import { cn } from "@/lib/utils";

const ICONS: Record<AttentionKind, LucideIcon> = {
    approval: Scale,
    denial_spike: AlertTriangle,
    orphaned: UserX,
    dormant: Ghost,
    posture: ShieldAlert,
    audit: ShieldAlert,
};

export type NeedsAttentionProps = {
    items: AttentionItem[];
    className?: string;
};

/** Actionable Overview list for approvals, spikes, orphans, posture, and audit. */
export function NeedsAttention({ items, className }: NeedsAttentionProps) {
    return (
        <section
            data-testid="needs-attention"
            className={cn("space-y-3", className)}
            aria-labelledby="needs-attention-heading"
        >
            <h2
                id="needs-attention-heading"
                className="text-foreground text-sm font-semibold"
            >
                Needs attention
            </h2>
            {items.length === 0 ? (
                <EmptyState
                    title="All clear"
                    body="No pending approvals, denial spikes, or other alerts."
                    className="border-border bg-panel rounded-md border py-8"
                />
            ) : (
                <ul className="border-border bg-panel divide-border divide-y rounded-md border">
                    {items.map((item) => {
                        const Icon = ICONS[item.kind];
                        return (
                            <li key={item.id}>
                                <Link
                                    to={item.to}
                                    params={item.params}
                                    search={(prev) => ({
                                        ...prev,
                                        ...item.search,
                                    })}
                                    data-testid={`attention-${item.kind}`}
                                    data-attention-id={item.id}
                                    className="hover:bg-muted/60 focus-visible:ring-ring flex items-start gap-3 px-3 py-2.5 focus-visible:ring-2 focus-visible:outline-none"
                                >
                                    <Icon
                                        className="text-muted-foreground mt-0.5 size-4 shrink-0"
                                        aria-hidden
                                    />
                                    <span className="min-w-0 space-y-0.5">
                                        <span className="text-foreground block text-sm font-medium">
                                            {item.title}
                                        </span>
                                        <span className="text-muted-foreground block text-xs">
                                            {item.detail}
                                        </span>
                                    </span>
                                </Link>
                            </li>
                        );
                    })}
                </ul>
            )}
        </section>
    );
}
