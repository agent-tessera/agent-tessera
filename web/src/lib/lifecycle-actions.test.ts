import { describe, expect, it } from "vitest";

import { lifecycleActionsForStatus } from "./lifecycle-actions";

describe("lifecycleActionsForStatus", () => {
    it("returns Activate for draft", () => {
        const actions = lifecycleActionsForStatus("draft", "probe");
        expect(actions.map((a) => a.name)).toEqual(["activate"]);
        expect(actions[0]?.primary).toBe(true);
    });

    it("returns Suspend and Retire for active", () => {
        const actions = lifecycleActionsForStatus("active", "probe");
        expect(actions.map((a) => a.name)).toEqual(["suspend", "retire"]);
    });

    it("returns Activate and Retire for suspended", () => {
        const actions = lifecycleActionsForStatus("suspended", "probe");
        expect(actions.map((a) => a.name)).toEqual(["activate", "retire"]);
    });

    it("returns no actions for retired", () => {
        expect(lifecycleActionsForStatus("retired", "probe")).toEqual([]);
    });
});
