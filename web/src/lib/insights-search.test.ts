import { describe, expect, it } from "vitest";

import {
    parseInsightsSearch,
    resolveDenialBy,
    resolveInsightsRange,
    resolveInsightsView,
    resolveSpendGroupBy,
} from "./insights-search";

describe("parseInsightsSearch", () => {
    it("keeps known trimmed string params", () => {
        expect(
            parseInsightsSearch({
                view: " denials ",
                range: "7d",
                group_by: "model",
                denial_by: "rule",
                environment: "prod",
                noise: 1,
            }),
        ).toEqual({
            view: "denials",
            range: "7d",
            group_by: "model",
            denial_by: "rule",
            environment: "prod",
        });
    });

    it("omits blank values", () => {
        expect(parseInsightsSearch({ view: "  ", range: "" })).toEqual({});
    });
});

describe("resolvers", () => {
    it("defaults view/range/groupings", () => {
        expect(resolveInsightsView(undefined)).toBe("spend");
        expect(resolveInsightsView("posture")).toBe("posture");
        expect(resolveInsightsView("quotas")).toBe("quotas");
        expect(resolveInsightsView("nope")).toBe("spend");
        expect(resolveInsightsRange(undefined)).toBe("24h");
        expect(resolveInsightsRange("1h")).toBe("1h");
        expect(resolveSpendGroupBy("model")).toBe("model");
        expect(resolveSpendGroupBy(undefined)).toBe("agent");
        expect(resolveDenialBy("rule")).toBe("rule");
        expect(resolveDenialBy(undefined)).toBe("agent");
    });
});
