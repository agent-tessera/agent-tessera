import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo } from "react";

import {
    fetchEventFacets,
    fetchEvents,
    fetchEventsHistogram,
    type EventItem,
    type FacetValue,
} from "@/api/events";
import { eventsKeys } from "@/api/keys";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { EventDrawer } from "@/components/explorer/EventDrawer";
import { EventHistogram } from "@/components/explorer/EventHistogram";
import { EventTable } from "@/components/explorer/EventTable";
import { FacetSidebar } from "@/components/explorer/FacetSidebar";
import { QueryBar } from "@/components/explorer/QueryBar";
import { downloadEventsNdjson } from "@/lib/explorer-export";
import {
    explorerApiFilters,
    EXPLORER_TOKEN_KEYS,
    isLiveEnabled,
    parseExplorerSearch,
    type ExplorerSearch,
    type ExplorerTokenKey,
} from "@/lib/explorer-search";
import { setPageTitle } from "@/lib/page-title";
import { useExplorerLiveTail } from "@/lib/use-explorer-live-tail";

export type { ExplorerSearch };

export const Route = createFileRoute("/_console/explorer")({
    validateSearch: parseExplorerSearch,
    component: ExplorerPage,
});

const PAGE_SIZE = 100;

/**
 * Event explorer: query bar, facets, histogram, table, drawer, and live tail.
 */
function ExplorerPage() {
    const search = Route.useSearch();
    const navigate = useNavigate({ from: Route.fullPath });
    const apiFilters = useMemo(() => explorerApiFilters(search), [search]);

    useEffect(() => {
        setPageTitle("Explorer");
    }, []);

    const updateSearch = (next: ExplorerSearch) => {
        void navigate({
            search: () => next,
            replace: true,
        });
    };

    const patchSearch = (patch: Partial<ExplorerSearch>) => {
        const next: ExplorerSearch = { ...search, ...patch };
        for (const key of Object.keys(patch) as Array<keyof ExplorerSearch>) {
            if (patch[key] === undefined || patch[key] === "") {
                delete next[key];
            }
        }
        updateSearch(next);
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
            if (key === "rule" || key === "run" || key === "span") {
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
        if (!search.event) {
            return null;
        }
        return items.find((item) => item.id === search.event) ?? null;
    }, [items, search.event]);

    const liveOn = isLiveEnabled(search);
    const onToggleLive = useCallback(
        (next: boolean) => {
            void navigate({
                search: (prev) => {
                    const nextSearch = { ...prev };
                    if (next) {
                        nextSearch.live = 1;
                    } else {
                        delete nextSearch.live;
                    }
                    return nextSearch;
                },
                replace: true,
            });
        },
        [navigate],
    );

    const live = useExplorerLiveTail({
        live: liveOn,
        ready: !eventsQuery.isPending,
        rowOpen: Boolean(search.event),
        filters: apiFilters,
        items,
        onToggleLive,
    });

    const loadError = eventsQuery.isError
        ? eventsQuery.error
        : facetsQuery.isError
          ? facetsQuery.error
          : histogramQuery.isError
            ? histogramQuery.error
            : null;

    return (
        <div className="space-y-6" data-testid="explorer-page">
            <PageHeader
                title="Explorer"
                description="Query events with shareable filters, histogram zoom, and NDJSON export."
                breadcrumbs={[
                    { label: "Console", href: "/app/overview" },
                    { label: "Explorer" },
                ]}
                actions={
                    <label className="text-muted-foreground flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            className="accent-primary size-4"
                            checked={liveOn}
                            data-testid="explorer-live-toggle"
                            onChange={(event) =>
                                patchSearch({
                                    live: event.target.checked ? 1 : undefined,
                                })
                            }
                        />
                        Live
                        {liveOn && live.paused ? (
                            <span
                                className="text-muted-foreground text-xs"
                                data-testid="explorer-live-paused"
                            >
                                (paused)
                            </span>
                        ) : null}
                    </label>
                }
            />

            <QueryBar
                search={search}
                facetValues={facetValues}
                onSearchChange={updateSearch}
            />

            <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
                <FacetSidebar
                    search={search}
                    facets={facetsQuery.data?.facets}
                    loading={facetsQuery.isPending}
                    onSearchChange={updateSearch}
                />

                <div className="min-w-0 space-y-4">
                    <div
                        className="flex flex-wrap items-end gap-3"
                        data-testid="explorer-time-range"
                    >
                        <label className="text-muted-foreground flex flex-col gap-1 text-xs">
                            Since
                            <input
                                type="datetime-local"
                                className="border-border bg-panel text-foreground h-9 rounded-md border px-2 text-sm"
                                value={toLocalInput(search.since)}
                                data-testid="explorer-since"
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
                                data-testid="explorer-until"
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
                                className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
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
                                    : "Failed to load explorer data"
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
                                    patchSearch({
                                        since: range.since,
                                        until: range.until,
                                    })
                                }
                            />
                            <EventTable
                                items={items}
                                selectedId={search.event}
                                loading={eventsQuery.isPending}
                                loadingMore={eventsQuery.isFetchingNextPage}
                                highlightedIds={live.highlightedIds}
                                liveBufferedCount={live.bufferedCount}
                                onLiveResume={live.resume}
                                onViewportScroll={live.onViewportScroll}
                                scrollResetToken={live.scrollResetToken}
                                onSelect={(eventId) =>
                                    patchSearch({
                                        event:
                                            search.event === eventId
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
                open={Boolean(search.event && selectedEvent)}
                search={search}
                onSearchChange={updateSearch}
                onClose={() => patchSearch({ event: undefined })}
            />
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
