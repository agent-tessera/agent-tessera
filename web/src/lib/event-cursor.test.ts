import { describe, expect, it } from "vitest";

import { encodeEventCursor, newestEvent } from "./event-cursor";

describe("event cursor", () => {
    it("encodes base64url of ts|id without padding", () => {
        const cursor = encodeEventCursor("2026-11-09T19:00:00Z", "ab".repeat(16));
        expect(cursor).not.toMatch(/=/);
        expect(cursor).not.toMatch(/[+/]/);
        const padded = cursor + "=".repeat((4 - (cursor.length % 4)) % 4);
        const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
        expect(raw).toBe(`2026-11-09T19:00:00Z|${"ab".repeat(16)}`);
    });

    it("picks the newest event by ts then id", () => {
        expect(
            newestEvent([
                { id: "a", ts: "2026-11-09T19:00:00Z" },
                { id: "c", ts: "2026-11-09T19:00:01Z" },
                { id: "b", ts: "2026-11-09T19:00:01Z" },
            ])?.id,
        ).toBe("c");
        expect(newestEvent([])).toBeNull();
    });
});
