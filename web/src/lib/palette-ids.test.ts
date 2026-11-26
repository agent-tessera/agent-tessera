import { describe, expect, it } from "vitest";

import {
    isLikelyRunId,
    isSpanId,
    parseSearchString,
    stripAppHref,
} from "./palette-ids";

describe("isSpanId", () => {
    it("accepts 16 hex characters", () => {
        expect(isSpanId("abcdef0123456789")).toBe(true);
        expect(isSpanId("ABCDEF0123456789")).toBe(true);
    });

    it("rejects other shapes", () => {
        expect(isSpanId("abcdef012345678")).toBe(false);
        expect(isSpanId("palette-probe")).toBe(false);
    });
});

describe("isLikelyRunId", () => {
    it("accepts ULIDs and opaque run slugs", () => {
        expect(isLikelyRunId("01JE2E2ESEED00000000000000")).toBe(true);
        expect(isLikelyRunId("run-search-99")).toBe(true);
    });

    it("rejects short noise", () => {
        expect(isLikelyRunId("ab")).toBe(false);
    });
});

describe("stripAppHref", () => {
    it("strips the /app prefix and splits search", () => {
        expect(stripAppHref("/app/traces/run-1?span=abc")).toEqual({
            pathname: "/traces/run-1",
            searchStr: "span=abc",
        });
    });
});

describe("parseSearchString", () => {
    it("parses query pairs", () => {
        expect(parseSearchString("span=abc&live=1")).toEqual({
            span: "abc",
            live: "1",
        });
    });
});
