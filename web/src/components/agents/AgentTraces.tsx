import { useInfiniteQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { runsKeys } from "@/api/keys";
import { fetchRuns } from "@/api/runs";
import { ErrorState } from "@/components/ErrorState";
import { RunsTable } from "@/components/traces/RunsTable";

export type AgentTracesProps = {
    agentName: string;
    agentNames: Record<string, string>;
};

const PAGE_SIZE = 50;

/** Traces tab: runs this agent took part in. */
export function AgentTraces({ agentName, agentNames }: AgentTracesProps) {
    const runsQuery = useInfiniteQuery({
        queryKey: runsKeys.list({ agent: agentName, limit: PAGE_SIZE }),
        queryFn: ({ pageParam }) =>
            fetchRuns({
                agent: agentName,
                limit: PAGE_SIZE,
                cursor: pageParam,
            }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    });

    const runs = useMemo(
        () => runsQuery.data?.pages.flatMap((page) => page.items) ?? [],
        [runsQuery.data],
    );

    if (runsQuery.isError) {
        return (
            <ErrorState
                title="Could not load traces"
                message={
                    runsQuery.error instanceof Error
                        ? runsQuery.error.message
                        : "Request failed"
                }
                onRetry={() => void runsQuery.refetch()}
            />
        );
    }

    return (
        <div className="space-y-3" data-testid="agent-traces">
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
        </div>
    );
}
