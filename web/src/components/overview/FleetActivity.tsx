import { Link } from "@tanstack/react-router";
import {
    Ban,
    FilePenLine,
    Power,
    PowerOff,
    ShieldOff,
    type LucideIcon,
} from "lucide-react";

import type { RevocationItem } from "@/api/overview";
import { EmptyState } from "@/components/EmptyState";
import { RelativeTime } from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

export type FleetActivityProps = {
    items: RevocationItem[];
    agentNames: Record<string, string>;
    loading?: boolean;
};

const KIND_META: Record<string, { label: string; icon: LucideIcon; tone: string }> = {
    agent_suspended: {
        label: "Agent suspended",
        icon: PowerOff,
        tone: "text-status-running-fg",
    },
    agent_activated: {
        label: "Agent activated",
        icon: Power,
        tone: "text-status-ok-fg",
    },
    instance_revoked: {
        label: "Instance revoked",
        icon: Ban,
        tone: "text-status-revoked-fg",
    },
    run_revoked: {
        label: "Run revoked",
        icon: ShieldOff,
        tone: "text-status-revoked-fg",
    },
    policy_updated: {
        label: "Policy updated",
        icon: FilePenLine,
        tone: "text-muted-foreground",
    },
};

/**
 * Overview strip of recent revocations / fleet lifecycle events.
 */
export function FleetActivity({ items, agentNames, loading }: FleetActivityProps) {
    return (
        <section
            className="space-y-3"
            data-testid="fleet-activity"
            aria-labelledby="fleet-activity-heading"
        >
            <h2
                id="fleet-activity-heading"
                className="text-foreground text-sm font-semibold"
            >
                Fleet activity
            </h2>
            {loading ? <p className="text-muted-foreground text-sm">Loading…</p> : null}
            {!loading && items.length === 0 ? (
                <EmptyState
                    title="No recent revocations"
                    body="Suspends, activates, instance and run revokes, and policy updates appear here."
                    className="border-border bg-panel rounded-md border py-8"
                />
            ) : null}
            {items.length > 0 ? (
                <ul className="border-border bg-panel divide-border divide-y rounded-md border">
                    {items.map((item) => {
                        const meta = KIND_META[item.kind];
                        const Icon = meta?.icon ?? FilePenLine;
                        return (
                            <li
                                key={item.id}
                                className="hover:bg-muted/40 flex items-start gap-3 px-3 py-2.5"
                                data-testid={`fleet-row-${item.id}`}
                                data-kind={item.kind}
                            >
                                <Icon
                                    className={cn(
                                        "mt-0.5 size-4 shrink-0",
                                        meta?.tone ?? "text-muted-foreground",
                                    )}
                                    aria-hidden
                                />
                                <div className="min-w-0 flex-1 space-y-0.5">
                                    <p className="text-foreground text-sm font-medium">
                                        {meta?.label ?? item.kind}
                                    </p>
                                    <p className="text-muted-foreground text-xs">
                                        <FleetLink
                                            item={item}
                                            agentNames={agentNames}
                                        />
                                    </p>
                                </div>
                                <RelativeTime
                                    value={item.created_at}
                                    className="text-muted-foreground shrink-0 text-xs"
                                />
                            </li>
                        );
                    })}
                </ul>
            ) : null}
        </section>
    );
}

function FleetLink({
    item,
    agentNames,
}: {
    item: RevocationItem;
    agentNames: Record<string, string>;
}) {
    if (item.kind === "run_revoked") {
        return (
            <Link
                to="/traces/$runId"
                params={{ runId: item.subject }}
                className="hover:text-foreground underline-offset-2 hover:underline"
            >
                Run {item.subject}
            </Link>
        );
    }
    if (item.agent_id) {
        const label = agentNames[item.agent_id] ?? item.agent_id;
        return (
            <Link
                to="/agents/$agentId"
                params={{ agentId: item.agent_id }}
                className="hover:text-foreground underline-offset-2 hover:underline"
            >
                {label}
                {item.kind === "instance_revoked" ? ` · ${item.subject}` : ""}
            </Link>
        );
    }
    return <span>{item.subject}</span>;
}
