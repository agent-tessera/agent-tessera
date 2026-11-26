/**
 * Pure helpers for explorer live-tail merge and highlight bookkeeping.
 */

import type { InfiniteData } from "@tanstack/react-query";

import type { EventItem, EventList } from "@/api/events";

/**
 * Prepend stream events onto the first page of an infinite events query.
 *
 * Newer events are placed first. Duplicates (by id) already in any page are
 * skipped so resume / reconnect cannot double-insert.
 *
 * @param data - Current infinite query data (or undefined).
 * @param incoming - Events in arrival order (oldest → newest).
 * @returns Updated cache value, or the input when nothing changes.
 */
export function prependLiveEvents(
    data: InfiniteData<EventList> | undefined,
    incoming: readonly EventItem[],
): InfiniteData<EventList> | undefined {
    if (!data || incoming.length === 0) {
        return data;
    }
    const seen = new Set<string>();
    for (const page of data.pages) {
        for (const item of page.items) {
            seen.add(item.id);
        }
    }
    const fresh: EventItem[] = [];
    for (const item of incoming) {
        if (!item.id || seen.has(item.id)) {
            continue;
        }
        seen.add(item.id);
        fresh.push(item);
    }
    if (fresh.length === 0) {
        return data;
    }
    // Arrival is oldest→newest; table is newest-first.
    const ordered = [...fresh].reverse();
    const [first, ...rest] = data.pages;
    if (!first) {
        return {
            ...data,
            pages: [{ items: ordered, next_cursor: null }],
            pageParams: data.pageParams,
        };
    }
    return {
        ...data,
        pages: [{ ...first, items: [...ordered, ...first.items] }, ...rest],
    };
}

/**
 * Format the live-tail resume label.
 *
 * @param count - Buffered event count.
 * @returns Button / banner text.
 */
export function newEventsLabel(count: number): string {
    if (count <= 0) {
        return "0 new events";
    }
    if (count === 1) {
        return "1 new event";
    }
    return `${count} new events`;
}
