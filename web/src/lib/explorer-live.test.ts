import { describe, expect, it } from "vitest";

import type { EventItem, EventList } from "@/api/events";
import type { InfiniteData } from "@tanstack/react-query";

import { newEventsLabel, prependLiveEvents } from "./explorer-live";

function page(items: EventItem[]): EventList {
    return { items, next_cursor: null };
}

function data(items: EventItem[]): InfiniteData<EventList> {
    return { pages: [page(items)], pageParams: [undefined] };
}

describe("prependLiveEvents", () => {
    it("prepends newest-first and skips duplicates", () => {
        const existing = data([
            { id: "2", ts: "2026-11-09T19:00:02Z" },
            { id: "1", ts: "2026-11-09T19:00:01Z" },
        ]);
        const next = prependLiveEvents(existing, [
            { id: "2", ts: "2026-11-09T19:00:02Z" },
            { id: "3", ts: "2026-11-09T19:00:03Z" },
            { id: "4", ts: "2026-11-09T19:00:04Z" },
        ]);
        expect(next?.pages[0]?.items.map((item) => item.id)).toEqual([
            "4",
            "3",
            "2",
            "1",
        ]);
    });

    it("returns the same reference when nothing is new", () => {
        const existing = data([{ id: "1", ts: "2026-11-09T19:00:01Z" }]);
        expect(
            prependLiveEvents(existing, [{ id: "1", ts: "2026-11-09T19:00:01Z" }]),
        ).toBe(existing);
    });
});

describe("newEventsLabel", () => {
    it("pluralizes", () => {
        expect(newEventsLabel(0)).toBe("0 new events");
        expect(newEventsLabel(1)).toBe("1 new event");
        expect(newEventsLabel(3)).toBe("3 new events");
    });
});
