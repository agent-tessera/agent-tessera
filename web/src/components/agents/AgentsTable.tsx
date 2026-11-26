import { createColumnHelper } from "@tanstack/react-table";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";

import type { Agent } from "@/api/agents";
import { DataTable } from "@/components/DataTable";
import { LifecycleBadge } from "@/components/agents/LifecycleBadge";
import { RelativeTime } from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

export type AgentTableRow = {
    id: string;
    name: string;
    environment: string;
    status: string;
    autonomy: string;
    risk: string;
    owner: string;
    posture: string;
    permissive: boolean;
    lastSeen: string | null;
    denials24h: number;
};

export type AgentsTableProps = {
    agents: Agent[];
    ownerNames: Record<string, string>;
    postureById: Record<string, { score: number; permissive?: boolean }>;
    denialsById: Record<string, number>;
    loading?: boolean;
    loadingMore?: boolean;
    onEndReached?: () => void;
};

const columnHelper = createColumnHelper<AgentTableRow>();

/** Virtualized agents registry table for `/app/agents`. */
export function AgentsTable({
    agents,
    ownerNames,
    postureById,
    denialsById,
    loading,
    loadingMore,
    onEndReached,
}: AgentsTableProps) {
    const navigate = useNavigate();

    const data = useMemo<AgentTableRow[]>(
        () =>
            agents.map((agent) => {
                const posture = postureById[agent.id];
                return {
                    id: agent.id,
                    name: agent.name,
                    environment: agent.environment,
                    status: agent.status,
                    autonomy: agent.autonomy,
                    risk: agent.risk_tier,
                    owner: ownerNames[agent.owner_id] ?? agent.owner_id,
                    posture:
                        posture && Number.isFinite(posture.score)
                            ? String(Math.round(posture.score))
                            : "—",
                    permissive: Boolean(posture?.permissive),
                    lastSeen: agent.last_seen_at,
                    denials24h: denialsById[agent.id] ?? 0,
                };
            }),
        [agents, ownerNames, postureById, denialsById],
    );

    const columns = useMemo(
        () => [
            columnHelper.accessor("name", {
                header: "Name",
                size: 160,
                cell: (info) => (
                    <Link
                        to="/agents/$agentId"
                        params={{ agentId: info.row.original.id }}
                        search={(prev) => ({
                            environment:
                                typeof prev.environment === "string"
                                    ? prev.environment
                                    : undefined,
                        })}
                        className="hover:underline"
                        onClick={(event) => event.stopPropagation()}
                    >
                        {info.getValue()}
                    </Link>
                ),
            }),
            columnHelper.accessor("environment", {
                header: "Environment",
                size: 110,
            }),
            columnHelper.accessor("status", {
                header: "Status",
                size: 110,
                cell: (info) => <LifecycleBadge status={info.getValue()} />,
            }),
            columnHelper.accessor("autonomy", {
                header: "Autonomy",
                size: 110,
                cell: (info) => <span className="capitalize">{info.getValue()}</span>,
            }),
            columnHelper.accessor("risk", {
                header: "Risk",
                size: 80,
                cell: (info) => <span className="capitalize">{info.getValue()}</span>,
            }),
            columnHelper.accessor("owner", {
                header: "Owner",
                size: 140,
            }),
            columnHelper.accessor("posture", {
                header: "Posture",
                size: 80,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("permissive", {
                header: "Policy",
                size: 100,
                cell: (info) =>
                    info.getValue() ? (
                        <span
                            className={cn(
                                "bg-status-running-bg text-status-running-fg border-status-running/40 inline-flex rounded-md border px-1.5 py-0.5 text-xs font-medium",
                            )}
                            data-testid="permissive-chip"
                        >
                            Permissive
                        </span>
                    ) : (
                        <span className="text-muted-foreground">—</span>
                    ),
            }),
            columnHelper.accessor("lastSeen", {
                header: "Last seen",
                size: 120,
                cell: (info) => {
                    const value = info.getValue();
                    return value ? (
                        <RelativeTime value={value} />
                    ) : (
                        <span className="text-muted-foreground">Never</span>
                    );
                },
            }),
            columnHelper.accessor("denials24h", {
                header: "Denials 24 h",
                size: 100,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
        ],
        [],
    );

    return (
        <div className="space-y-2" data-testid="agents-table">
            <DataTable
                data={data}
                columns={columns}
                height={480}
                loading={loading}
                emptyTitle="No agents"
                emptyBody="Create an agent to register it in the environment."
                getRowId={(row) => row.id}
                getRowAttributes={(row) => ({
                    "data-testid": "agents-row",
                    "data-agent-id": row.id,
                    "data-agent-name": row.name,
                })}
                onRowActivate={(row) => {
                    void navigate({
                        to: "/agents/$agentId",
                        params: { agentId: row.id },
                        search: (prev) => ({
                            environment:
                                typeof prev.environment === "string"
                                    ? prev.environment
                                    : undefined,
                        }),
                    });
                }}
                onEndReached={onEndReached}
                aria-label="Agents"
            />
            {loadingMore ? (
                <p
                    className="text-muted-foreground text-xs"
                    data-testid="agents-loading-more"
                >
                    Loading more…
                </p>
            ) : null}
        </div>
    );
}
