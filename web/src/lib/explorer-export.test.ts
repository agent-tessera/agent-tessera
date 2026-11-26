import { describe, expect, it } from "vitest";

import { eventsToNdjson } from "./explorer-export";

describe("eventsToNdjson", () => {
    it("emits one JSON line per row and a trailing newline", () => {
        const text = eventsToNdjson([
            { id: "a", effect: "allow" },
            { id: "b", effect: "deny" },
        ]);
        const lines = text.trimEnd().split("\n");
        expect(lines).toHaveLength(2);
        expect(JSON.parse(lines[0]!)).toEqual({ id: "a", effect: "allow" });
        expect(JSON.parse(lines[1]!)).toEqual({ id: "b", effect: "deny" });
        expect(text.endsWith("\n")).toBe(true);
    });

    it("returns an empty string for no rows", () => {
        expect(eventsToNdjson([])).toBe("");
    });
});
