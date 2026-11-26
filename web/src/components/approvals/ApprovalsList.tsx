import { Inbox } from "lucide-react";

import type { Approval } from "@/api/approvals";
import { formatApprovalWhy } from "@/api/approvals";
import { EmptyState } from "@/components/EmptyState";
import { RelativeTime } from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

export type ApprovalsListProps = {
    items: Approval[];
    selectedId: string | undefined;
    agentNames: Record<string, string>;
    onSelect: (id: string) => void;
};

/**
 * Left-hand approvals inbox list.
 *
 * Keyboard focus is driven by the parent; this renders the selected row.
 */
export function ApprovalsList({
    items,
    selectedId,
    agentNames,
    onSelect,
}: ApprovalsListProps) {
    if (items.length === 0) {
        return (
            <EmptyState
                icon={Inbox}
                title="No approvals"
                body="Nothing matches the current status and agent filters."
                className="py-16"
            />
        );
    }

    return (
        <ul
            className="divide-border divide-y"
            role="listbox"
            aria-label="Approvals"
            data-testid="approvals-list"
        >
            {items.map((item) => {
                const selected = item.id === selectedId;
                const resource =
                    typeof item.action?.resource === "string"
                        ? item.action.resource
                        : "—";
                const why = formatApprovalWhy(item.decision) || "—";
                const agentLabel = agentNames[item.agent_id] ?? item.agent_id;
                return (
                    <li
                        key={item.id}
                        role="option"
                        aria-selected={selected}
                        tabIndex={selected ? 0 : -1}
                        data-testid="approval-row"
                        data-approval-id={item.id}
                        data-selected={selected ? "true" : "false"}
                        className={cn(
                            "hover:bg-muted/60 focus-visible:ring-ring cursor-pointer px-3 py-2.5 text-left focus-visible:ring-2 focus-visible:outline-none",
                            selected && "bg-muted",
                        )}
                        onClick={() => onSelect(item.id)}
                        onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                onSelect(item.id);
                            }
                        }}
                    >
                        <div className="flex items-baseline justify-between gap-2">
                            <span className="truncate text-sm font-medium">
                                {agentLabel}
                            </span>
                            <RelativeTime
                                value={item.created_at}
                                className="text-muted-foreground shrink-0 text-xs"
                            />
                        </div>
                        <div className="mt-0.5 truncate font-mono text-xs">
                            {resource}
                        </div>
                        <div className="text-muted-foreground mt-0.5 truncate text-xs">
                            {why}
                        </div>
                        <div className="text-muted-foreground mt-1 text-[11px] capitalize">
                            {item.status}
                        </div>
                    </li>
                );
            })}
        </ul>
    );
}
