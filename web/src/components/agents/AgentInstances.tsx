import { createColumnHelper } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { fetchAgentInstances, revokeInstance, type AgentInstance } from "@/api/agents";
import { agentsKeys } from "@/api/keys";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/DataTable";
import { ErrorState } from "@/components/ErrorState";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { Button } from "@/components/ui/button";

export type AgentInstancesProps = {
    agentId: string;
};

type InstanceRow = {
    id: string;
    shortId: string;
    host: string;
    started: string;
    lastSeen: string;
    status: string;
    revoked: boolean;
};

const columnHelper = createColumnHelper<InstanceRow>();

/** Instances tab with revoke-confirm. */
export function AgentInstances({ agentId }: AgentInstancesProps) {
    const queryClient = useQueryClient();
    const [revokeTarget, setRevokeTarget] = useState<AgentInstance | null>(null);
    const [revokeError, setRevokeError] = useState<string | null>(null);

    const instancesQuery = useQuery({
        queryKey: agentsKeys.instances(agentId),
        queryFn: () => fetchAgentInstances(agentId, { limit: 100 }),
    });

    const revokeMutation = useMutation({
        mutationFn: (input: { id: string; reason: string }) =>
            revokeInstance(input.id, { reason: input.reason }),
        onSuccess: async () => {
            setRevokeTarget(null);
            setRevokeError(null);
            await queryClient.invalidateQueries({
                queryKey: agentsKeys.instances(agentId),
            });
            await queryClient.invalidateQueries({
                queryKey: agentsKeys.detail(agentId),
            });
        },
        onError: (error) => {
            setRevokeError(error instanceof Error ? error.message : "Revoke failed");
        },
    });

    const rows = useMemo<InstanceRow[]>(
        () =>
            (instancesQuery.data?.items ?? []).map((item) => ({
                id: item.id,
                shortId: item.id.slice(0, 8),
                host: item.host || "—",
                started: item.started_at,
                lastSeen: item.last_heartbeat_at,
                status: item.revoked ? "revoked" : "active",
                revoked: item.revoked,
            })),
        [instancesQuery.data],
    );

    const columns = useMemo(
        () => [
            columnHelper.accessor("shortId", {
                header: "Instance",
                size: 120,
                cell: (info) => (
                    <span title={info.row.original.id}>
                        <Mono copyValue={info.row.original.id}>{info.getValue()}</Mono>
                    </span>
                ),
            }),
            columnHelper.accessor("host", {
                header: "Host",
                size: 160,
            }),
            columnHelper.accessor("started", {
                header: "Registered",
                size: 140,
                cell: (info) => <RelativeTime value={info.getValue()} />,
            }),
            columnHelper.accessor("lastSeen", {
                header: "Last seen",
                size: 140,
                cell: (info) => <RelativeTime value={info.getValue()} />,
            }),
            columnHelper.accessor("status", {
                header: "Status",
                size: 100,
                cell: (info) => <span className="capitalize">{info.getValue()}</span>,
            }),
            columnHelper.display({
                id: "actions",
                header: "",
                size: 100,
                cell: (info) => {
                    if (info.row.original.revoked) {
                        return null;
                    }
                    return (
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            data-testid={`instance-revoke-${info.row.original.id}`}
                            onClick={(event) => {
                                event.stopPropagation();
                                const match = instancesQuery.data?.items.find(
                                    (item) => item.id === info.row.original.id,
                                );
                                if (match) {
                                    setRevokeError(null);
                                    setRevokeTarget(match);
                                }
                            }}
                        >
                            Revoke
                        </Button>
                    );
                },
            }),
        ],
        [instancesQuery.data],
    );

    if (instancesQuery.isError) {
        return (
            <ErrorState
                title="Could not load instances"
                message={
                    instancesQuery.error instanceof Error
                        ? instancesQuery.error.message
                        : "Request failed"
                }
                onRetry={() => void instancesQuery.refetch()}
            />
        );
    }

    return (
        <div className="space-y-3" data-testid="agent-instances">
            {revokeError ? (
                <p
                    className="text-status-error-fg text-sm"
                    role="alert"
                    data-testid="instance-revoke-error"
                >
                    {revokeError}
                </p>
            ) : null}
            <DataTable
                data={rows}
                columns={columns}
                loading={instancesQuery.isPending}
                height={420}
                emptyTitle="No instances"
                emptyBody="Instances appear after an agent connects with a registration token."
                aria-label="Agent instances"
            />
            <ConfirmDialog
                open={revokeTarget != null}
                title="Revoke instance"
                sentence={
                    revokeTarget
                        ? `Revoke instance ${revokeTarget.id.slice(0, 8)}? Its runtime credentials will stop working.`
                        : ""
                }
                confirmLabel="Revoke"
                reasonRequired={false}
                destructive
                onCancel={() => {
                    setRevokeTarget(null);
                    setRevokeError(null);
                }}
                onConfirm={(reason) => {
                    if (!revokeTarget) {
                        return;
                    }
                    setRevokeError(null);
                    revokeMutation.mutate({ id: revokeTarget.id, reason });
                }}
            />
        </div>
    );
}
