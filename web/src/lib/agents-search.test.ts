import { describe, expect, it } from "vitest";

import {
    countAgentFacet,
    isAgentsFlagEnabled,
    isDormantAgent,
    parseAgentsSearch,
    splitAgentsCsv,
    toggleAgentsFacet,
} from "./agents-search";

describe("parseAgentsSearch", () => {
    it("trims string params and normalizes JSON-search flags", () => {
        expect(
            parseAgentsSearch({
                environment: " prod ",
                status: "active",
                autonomy: "",
                owner_id: " own-1 ",
                create: 1,
                orphaned: true,
                dormant: "yes",
                extra: "x",
            }),
        ).toEqual({
            environment: "prod",
            status: "active",
            owner_id: "own-1",
            create: 1,
            orphaned: 1,
            dormant: 1,
        });
    });
});

describe("agents facet helpers", () => {
    it("toggles CSV facet values", () => {
        const once = toggleAgentsFacet({}, "status", "active");
        expect(once).toEqual({ status: "active" });
        const twice = toggleAgentsFacet(once, "status", "draft");
        expect(splitAgentsCsv(twice.status)).toEqual(["active", "draft"]);
        const cleared = toggleAgentsFacet(twice, "status", "active");
        expect(cleared.status).toBe("draft");
    });

    it("counts facet values", () => {
        expect(
            countAgentFacet(
                [
                    { status: "active", environment: "prod", autonomy: "interactive" },
                    { status: "active", environment: "dev", autonomy: "assistive" },
                    { status: "draft", environment: "prod", autonomy: "interactive" },
                ],
                "environment",
            ),
        ).toEqual([
            { value: "prod", count: 2 },
            { value: "dev", count: 1 },
        ]);
    });

    it("parses flag values", () => {
        expect(isAgentsFlagEnabled("true")).toBe(true);
        expect(isAgentsFlagEnabled("1")).toBe(true);
        expect(isAgentsFlagEnabled(1)).toBe(true);
        expect(isAgentsFlagEnabled(true)).toBe(true);
        expect(isAgentsFlagEnabled("no")).toBe(false);
        expect(isAgentsFlagEnabled(0)).toBe(false);
    });

    it("detects dormant last-seen", () => {
        const now = new Date("2026-11-10T12:00:00Z");
        expect(isDormantAgent(null, now)).toBe(true);
        expect(isDormantAgent("2026-11-09T12:00:00Z", now)).toBe(false);
        expect(isDormantAgent("2026-10-01T12:00:00Z", now)).toBe(true);
    });
});
