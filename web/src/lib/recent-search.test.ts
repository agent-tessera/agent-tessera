import { beforeEach, describe, expect, it } from "vitest";

import { pushRecentSearch, readRecentSearch, RECENT_SEARCH_KEY } from "./recent-search";

describe("recent search", () => {
    beforeEach(() => {
        localStorage.removeItem(RECENT_SEARCH_KEY);
    });

    it("stores newest first and dedupes by type+id", () => {
        pushRecentSearch({
            type: "agent",
            id: "a1",
            title: "one",
            href: "/app/agents/a1",
        });
        pushRecentSearch({
            type: "agent",
            id: "a2",
            title: "two",
            href: "/app/agents/a2",
        });
        pushRecentSearch({
            type: "agent",
            id: "a1",
            title: "one-updated",
            href: "/app/agents/a1",
        });
        const recent = readRecentSearch();
        expect(recent).toHaveLength(2);
        expect(recent[0]?.id).toBe("a1");
        expect(recent[0]?.title).toBe("one-updated");
        expect(recent[1]?.id).toBe("a2");
    });
});
