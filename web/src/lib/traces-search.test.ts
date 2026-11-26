import { describe, expect, it } from "vitest";

import { isFlagEnabled, parseTracesSearch, tracesApiFilters } from "./traces-search";

describe("parseTracesSearch", () => {
    it("keeps trimmed string filters and flag encodings", () => {
        expect(
            parseTracesSearch({
                environment: " prod ",
                status: "running",
                agent: " ag1 ",
                since: "2026-01-01T00:00:00Z",
                until: "2026-01-02T00:00:00Z",
                has_denials: "1",
                has_errors: true,
                noise: "x",
            }),
        ).toEqual({
            environment: "prod",
            status: "running",
            agent: "ag1",
            since: "2026-01-01T00:00:00Z",
            until: "2026-01-02T00:00:00Z",
            has_denials: 1,
            has_errors: 1,
        });
    });

    it("omits blank and disabled flags", () => {
        expect(
            parseTracesSearch({
                status: "  ",
                has_denials: 0,
                has_errors: "false",
            }),
        ).toEqual({});
    });
});

describe("tracesApiFilters", () => {
    it("maps flags to booleans and forwards environment", () => {
        expect(
            tracesApiFilters({
                environment: "prod",
                status: "running",
                agent: "a1",
                since: "2026-01-01T00:00:00Z",
                has_denials: 1,
            }),
        ).toEqual({
            environment: "prod",
            status: "running",
            agent: "a1",
            since: "2026-01-01T00:00:00Z",
            has_denials: true,
        });
    });
});

describe("isFlagEnabled", () => {
    it("is true only for 1", () => {
        expect(isFlagEnabled(1)).toBe(true);
        expect(isFlagEnabled(undefined)).toBe(false);
        expect(isFlagEnabled(0)).toBe(false);
    });
});
