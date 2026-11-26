import { describe, expect, it } from "vitest";

import { brushToTimeRange, stackBucket, toExplorerIso } from "./explorer-histogram";

describe("stackBucket", () => {
    it("derives allow as residual of total minus deny and approval", () => {
        expect(
            stackBucket({
                t: "2026-11-09T00:00:00Z",
                total: 10,
                deny: 2,
                require_approval: 3,
                error: 1,
            }),
        ).toEqual({
            t: "2026-11-09T00:00:00Z",
            allow: 5,
            approval: 3,
            deny: 2,
            error: 1,
            total: 10,
        });
    });
});

describe("brushToTimeRange", () => {
    it("maps a pixel brush onto since/until ISO bounds", () => {
        const range = brushToTimeRange({
            x0: 0,
            x1: 50,
            width: 100,
            from: "2026-11-09T00:00:00Z",
            to: "2026-11-09T10:00:00Z",
        });
        expect(range).not.toBeNull();
        expect(range?.since).toBe("2026-11-09T00:00:00Z");
        expect(range?.until).toBe("2026-11-09T05:00:00Z");
    });

    it("returns null for a tiny brush", () => {
        expect(
            brushToTimeRange({
                x0: 10,
                x1: 11,
                width: 100,
                from: "2026-11-09T00:00:00Z",
                to: "2026-11-09T10:00:00Z",
            }),
        ).toBeNull();
    });
});

describe("toExplorerIso", () => {
    it("strips milliseconds", () => {
        expect(toExplorerIso(new Date("2026-11-09T19:00:00.123Z"))).toBe(
            "2026-11-09T19:00:00Z",
        );
    });
});
