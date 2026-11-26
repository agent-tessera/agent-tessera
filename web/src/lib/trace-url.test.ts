import { describe, expect, it } from "vitest";

import { externalTraceUrl } from "./trace-url";

describe("externalTraceUrl", () => {
    it("fills placeholders when template and ids are set", () => {
        expect(
            externalTraceUrl(
                "https://t.example/{trace_id}?s={span_id}",
                "abc",
                "1111111111111111",
            ),
        ).toBe("https://t.example/abc?s=1111111111111111");
    });

    it("returns null when template or trace id is missing", () => {
        expect(externalTraceUrl(undefined, "abc", "1111")).toBeNull();
        expect(externalTraceUrl("https://t/{trace_id}", null, "1111")).toBeNull();
        expect(externalTraceUrl("", "abc", "1111")).toBeNull();
    });
});
