import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { fetchEvents } from "@/api/events";
import { eventsKeys } from "@/api/keys";
import { ErrorState } from "@/components/ErrorState";
import { EventTable } from "@/components/explorer/EventTable";
import { Button } from "@/components/ui/button";

export type EventsTabProps = {
    runId: string;
};

const PAGE_SIZE = 100;

/**
 * Explorer event table pre-filtered to `run:<id>` for the trace Events tab.
 */
export function EventsTab({ runId }: EventsTabProps) {
    const filters = useMemo(() => ({ run: runId }), [runId]);
    const [selectedId, setSelectedId] = useState<string | undefined>();

    const eventsQuery = useInfiniteQuery({
        queryKey: eventsKeys.list(filters),
        queryFn: ({ pageParam }) =>
            fetchEvents({
                ...filters,
                limit: PAGE_SIZE,
                cursor: pageParam,
            }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    });

    const items = useMemo(
        () => eventsQuery.data?.pages.flatMap((page) => page.items) ?? [],
        [eventsQuery.data],
    );

    if (eventsQuery.isError) {
        return (
            <ErrorState
                title="Could not load events"
                message={
                    eventsQuery.error instanceof Error
                        ? eventsQuery.error.message
                        : "Request failed"
                }
                onRetry={() => void eventsQuery.refetch()}
            />
        );
    }

    return (
        <div className="space-y-3" data-testid="trace-events">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-muted-foreground text-sm">
                    Filtered to <code className="text-foreground">run:{runId}</code>
                </p>
                <Button type="button" size="sm" variant="outline" asChild>
                    <Link
                        to="/explorer"
                        search={{ run: runId }}
                        data-testid="trace-events-open-explorer"
                    >
                        Open in Explorer
                    </Link>
                </Button>
            </div>
            <EventTable
                items={items}
                selectedId={selectedId}
                loading={eventsQuery.isLoading}
                loadingMore={eventsQuery.isFetchingNextPage}
                onSelect={setSelectedId}
                onEndReached={() => {
                    if (eventsQuery.hasNextPage && !eventsQuery.isFetchingNextPage) {
                        void eventsQuery.fetchNextPage();
                    }
                }}
            />
        </div>
    );
}
