import { describe, expect, it } from "vitest";

import { environmentLabel, parseEnvironmentSearch } from "./environment";

describe("parseEnvironmentSearch", () => {
    it("returns the trimmed environment slug", () => {
        expect(parseEnvironmentSearch({ environment: " prod " })).toEqual({
            environment: "prod",
        });
    });

    it("omits empty or non-string values", () => {
        expect(parseEnvironmentSearch({})).toEqual({});
        expect(parseEnvironmentSearch({ environment: "" })).toEqual({});
        expect(parseEnvironmentSearch({ environment: 1 })).toEqual({});
    });
});

describe("environmentLabel", () => {
    it("labels the all-environments filter", () => {
        expect(environmentLabel(undefined)).toBe("All environments");
        expect(environmentLabel("dev")).toBe("dev");
    });
});
