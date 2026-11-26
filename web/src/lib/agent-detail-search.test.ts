import { describe, expect, it } from "vitest";

import { activeAgentTab, parseAgentDetailSearch } from "./agent-detail-search";

describe("parseAgentDetailSearch", () => {
    it("defaults to overview when section is omitted", () => {
        const search = parseAgentDetailSearch({});
        expect(search.section).toBeUndefined();
        expect(activeAgentTab(search)).toBe("overview");
    });

    it("keeps activity section and explorer filters", () => {
        const search = parseAgentDetailSearch({
            section: "activity",
            type: "decision",
            live: 1,
        });
        expect(search.section).toBe("activity");
        expect(search.type).toBe("decision");
        expect(search.live).toBe(1);
        expect(activeAgentTab(search)).toBe("activity");
    });

    it("accepts legacy tab= alias", () => {
        const search = parseAgentDetailSearch({ tab: "instances" });
        expect(search.section).toBe("instances");
    });

    it("drops unknown sections", () => {
        const search = parseAgentDetailSearch({ section: "nope" });
        expect(search.section).toBeUndefined();
    });
});
