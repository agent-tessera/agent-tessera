import { describe, expect, it } from "vitest";

import {
    autocompleteDraft,
    explorerApiFilters,
    explorerChips,
    joinCsv,
    parseExplorerSearch,
    parseQueryInput,
    removeFilterValue,
    setFilterValue,
    splitCsv,
    toggleFacetValue,
} from "./explorer-search";

describe("parseExplorerSearch", () => {
    it("keeps trimmed filter, time, event, and live params", () => {
        expect(
            parseExplorerSearch({
                agent: "  palette-probe ",
                effect: "deny",
                since: "2026-01-01T00:00:00Z",
                event: "abc",
                live: "1",
                environment: "prod",
                junk: 1,
                empty: "  ",
            }),
        ).toEqual({
            agent: "palette-probe",
            effect: "deny",
            since: "2026-01-01T00:00:00Z",
            event: "abc",
            live: 1,
            environment: "prod",
        });
        expect(parseExplorerSearch({ live: 1 })).toEqual({ live: 1 });
        expect(parseExplorerSearch({ live: true })).toEqual({ live: 1 });
        expect(parseExplorerSearch({ live: 0 })).toEqual({});
    });
});

describe("csv helpers", () => {
    it("splits and joins distinct values", () => {
        expect(splitCsv("a, b, a")).toEqual(["a", "b"]);
        expect(joinCsv(["a", "b", "a"])).toBe("a,b");
        expect(joinCsv([])).toBeUndefined();
    });
});

describe("parseQueryInput", () => {
    it("maps free text to resource and known prefixes to tokens", () => {
        expect(parseQueryInput("files.read")).toEqual({
            kind: "resource",
            value: "files.read",
        });
        expect(parseQueryInput("agent:palette-probe")).toEqual({
            kind: "token",
            key: "agent",
            value: "palette-probe",
        });
        expect(parseQueryInput("effect:")).toEqual({ kind: "empty" });
        expect(parseQueryInput("unknown:x")).toEqual({
            kind: "resource",
            value: "unknown:x",
        });
    });
});

describe("autocompleteDraft", () => {
    it("exposes the active token prefix and partial", () => {
        expect(autocompleteDraft("type:dec")).toEqual({
            key: "type",
            partial: "dec",
        });
        expect(autocompleteDraft("files")).toBeNull();
    });
});

describe("filter mutations", () => {
    it("appends multi-keys and replaces single keys", () => {
        let search = setFilterValue({}, "agent", "a");
        search = setFilterValue(search, "agent", "b");
        expect(search.agent).toBe("a,b");
        search = setFilterValue(search, "status", "ok");
        search = setFilterValue(search, "status", "error");
        expect(search.status).toBe("error");
        search = removeFilterValue(search, "agent", "a");
        expect(search.agent).toBe("b");
        search = toggleFacetValue(search, "effect", "deny");
        expect(search.effect).toBe("deny");
        search = toggleFacetValue(search, "effect", "deny");
        expect(search.effect).toBeUndefined();
    });

    it("builds chips and API filters without environment", () => {
        const search = parseExplorerSearch({
            agent: "a,b",
            effect: "deny",
            resource: "shell",
            environment: "prod",
            since: "2026-01-01T00:00:00Z",
        });
        expect(explorerChips(search).map((chip) => chip.label)).toEqual([
            "agent:a",
            "agent:b",
            "effect:deny",
            "shell",
        ]);
        expect(explorerApiFilters(search)).toEqual({
            agent: ["a", "b"],
            type: undefined,
            effect: ["deny"],
            status: undefined,
            kind: undefined,
            resource: "shell",
            rule: undefined,
            run: undefined,
            span: undefined,
            since: "2026-01-01T00:00:00Z",
            until: undefined,
        });
    });
});
