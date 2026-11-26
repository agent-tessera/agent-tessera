import { useQuery } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { useMemo } from "react";

import { runsKeys } from "@/api/keys";
import { fetchRunEffects, type RunEffectItem } from "@/api/runs";
import { DataTable } from "@/components/DataTable";
import { ErrorState } from "@/components/ErrorState";
import { Mono } from "@/components/Mono";

export type EffectsTabProps = {
    runId: string;
};

const columnHelper = createColumnHelper<RunEffectItem>();

/**
 * Effect ledger for a run (`GET /v1/runs/{id}/effects`).
 */
export function EffectsTab({ runId }: EffectsTabProps) {
    const query = useQuery({
        queryKey: runsKeys.effects(runId),
        queryFn: () => fetchRunEffects(runId),
    });

    const columns = useMemo(
        () => [
            columnHelper.accessor("ref", {
                header: "Ref",
                size: 240,
                cell: (info) => <Mono>{info.getValue()}</Mono>,
            }),
            columnHelper.accessor("reversible", {
                header: "Reversible",
                size: 110,
                cell: (info) => (info.getValue() ? "Yes" : "No"),
            }),
            columnHelper.accessor("undo", {
                header: "Undo hint",
                size: 180,
                cell: (info) => {
                    const value = info.getValue();
                    return value ? <Mono>{value}</Mono> : "—";
                },
            }),
            columnHelper.accessor("agent_id", {
                header: "Agent",
                size: 160,
                cell: (info) => <Mono>{info.getValue()}</Mono>,
            }),
        ],
        [],
    );

    if (query.isError) {
        return (
            <ErrorState
                title="Could not load effects"
                message={
                    query.error instanceof Error
                        ? query.error.message
                        : "Request failed"
                }
                onRetry={() => void query.refetch()}
            />
        );
    }

    const items = query.data?.items ?? [];

    return (
        <div data-testid="trace-effects">
            <DataTable
                data={items}
                columns={columns}
                height={360}
                loading={query.isLoading}
                emptyTitle="No effects"
                emptyBody="This run has not recorded any reversible side effects."
                aria-label="Run effects"
            />
        </div>
    );
}
