import { describe, expect, it, vi } from "vitest";

import { isSearchHotkey, OPEN_SEARCH_EVENT, requestOpenSearch } from "./search-events";

describe("isSearchHotkey", () => {
    it("matches Ctrl/Meta-K", () => {
        expect(
            isSearchHotkey(new KeyboardEvent("keydown", { key: "k", ctrlKey: true })),
        ).toBe(true);
        expect(
            isSearchHotkey(new KeyboardEvent("keydown", { key: "k", metaKey: true })),
        ).toBe(true);
    });

    it("matches / outside editable fields", () => {
        expect(isSearchHotkey(new KeyboardEvent("keydown", { key: "/" }))).toBe(true);
    });
});

describe("requestOpenSearch", () => {
    it("dispatches the open-search custom event", () => {
        const spy = vi.fn();
        window.addEventListener(OPEN_SEARCH_EVENT, spy);
        requestOpenSearch();
        expect(spy).toHaveBeenCalledOnce();
        window.removeEventListener(OPEN_SEARCH_EVENT, spy);
    });
});
