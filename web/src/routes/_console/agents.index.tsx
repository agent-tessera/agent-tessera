import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { fetchAgents, fetchOwners } from "@/api/agents";
import { agentsKeys, dashboardsKeys, ownersKeys, postureKeys } from "@/api/keys";
import { fetchDenialsDashboard, fetchPostureBoard } from "@/api/overview";
import { AgentsFacetSidebar } from "@/components/agents/AgentsFacetSidebar";
import { AgentsTable } from "@/components/agents/AgentsTable";
import { CreateAgentDialog } from "@/components/agents/CreateAgentDialog";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import {
    isAgentsFlagEnabled,
    isDormantAgent,
    parseAgentsSearch,
    splitAgentsCsv,
    type AgentsSearch,
} from "@/lib/agents-search";
import { setPageTitle } from "@/lib/page-title";

export type { AgentsSearch };

export const Route = createFileRoute("/_console/agents/")({
    validateSearch: parseAgentsSearch,
    component: AgentsPage,
});

const PAGE_SIZE = 100;
const MS_DAY = 24 * 60 * 60 * 1000;

/**
 * Agents registry list with facets and create dialog.
 */
function AgentsPage() {
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const queryClient = useQueryClient();

    useEffect(() => {
        setPageTitle("Agents");
    }, []);

    const patchSearch = (patch: Partial<AgentsSearch> | AgentsSearch) => {
        void navigate({
            search: (prev) => {
                const next: AgentsSearch = { ...prev, ...patch };
                for (const key of Object.keys(patch) as Array<keyof AgentsSearch>) {
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

    const createOpen = isAgentsFlagEnabled(search.create);
    const orphanedOnly = isAgentsFlagEnabled(search.orphaned);
    const dormantOnly = isAgentsFlagEnabled(search.dormant);

    const openCreate = () => patchSearch({ create: 1 });
    const closeCreate = () => patchSearch({ create: undefined });

    const apiEnvironment = useMemo(() => {
        const values = splitAgentsCsv(search.environment);
        return values.length === 1 ? values[0] : undefined;
    }, [search.environment]);

    const agentsQuery = useInfiniteQuery({
        queryKey: agentsKeys.list({
            environment: apiEnvironment,
            owner_id: search.owner_id,
            orphaned: orphanedOnly || undefined,
            limit: PAGE_SIZE,
        }),
        queryFn: ({ pageParam }) =>
            fetchAgents({
                environment: apiEnvironment,
                owner_id: search.owner_id,
                orphaned: orphanedOnly || undefined,
                limit: PAGE_SIZE,
                cursor: pageParam,
            }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    });

    const ownersQuery = useQuery({
        queryKey: ownersKeys.list({ limit: 200 }),
        queryFn: () => fetchOwners({ limit: 200 }),
    });

    const postureQuery = useQuery({
        queryKey: postureKeys.summary({ environment: search.environment }),
        queryFn: fetchPostureBoard,
    });

    const now = useMemo(() => new Date(), []);
    const dayAgo = useMemo(() => new Date(now.getTime() - MS_DAY), [now]);

    const denialsQuery = useQuery({
        queryKey: dashboardsKeys.denials({
            window: "24h",
            environment: search.environment,
        }),
        queryFn: () => fetchDenialsDashboard(dayAgo, now),
    });

    const allAgents = useMemo(
        () => agentsQuery.data?.pages.flatMap((page) => page.items) ?? [],
        [agentsQuery.data],
    );

    const filteredAgents = useMemo(() => {
        const statusFacet = new Set(splitAgentsCsv(search.status));
        const autonomyFacet = new Set(splitAgentsCsv(search.autonomy));
        return allAgents.filter((agent) => {
            if (statusFacet.size > 0 && !statusFacet.has(agent.status)) {
                return false;
            }
            if (autonomyFacet.size > 0 && !autonomyFacet.has(agent.autonomy)) {
                return false;
            }
            if (dormantOnly && !isDormantAgent(agent.last_seen_at, now)) {
                return false;
            }
            return true;
        });
    }, [allAgents, search.status, search.autonomy, dormantOnly, now]);

    const ownerNames = useMemo(() => {
        const map: Record<string, string> = {};
        for (const owner of ownersQuery.data?.items ?? []) {
            map[owner.id] = owner.display_name || owner.email;
        }
        return map;
    }, [ownersQuery.data]);

    const postureById = useMemo(() => {
        const map: Record<string, { score: number; permissive?: boolean }> = {};
        for (const row of postureQuery.data ?? []) {
            map[row.id] = {
                score: Number(row.score) || 0,
                permissive: row.permissive,
            };
        }
        return map;
    }, [postureQuery.data]);

    const denialsById = useMemo(() => {
        const map: Record<string, number> = {};
        for (const row of denialsQuery.data?.items ?? []) {
            map[row.agent_id] = row.denials;
        }
        return map;
    }, [denialsQuery.data]);

    const loadError = agentsQuery.isError
        ? agentsQuery.error
        : ownersQuery.isError
          ? ownersQuery.error
          : postureQuery.isError
            ? postureQuery.error
            : denialsQuery.isError
              ? denialsQuery.error
              : null;

    const onFacetChange = (next: AgentsSearch) => {
        void navigate({
            search: () => {
                const out: AgentsSearch = { ...next };
                for (const key of Object.keys(out) as Array<keyof AgentsSearch>) {
                    if (out[key] === undefined || out[key] === "") {
                        delete out[key];
                    }
                }
                return out;
            },
            replace: true,
        });
    };

    const onCreated = (agentId: string) => {
        void queryClient.invalidateQueries({ queryKey: agentsKeys.all });
        void navigate({
            to: "/agents/$agentId",
            params: { agentId },
            search: (prev) => {
                const next = { ...prev } as AgentsSearch;
                delete next.create;
                return next;
            },
        });
    };

    return (
        <div className="space-y-6" data-testid="agents-page">
            <PageHeader
                title="Agents"
                description="Registry, lifecycle, policy, and connect."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Agents" },
                ]}
                actions={
                    <Button
                        type="button"
                        size="sm"
                        data-testid="agents-create"
                        onClick={openCreate}
                    >
                        Create agent
                    </Button>
                }
            />

            {loadError ? (
                <ErrorState
                    message={
                        loadError instanceof Error
                            ? loadError.message
                            : "Failed to load agents"
                    }
                    onRetry={() => {
                        void agentsQuery.refetch();
                        void ownersQuery.refetch();
                        void postureQuery.refetch();
                        void denialsQuery.refetch();
                    }}
                />
            ) : (
                <div className="flex flex-col gap-6 lg:flex-row">
                    <AgentsFacetSidebar
                        className="lg:w-52 lg:shrink-0"
                        search={search}
                        agents={allAgents}
                        loading={agentsQuery.isPending}
                        onSearchChange={onFacetChange}
                    />
                    <div className="min-w-0 flex-1 space-y-3">
                        {(orphanedOnly || dormantOnly) && (
                            <div
                                className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs"
                                data-testid="agents-active-flags"
                            >
                                {orphanedOnly ? (
                                    <FlagChip
                                        label="Orphaned"
                                        onClear={() =>
                                            patchSearch({ orphaned: undefined })
                                        }
                                    />
                                ) : null}
                                {dormantOnly ? (
                                    <FlagChip
                                        label="Dormant"
                                        onClear={() =>
                                            patchSearch({ dormant: undefined })
                                        }
                                    />
                                ) : null}
                            </div>
                        )}
                        <AgentsTable
                            agents={filteredAgents}
                            ownerNames={ownerNames}
                            postureById={postureById}
                            denialsById={denialsById}
                            loading={agentsQuery.isPending}
                            loadingMore={agentsQuery.isFetchingNextPage}
                            onEndReached={() => {
                                if (
                                    agentsQuery.hasNextPage &&
                                    !agentsQuery.isFetchingNextPage
                                ) {
                                    void agentsQuery.fetchNextPage();
                                }
                            }}
                        />
                    </div>
                </div>
            )}

            <CreateAgentDialog
                open={createOpen}
                defaultEnvironment={search.environment ?? "dev"}
                onClose={closeCreate}
                onCreated={onCreated}
            />
        </div>
    );
}

function FlagChip({ label, onClear }: { label: string; onClear: () => void }) {
    return (
        <button
            type="button"
            className="border-border bg-muted hover:bg-muted/80 inline-flex items-center gap-1 rounded-md border px-2 py-1"
            onClick={onClear}
        >
            <span>{label}</span>
            <span aria-hidden>×</span>
        </button>
    );
}
