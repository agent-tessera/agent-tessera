import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import type { TraceAgent, TraceSpan } from "@/api/runs";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { Mono } from "@/components/Mono";
import { cn } from "@/lib/utils";
import {
    agentSpanCounts,
    buildTraceTree,
    spanOutcome,
    type TraceTreeNode,
} from "@/lib/trace-waterfall";

export type TraceTreeProps = {
    spans: TraceSpan[];
    agents: TraceAgent[];
    selectedSpanId?: string;
    onSelectSpan?: (spanId: string) => void;
    className?: string;
};

/**
 * Nested agent → span tree with descendant counts.
 */
export function TraceTree({
    spans,
    agents,
    selectedSpanId,
    onSelectSpan,
    className,
}: TraceTreeProps) {
    const tree = useMemo(() => buildTraceTree(spans), [spans]);
    const byAgent = useMemo(() => agentSpanCounts(spans, agents), [spans, agents]);
    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const agent of agents) {
            map[agent.id] = agent.name;
        }
        return map;
    }, [agents]);

    return (
        <div
            className={cn(
                "border-border bg-panel space-y-4 rounded-md border p-3",
                className,
            )}
            data-testid="trace-tree"
        >
            <ul className="text-muted-foreground flex flex-wrap gap-3 text-xs">
                {byAgent.map(({ agent, count }) => (
                    <li key={agent.id} data-testid="trace-tree-agent-count">
                        <span className="text-foreground font-medium">
                            {agent.name}
                        </span>
                        {" · "}
                        {count} span{count === 1 ? "" : "s"}
                    </li>
                ))}
            </ul>
            <ul className="space-y-1">
                {tree.map((node) => (
                    <TreeNodeView
                        key={node.span.span_id}
                        node={node}
                        depth={0}
                        agentNames={agentNames}
                        selectedSpanId={selectedSpanId}
                        onSelectSpan={onSelectSpan}
                    />
                ))}
            </ul>
        </div>
    );
}

function TreeNodeView({
    node,
    depth,
    agentNames,
    selectedSpanId,
    onSelectSpan,
}: {
    node: TraceTreeNode;
    depth: number;
    agentNames: Record<string, string>;
    selectedSpanId?: string;
    onSelectSpan?: (spanId: string) => void;
}) {
    const [open, setOpen] = useState(true);
    const wantId =
        selectedSpanId === undefined || selectedSpanId === ""
            ? ""
            : String(selectedSpanId);
    const selected = wantId !== "" && node.span.span_id === wantId;
    const label = node.span.resource || node.span.kind || node.span.span_id;
    const outcome = spanOutcome(node.span);
    const effect =
        outcome === "deny" || outcome === "allow" || outcome === "require_approval"
            ? (outcome as EffectKind)
            : null;
    const agentLabel = node.span.agent_id
        ? (agentNames[node.span.agent_id] ?? node.span.agent_id)
        : null;

    return (
        <li>
            <div
                className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm",
                    selected && "bg-accent-soft",
                )}
                style={{ paddingLeft: 8 + depth * 14 }}
                data-testid="trace-tree-node"
                data-span-id={node.span.span_id}
                data-selected={selected ? "true" : "false"}
                onClick={() => onSelectSpan?.(node.span.span_id)}
            >
                {node.children.length > 0 ? (
                    <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground shrink-0"
                        aria-label={open ? "Collapse" : "Expand"}
                        onClick={(event) => {
                            event.stopPropagation();
                            setOpen((value) => !value);
                        }}
                    >
                        {open ? (
                            <ChevronDown className="size-3.5" aria-hidden />
                        ) : (
                            <ChevronRight className="size-3.5" aria-hidden />
                        )}
                    </button>
                ) : (
                    <span className="inline-block size-3.5 shrink-0" aria-hidden />
                )}
                <span className="min-w-0 flex-1 truncate">{label}</span>
                {agentLabel ? (
                    <span className="text-muted-foreground shrink-0 text-xs">
                        {agentLabel}
                    </span>
                ) : null}
                <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                    {node.descendantCount > 0 ? `${node.descendantCount + 1}` : "1"}
                </span>
                {effect ? <EffectBadge effect={effect} /> : null}
                <Mono className="text-muted-foreground hidden text-[10px] sm:inline">
                    {node.span.span_id.slice(0, 8)}
                </Mono>
            </div>
            {open && node.children.length > 0 ? (
                <ul>
                    {node.children.map((child) => (
                        <TreeNodeView
                            key={child.span.span_id}
                            node={child}
                            depth={depth + 1}
                            agentNames={agentNames}
                            selectedSpanId={selectedSpanId}
                            onSelectSpan={onSelectSpan}
                        />
                    ))}
                </ul>
            ) : null}
        </li>
    );
}
