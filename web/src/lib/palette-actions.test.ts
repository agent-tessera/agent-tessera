import { describe, expect, it } from "vitest";

import { filterStaticActions, G_SHORTCUTS } from "./palette-actions";

describe("filterStaticActions", () => {
    it("returns all actions for an empty query", () => {
        expect(filterStaticActions("").length).toBeGreaterThan(5);
    });

    it("filters by title substring", () => {
        const hits = filterStaticActions("theme");
        expect(hits.some((item) => item.id === "toggle-theme")).toBe(true);
        expect(hits.every((item) => /theme/i.test(item.title))).toBe(true);
    });
});

describe("G_SHORTCUTS", () => {
    it("maps section letters", () => {
        expect(G_SHORTCUTS.o).toBe("/overview");
        expect(G_SHORTCUTS.a).toBe("/agents");
        expect(G_SHORTCUTS.p).toBe("/approvals");
    });
});
