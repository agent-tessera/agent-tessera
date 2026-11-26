import { createColumnHelper } from "@tanstack/react-table";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";

import type { RunListItem } from "@/api/runs";
import { DataTable } from "@/components/DataTable";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDuration, runStatusKind } from "@/lib/overview-metrics";

export type RunsTableProps = {
    runs: RunListItem[];
    agentNames: Record<string, string>;
    loading?: boolean;
    loadingMore?: boolean;
    onEndReached?: () => void;
};

type RunRow = {
    id: string;
    status: string;
    started: string;
    duration: string;
    agents: string;
    spans: number;
    denials: number;
    approvals: number;
    errors: number;
    principal: string;
    supervisor: string;
};

const columnHelper = createColumnHelper<RunRow>();

/** Virtualized runs table for `/app/traces`. */
export function RunsTable({
    runs,
    agentNames,
    loading,
    loadingMore,
    onEndReached,
}: RunsTableProps) {
    const navigate = useNavigate();

    const data = useMemo<RunRow[]>(
        () =>
            runs.map((run) => {
                const start = Date.parse(run.first_seen_at);
                const end = Date.parse(run.last_seen_at);
                const names = run.participants
                    .map((p) => agentNames[p.agent_id] ?? p.agent_id)
                    .filter((name, index, all) => all.indexOf(name) === index);
                return {
                    id: run.id,
                    status: run.status,
                    started: run.first_seen_at,
                    duration: formatDuration(
                        Number.isFinite(start) && Number.isFinite(end)
                            ? end - start
                            : NaN,
                    ),
                    agents: names.length ? names.join(", ") : "—",
                    spans: run.span_count ?? 0,
                    denials: run.denial_count ?? 0,
                    approvals: run.approval_count ?? 0,
                    errors: run.error_count ?? 0,
                    principal: run.principal?.trim() ? run.principal : "—",
                    supervisor: run.supervisor?.trim() ? run.supervisor : "—",
                };
            }),
        [runs, agentNames],
    );

    const columns = useMemo(
        () => [
            columnHelper.accessor("status", {
                header: "Status",
                size: 110,
                cell: (info) => <StatusBadge status={runStatusKind(info.getValue())} />,
            }),
            columnHelper.accessor("started", {
                header: "Started",
                size: 120,
                cell: (info) => <RelativeTime value={info.getValue()} />,
            }),
            columnHelper.accessor("duration", {
                header: "Duration",
                size: 100,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("id", {
                header: "Run",
                size: 180,
                cell: (info) => (
                    <Link
                        to="/traces/$runId"
                        params={{ runId: info.getValue() }}
                        search={(prev) => prev}
                        className="hover:underline"
                        onClick={(event) => event.stopPropagation()}
                    >
                        <Mono>{info.getValue()}</Mono>
                    </Link>
                ),
            }),
            columnHelper.accessor("agents", {
                header: "Agents",
                size: 180,
            }),
            columnHelper.accessor("spans", {
                header: "Spans",
                size: 70,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("denials", {
                header: "Denials",
                size: 80,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("approvals", {
                header: "Approvals",
                size: 90,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("errors", {
                header: "Errors",
                size: 70,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("principal", {
                header: "Principal",
                size: 120,
            }),
            columnHelper.accessor("supervisor", {
                header: "Supervisor",
                size: 140,
            }),
        ],
        [],
    );

    return (
        <div className="space-y-2" data-testid="traces-table">
            <DataTable
                data={data}
                columns={columns}
                loading={loading}
                height={520}
                emptyTitle="No runs"
                emptyBody="Runs appear when agents report governed activity."
                aria-label="Governance runs"
                getRowId={(row) => row.id}
                getRowAttributes={(row) => ({
                    "data-testid": "traces-row",
                    "data-run-id": row.id,
                    "data-denials": row.denials,
                })}
                onRowActivate={(row) => {
                    void navigate({
                        to: "/traces/$runId",
                        params: { runId: row.id },
                        search: (prev) => prev,
                    });
                }}
                onEndReached={onEndReached}
            />
            {loadingMore ? (
                <p
                    className="text-muted-foreground text-xs"
                    data-testid="traces-loading-more"
                >
                    Loading more…
                </p>
            ) : null}
        </div>
    );
}
