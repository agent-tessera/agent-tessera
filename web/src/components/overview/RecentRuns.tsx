import { createColumnHelper } from "@tanstack/react-table";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";

import type { RunListItem } from "@/api/overview";
import { DataTable } from "@/components/DataTable";
import { Mono } from "@/components/Mono";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDuration, runStatusKind } from "@/lib/overview-metrics";
import { cn } from "@/lib/utils";

export type RecentRunsProps = {
    runs: RunListItem[];
    agentNames: Record<string, string>;
    loading?: boolean;
    className?: string;
};

type RunRow = {
    id: string;
    status: string;
    agents: string;
    spans: number;
    duration: string;
    denials: number;
};

const columnHelper = createColumnHelper<RunRow>();

/** Recent runs table for the Overview page. */
export function RecentRuns({ runs, agentNames, loading, className }: RecentRunsProps) {
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
                    agents: names.length ? names.join(", ") : "—",
                    spans: run.span_count ?? 0,
                    duration: formatDuration(
                        Number.isFinite(start) && Number.isFinite(end)
                            ? end - start
                            : NaN,
                    ),
                    denials: run.denial_count ?? 0,
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
                size: 200,
            }),
            columnHelper.accessor("spans", {
                header: "Spans",
                size: 80,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("duration", {
                header: "Duration",
                size: 100,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
            columnHelper.accessor("denials", {
                header: "Denials",
                size: 80,
                cell: (info) => <span className="tabular-nums">{info.getValue()}</span>,
            }),
        ],
        [],
    );

    return (
        <section
            data-testid="recent-runs"
            className={cn("space-y-3", className)}
            aria-labelledby="recent-runs-heading"
        >
            <div className="flex items-baseline justify-between gap-2">
                <h2
                    id="recent-runs-heading"
                    className="text-foreground text-sm font-semibold"
                >
                    Recent runs
                </h2>
                <Link
                    to="/traces"
                    search={(prev) => prev}
                    className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
                >
                    View all
                </Link>
            </div>
            <DataTable
                data={data}
                columns={columns}
                loading={loading}
                height={360}
                emptyTitle="No runs yet"
                emptyBody="Runs appear when agents report governed activity."
                aria-label="Recent runs"
                onRowActivate={(row) => {
                    void navigate({
                        to: "/traces/$runId",
                        params: { runId: row.id },
                        search: (prev) => prev,
                    });
                }}
            />
        </section>
    );
}
