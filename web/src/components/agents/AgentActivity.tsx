import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import {
    fetchEventFacets,
    fetchEvents,
    fetchEventsHistogram,
    type EventItem,
    type FacetValue,
} from "@/api/events";
import { eventsKeys } from "@/api/keys";
import { ErrorState } from "@/components/ErrorState";
import { EventDrawer } from "@/components/explorer/EventDrawer";
import { EventHistogram } from "@/components/explorer/EventHistogram";
import { EventTable } from "@/components/explorer/EventTable";
import { FacetSidebar } from "@/components/explorer/FacetSidebar";
import { QueryBar } from "@/components/explorer/QueryBar";
import { downloadEventsNdjson } from "@/lib/explorer-export";
import {
    explorerApiFilters,
    EXPLORER_TOKEN_KEYS,
    type ExplorerSearch,
    type ExplorerTokenKey,
} from "@/lib/explorer-search";

export type AgentActivityProps = {
    agentName: string;
    search: ExplorerSearch;
    onSearchChange: (next: ExplorerSearch) => void;
};

const PAGE_SIZE = 100;

/**
 * Embedded explorer for one agent (`agent:` filter fixed).
 */
export function AgentActivity({
    agentName,
    search,
    onSearchChange,
}: AgentActivityProps) {
    const lockedSearch = useMemo(
        () => ({ ...search, agent: agentName }),
        [search, agentName],
    );
    const apiFilters = useMemo(() => explorerApiFilters(lockedSearch), [lockedSearch]);

    const patchLocked = (next: ExplorerSearch) => {
        onSearchChange({ ...next, agent: agentName });
    };

    const eventsQuery = useInfiniteQuery({
        queryKey: eventsKeys.list(apiFilters),
        queryFn: ({ pageParam }) =>
            fetchEvents({
                ...apiFilters,
                limit: PAGE_SIZE,
                cursor: pageParam,
            }),
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    });

    const facetsQuery = useQuery({
        queryKey: eventsKeys.facets(apiFilters),
        queryFn: () => fetchEventFacets(apiFilters),
    });

    const histogramQuery = useQuery({
        queryKey: eventsKeys.histogram({ ...apiFilters, buckets: 60 }),
        queryFn: () => fetchEventsHistogram({ ...apiFilters, buckets: 60 }),
    });

    const facetValues = useMemo(() => {
        const facets = facetsQuery.data?.facets ?? {};
        const out: Partial<Record<ExplorerTokenKey, FacetValue[]>> = {};
        for (const key of EXPLORER_TOKEN_KEYS) {
            if (key === "rule" || key === "run" || key === "span" || key === "agent") {
                continue;
            }
            out[key] = facets[key] ?? [];
        }
        return out;
    }, [facetsQuery.data]);

    const items = useMemo(
        () => eventsQuery.data?.pages.flatMap((page) => page.items) ?? [],
        [eventsQuery.data],
    );

    const selectedEvent: EventItem | null = useMemo(() => {
        if (!lockedSearch.event) {
            return null;
        }
        return items.find((item) => item.id === lockedSearch.event) ?? null;
    }, [items, lockedSearch.event]);

    const loadError = eventsQuery.isError
        ? eventsQuery.error
        : facetsQuery.isError
          ? facetsQuery.error
          : histogramQuery.isError
            ? histogramQuery.error
            : null;

    return (
        <div className="space-y-4" data-testid="agent-activity">
            <p className="text-muted-foreground text-sm">
                Events for{" "}
                <span className="text-foreground font-medium">{agentName}</span>.{" "}
                <Link
                    to="/explorer"
                    search={{ agent: agentName }}
                    className="underline-offset-2 hover:underline"
                >
                    Open in Explorer
                </Link>
            </p>

            <QueryBar
                search={lockedSearch}
                facetValues={facetValues}
                onSearchChange={(next) => {
                    // Keep agent: fixed even if the chip is removed.
                    patchLocked({ ...next, agent: agentName });
                }}
            />

            <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
                <FacetSidebar
                    search={lockedSearch}
                    facets={facetsQuery.data?.facets}
                    loading={facetsQuery.isPending}
                    onSearchChange={(next) =>
                        patchLocked({ ...next, agent: agentName })
                    }
                />

                <div className="min-w-0 space-y-4">
                    {loadError ? (
                        <ErrorState
                            message={
                                loadError instanceof Error
                                    ? loadError.message
                                    : "Failed to load activity"
                            }
                            onRetry={() => {
                                void eventsQuery.refetch();
                                void facetsQuery.refetch();
                                void histogramQuery.refetch();
                            }}
                        />
                    ) : (
                        <>
                            <EventHistogram
                                data={histogramQuery.data}
                                loading={histogramQuery.isPending}
                                onBrush={(range) =>
                                    patchLocked({
                                        ...lockedSearch,
                                        since: range.since,
                                        until: range.until,
                                    })
                                }
                            />
                            <EventTable
                                items={items}
                                selectedId={lockedSearch.event}
                                loading={eventsQuery.isPending}
                                loadingMore={eventsQuery.isFetchingNextPage}
                                onSelect={(eventId) =>
                                    patchLocked({
                                        ...lockedSearch,
                                        event:
                                            lockedSearch.event === eventId
                                                ? undefined
                                                : eventId,
                                    })
                                }
                                onEndReached={() => {
                                    if (
                                        eventsQuery.hasNextPage &&
                                        !eventsQuery.isFetchingNextPage
                                    ) {
                                        void eventsQuery.fetchNextPage();
                                    }
                                }}
                                onExport={() => downloadEventsNdjson(items)}
                            />
                        </>
                    )}
                </div>
            </div>

            <EventDrawer
                event={selectedEvent}
                open={Boolean(lockedSearch.event && selectedEvent)}
                search={lockedSearch}
                onSearchChange={(next) => patchLocked({ ...next, agent: agentName })}
                onClose={() => patchLocked({ ...lockedSearch, event: undefined })}
            />
        </div>
    );
}
