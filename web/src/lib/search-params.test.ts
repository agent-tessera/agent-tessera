import { describe, expect, it } from "vitest";

import { parseSearch, stringifySearch } from "./search-params";

describe("search-params", () => {
    it("round-trips all-digit span ids as strings", () => {
        const encoded = stringifySearch({ span: "3333333333333333", tab: "tree" });
        expect(encoded).toBe("?span=3333333333333333&tab=tree");
        expect(parseSearch(encoded)).toEqual({
            span: "3333333333333333",
            tab: "tree",
        });
    });

    it("omits empty values", () => {
        expect(stringifySearch({ span: "", environment: undefined })).toBe("");
    });
});
