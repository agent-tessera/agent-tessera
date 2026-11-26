import { beforeEach, describe, expect, it } from "vitest";

import {
    EXPLORER_COLUMNS_KEY,
    readColumnVisibility,
    writeColumnVisibility,
} from "./explorer-columns";

describe("explorer column visibility", () => {
    beforeEach(() => {
        localStorage.removeItem(EXPLORER_COLUMNS_KEY);
    });

    it("defaults every column to visible", () => {
        const visibility = readColumnVisibility();
        expect(visibility.time).toBe(true);
        expect(visibility.duration).toBe(true);
        expect(visibility.run).toBe(true);
    });

    it("round-trips through localStorage", () => {
        writeColumnVisibility({
            ...readColumnVisibility(),
            kind: false,
            rule: false,
        });
        const next = readColumnVisibility();
        expect(next.kind).toBe(false);
        expect(next.rule).toBe(false);
        expect(next.agent).toBe(true);
    });
});
