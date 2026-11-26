import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { fetchAgents } from "@/api/overview";
import { agentsKeys, runsKeys } from "@/api/keys";
import { fetchRuns } from "@/api/runs";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { RunsTable } from "@/components/traces/RunsTable";
import { setPageTitle } from "@/lib/page-title";
import {
    isFlagEnabled,
    parseTracesSearch,
    tracesApiFilters,
    type TracesSearch,
} from "@/lib/traces-search";

export type { TracesSearch };

export const Route = createFileRoute("/_console/traces/")({
    validateSearch: parseTracesSearch,
    component: TracesPage,
});

const PAGE_SIZE = 50;
const RUN_STATUSES = ["running", "completed", "revoked", "error"] as const;

/**
 * Governance runs list with shareable filters and cursor pagination.
 */
function TracesPage() {
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const apiFilters = useMemo(() => tracesApiFilters(search), [search]);

    useEffect(() => {
        setPageTitle("Traces");
    }, []);

    const patchSearch = (patch: Partial<TracesSearch>) => {
        void navigate({
            search: (prev) => {
                const next: TracesSearch = { ...prev, ...patch };
                for (const key of Object.keys(patch) as Array<keyof TracesSearch>) {
                    const value = patch[key];
                    if (value === undefined || value === "" || value === 0) {
                        delete next[key];
                    }
                }
                return next;
            },
            replace: true,
        });
    };

    const agentsQuery = useQuery({
        queryKey: agentsKeys.list({
            environment: search.environment,
            limit: 200,
        }),
        queryFn: () =>
            fetchAgents({
                environment: search.environment,
                limit: 200,
            }),
    });

    const runsQuery = useInfiniteQuery({
        queryKey: runsKeys.list({ ...apiFilters, environment: search.environment }),
        queryFn: ({ pageParam }) =>
            fetchRuns({
                ...apiFilters,
                limit: PAGE_SIZE,
                cursor: pageParam,
            }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    });

    const agentNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const agent of agentsQuery.data?.items ?? []) {
            map[agent.id] = agent.name;
        }
        return map;
    }, [agentsQuery.data]);

    const agentOptions = useMemo(() => {
        const items = [...(agentsQuery.data?.items ?? [])];
        items.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
        return items;
    }, [agentsQuery.data]);

    const runs = useMemo(
        () => runsQuery.data?.pages.flatMap((page) => page.items) ?? [],
        [runsQuery.data],
    );

    const loadError = runsQuery.isError
        ? runsQuery.error
        : agentsQuery.isError
          ? agentsQuery.error
          : null;

    return (
        <div className="space-y-6" data-testid="traces-page">
            <PageHeader
                title="Traces"
                description="Governance runs across agents, with denials, approvals, and errors."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Traces" },
                ]}
            />

            <div
                className="flex flex-wrap items-end gap-3"
                data-testid="traces-filters"
            >
                <label className="text-muted-foreground flex flex-col gap-1 text-xs">
                    Status
                    <select
                        className="border-border bg-panel text-foreground h-9 min-w-[9rem] rounded-md border px-2 text-sm"
                        value={search.status ?? ""}
                        data-testid="traces-filter-status"
                        onChange={(event) =>
                            patchSearch({
                                status: event.target.value || undefined,
                            })
                        }
                    >
                        <option value="">All statuses</option>
                        {RUN_STATUSES.map((status) => (
                            <option key={status} value={status}>
                                {status}
                            </option>
                        ))}
                    </select>
                </label>

                <label className="text-muted-foreground flex flex-col gap-1 text-xs">
                    Agent
                    <select
                        className="border-border bg-panel text-foreground h-9 min-w-[12rem] rounded-md border px-2 text-sm"
                        value={search.agent ?? ""}
                        data-testid="traces-filter-agent"
                        onChange={(event) =>
                            patchSearch({
                                agent: event.target.value || undefined,
                            })
                        }
                    >
                        <option value="">All agents</option>
                        {agentOptions.map((agent) => (
                            <option key={agent.id} value={agent.id}>
                                {agent.name}
                            </option>
                        ))}
                    </select>
                </label>

                <label className="text-muted-foreground flex items-center gap-2 pb-2 text-sm">
                    <input
                        type="checkbox"
                        className="accent-primary size-4"
                        checked={isFlagEnabled(search.has_denials)}
                        data-testid="traces-filter-has-denials"
                        onChange={(event) =>
                            patchSearch({
                                has_denials: event.target.checked ? 1 : undefined,
                            })
                        }
                    />
                    Has denials
                </label>

                <label className="text-muted-foreground flex items-center gap-2 pb-2 text-sm">
                    <input
                        type="checkbox"
                        className="accent-primary size-4"
                        checked={isFlagEnabled(search.has_errors)}
                        data-testid="traces-filter-has-errors"
                        onChange={(event) =>
                            patchSearch({
                                has_errors: event.target.checked ? 1 : undefined,
                            })
                        }
                    />
                    Has errors
                </label>

                <label className="text-muted-foreground flex flex-col gap-1 text-xs">
                    Since
                    <input
                        type="datetime-local"
                        className="border-border bg-panel text-foreground h-9 rounded-md border px-2 text-sm"
                        value={toLocalInput(search.since)}
                        data-testid="traces-filter-since"
                        onChange={(event) =>
                            patchSearch({
                                since: fromLocalInput(event.target.value),
                            })
                        }
                    />
                </label>

                <label className="text-muted-foreground flex flex-col gap-1 text-xs">
                    Until
                    <input
                        type="datetime-local"
                        className="border-border bg-panel text-foreground h-9 rounded-md border px-2 text-sm"
                        value={toLocalInput(search.until)}
                        data-testid="traces-filter-until"
                        onChange={(event) =>
                            patchSearch({
                                until: fromLocalInput(event.target.value),
                            })
                        }
                    />
                </label>

                {(search.since || search.until) && (
                    <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground pb-2 text-xs underline-offset-2 hover:underline"
                        onClick={() =>
                            patchSearch({ since: undefined, until: undefined })
                        }
                    >
                        Clear time range
                    </button>
                )}
            </div>

            {loadError ? (
                <ErrorState
                    message={
                        loadError instanceof Error
                            ? loadError.message
                            : "Failed to load runs"
                    }
                    onRetry={() => {
                        void runsQuery.refetch();
                        void agentsQuery.refetch();
                    }}
                />
            ) : (
                <RunsTable
                    runs={runs}
                    agentNames={agentNames}
                    loading={runsQuery.isPending}
                    loadingMore={runsQuery.isFetchingNextPage}
                    onEndReached={() => {
                        if (runsQuery.hasNextPage && !runsQuery.isFetchingNextPage) {
                            void runsQuery.fetchNextPage();
                        }
                    }}
                />
            )}
        </div>
    );
}

/**
 * Convert an ISO / Zulu timestamp to a `datetime-local` value.
 *
 * @param value - Stored search param.
 */
function toLocalInput(value: string | undefined): string {
    if (!value) {
        return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return "";
    }
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * Convert a `datetime-local` value to a UTC ISO string for the URL / API.
 *
 * @param value - Local datetime input value.
 */
function fromLocalInput(value: string): string | undefined {
    if (!value.trim()) {
        return undefined;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return undefined;
    }
    return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}
