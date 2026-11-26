/** Persist command-palette recent selections in `localStorage`. */

export const RECENT_SEARCH_KEY = "tessera-search-recent";

export const RECENT_SEARCH_LIMIT = 10;

/** A recently opened palette item. */
export type RecentSearchItem = {
    type: string;
    id: string;
    title: string;
    subtitle?: string;
    href: string;
};

/**
 * Read recent palette items (most recent first).
 *
 * @returns Up to {@link RECENT_SEARCH_LIMIT} items; empty when unavailable.
 */
export function readRecentSearch(): RecentSearchItem[] {
    if (typeof localStorage === "undefined") {
        return [];
    }
    try {
        const raw = localStorage.getItem(RECENT_SEARCH_KEY);
        if (!raw) {
            return [];
        }
        const parsed: unknown = JSON.parse(raw);
        if (!Array.isArray(parsed)) {
            return [];
        }
        const out: RecentSearchItem[] = [];
        for (const entry of parsed) {
            if (!entry || typeof entry !== "object") {
                continue;
            }
            const item = entry as Record<string, unknown>;
            if (
                typeof item.type !== "string" ||
                typeof item.id !== "string" ||
                typeof item.title !== "string" ||
                typeof item.href !== "string"
            ) {
                continue;
            }
            out.push({
                type: item.type,
                id: item.id,
                title: item.title,
                subtitle: typeof item.subtitle === "string" ? item.subtitle : undefined,
                href: item.href,
            });
            if (out.length >= RECENT_SEARCH_LIMIT) {
                break;
            }
        }
        return out;
    } catch {
        return [];
    }
}

/**
 * Push an item to the front of the recent list (deduped by type+id).
 *
 * @param item - Selected palette hit or action with an href.
 */
export function pushRecentSearch(item: RecentSearchItem): void {
    if (typeof localStorage === "undefined") {
        return;
    }
    const next = [
        item,
        ...readRecentSearch().filter(
            (existing) => !(existing.type === item.type && existing.id === item.id),
        ),
    ].slice(0, RECENT_SEARCH_LIMIT);
    localStorage.setItem(RECENT_SEARCH_KEY, JSON.stringify(next));
}
