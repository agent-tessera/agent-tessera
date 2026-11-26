/**
 * Subscribe to `/v1/events/stream` for the explorer live-tail toggle.
 *
 * Auto-pauses when the table scrolls away from the top or a row is open;
 * buffers arrivals as "N new events" until resume. Handles `lag` by
 * refetching the list query.
 */

import {
    useQueryClient,
    type InfiniteData,
    type QueryKey,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type { EventItem, EventList, EventQueryFilters } from "@/api/events";
import { eventsKeys } from "@/api/keys";
import { encodeEventCursor, newestEvent } from "@/lib/event-cursor";
import { openEventStream } from "@/lib/event-stream";
import { prependLiveEvents } from "@/lib/explorer-live";
import { TOGGLE_LIVE_TAIL_EVENT } from "@/lib/shell-events";

const HIGHLIGHT_MS = 1200;
const SCROLL_PAUSE_PX = 8;

export type UseExplorerLiveTailArgs = {
    live: boolean;
    /** Wait for the first list page before connecting (avoids replay). */
    ready: boolean;
    /** Drawer / selected event open → pause. */
    rowOpen: boolean;
    filters: EventQueryFilters;
    /** Flat items currently shown (for resume cursor). */
    items: readonly EventItem[];
    onToggleLive: (next: boolean) => void;
};

export type UseExplorerLiveTailResult = {
    paused: boolean;
    bufferedCount: number;
    highlightedIds: ReadonlySet<string>;
    scrollResetToken: number;
    onViewportScroll: (scrollTop: number) => void;
    resume: () => void;
};

/**
 * Wire SSE live tail into the explorer infinite-query cache.
 *
 * @param args - Live flag, filters, and pause signals.
 * @returns Pause/buffer state and table callbacks.
 */
export function useExplorerLiveTail(
    args: UseExplorerLiveTailArgs,
): UseExplorerLiveTailResult {
    const { live, ready, rowOpen, filters, items, onToggleLive } = args;
    const queryClient = useQueryClient();
    const [paused, setPaused] = useState(false);
    const [buffered, setBuffered] = useState<EventItem[]>([]);
    const [highlightedIds, setHighlightedIds] = useState<ReadonlySet<string>>(
        () => new Set(),
    );
    const [scrollResetToken, setScrollResetToken] = useState(0);

    const pausedRef = useRef(paused);
    const rowOpenRef = useRef(rowOpen);
    const itemsRef = useRef(items);
    const bufferRef = useRef(buffered);
    const cursorRef = useRef<string | null>(null);
    const highlightTimers = useRef<Map<string, number>>(new Map());

    pausedRef.current = paused;
    rowOpenRef.current = rowOpen;
    itemsRef.current = items;
    bufferRef.current = buffered;

    const listKey = eventsKeys.list(filters) as QueryKey;

    const clearHighlights = useCallback(() => {
        for (const timer of highlightTimers.current.values()) {
            window.clearTimeout(timer);
        }
        highlightTimers.current.clear();
        setHighlightedIds(new Set());
    }, []);

    const flashIds = useCallback((ids: string[]) => {
        if (ids.length === 0) {
            return;
        }
        setHighlightedIds((prev) => {
            const next = new Set(prev);
            for (const id of ids) {
                next.add(id);
            }
            return next;
        });
        for (const id of ids) {
            const existing = highlightTimers.current.get(id);
            if (existing !== undefined) {
                window.clearTimeout(existing);
            }
            const timer = window.setTimeout(() => {
                highlightTimers.current.delete(id);
                setHighlightedIds((prev) => {
                    if (!prev.has(id)) {
                        return prev;
                    }
                    const next = new Set(prev);
                    next.delete(id);
                    return next;
                });
            }, HIGHLIGHT_MS);
            highlightTimers.current.set(id, timer);
        }
    }, []);

    const applyIncoming = useCallback(
        (incoming: EventItem[]) => {
            if (incoming.length === 0) {
                return;
            }
            queryClient.setQueryData<InfiniteData<EventList>>(listKey, (current) =>
                prependLiveEvents(current, incoming),
            );
            flashIds(incoming.map((item) => item.id));
            const newest = newestEvent(incoming);
            if (newest && typeof newest.ts === "string") {
                cursorRef.current = encodeEventCursor(newest.ts, newest.id);
            }
        },
        [flashIds, listKey, queryClient],
    );

    const onViewportScroll = useCallback((scrollTop: number) => {
        if (scrollTop > SCROLL_PAUSE_PX) {
            setPaused(true);
        }
    }, []);

    const resume = useCallback(() => {
        const pending = bufferRef.current;
        setBuffered([]);
        setPaused(false);
        setScrollResetToken((token) => token + 1);
        if (pending.length > 0) {
            applyIncoming(pending);
        }
    }, [applyIncoming]);

    useEffect(() => {
        if (rowOpen) {
            setPaused(true);
        }
    }, [rowOpen]);

    useEffect(() => {
        const onToggle = () => {
            onToggleLive(!live);
        };
        window.addEventListener(TOGGLE_LIVE_TAIL_EVENT, onToggle);
        return () => window.removeEventListener(TOGGLE_LIVE_TAIL_EVENT, onToggle);
    }, [live, onToggleLive]);

    useEffect(() => {
        if (!live) {
            setPaused(false);
            setBuffered([]);
            clearHighlights();
            cursorRef.current = null;
            return;
        }
        if (!ready) {
            return;
        }

        const newest = newestEvent(itemsRef.current);
        if (newest && typeof newest.ts === "string") {
            cursorRef.current = encodeEventCursor(newest.ts, newest.id);
        } else {
            cursorRef.current = null;
        }

        const controller = new AbortController();
        const {
            limit: _limit,
            cursor: _cursor,
            buckets: _buckets,
            ...streamFilters
        } = filters;
        void _limit;
        void _cursor;
        void _buckets;

        void openEventStream(streamFilters, {
            lastEventId: cursorRef.current,
            signal: controller.signal,
            onEvent: (event, cursor) => {
                if (cursor) {
                    cursorRef.current = cursor;
                } else if (typeof event.ts === "string") {
                    cursorRef.current = encodeEventCursor(event.ts, event.id);
                }
                if (pausedRef.current || rowOpenRef.current) {
                    setPaused(true);
                    setBuffered((prev) => {
                        if (prev.some((item) => item.id === event.id)) {
                            return prev;
                        }
                        return [...prev, event];
                    });
                    return;
                }
                applyIncoming([event]);
            },
            onLag: () => {
                void queryClient.invalidateQueries({ queryKey: eventsKeys.all });
            },
        });

        return () => {
            controller.abort();
        };
        // Reconnect when filters change (listKey) or live/ready flip.
        // eslint-disable-next-line react-hooks/exhaustive-deps -- filters via listKey
    }, [applyIncoming, clearHighlights, live, listKey, queryClient, ready]);

    useEffect(() => {
        return () => {
            clearHighlights();
        };
    }, [clearHighlights]);

    return {
        paused,
        bufferedCount: buffered.length,
        highlightedIds,
        scrollResetToken,
        onViewportScroll,
        resume,
    };
}
