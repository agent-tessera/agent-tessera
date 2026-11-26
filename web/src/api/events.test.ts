import { describe, expect, it } from "vitest";

import { eventsQueryString } from "./events";

describe("eventsQueryString", () => {
    it("repeats multi-value keys and skips empties", () => {
        expect(
            eventsQueryString({
                agent: ["palette-probe", "denial-spike"],
                effect: ["deny"],
                status: "ok",
                resource: "shell",
                limit: 50,
            }),
        ).toBe(
            "?agent=palette-probe&agent=denial-spike&effect=deny&status=ok&resource=shell&limit=50",
        );
        expect(eventsQueryString({})).toBe("");
    });
});
