/**
 * Opaque event cursors matching the server (`base64url` of `ts|id`).
 *
 * Used for SSE `Last-Event-ID` so live tail resumes after the newest loaded
 * row instead of replaying the initial page.
 */

/**
 * Encode a list/stream cursor as base64url of `ts|id`.
 *
 * @param ts - Event timestamp (ISO-8601, typically with `Z`).
 * @param eventId - Event id.
 * @returns Opaque cursor string (no padding).
 *
 * @example
 * ```ts
 * encodeEventCursor("2026-11-09T19:00:00Z", "ab".repeat(16));
 * ```
 */
export function encodeEventCursor(ts: string, eventId: string): string {
    const bytes = new TextEncoder().encode(`${ts}|${eventId}`);
    let binary = "";
    for (const byte of bytes) {
        binary += String.fromCharCode(byte);
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Pick the newest event by `(ts, id)` for stream resume.
 *
 * @param items - Loaded explorer rows (any order).
 * @returns Newest item, or `null` when empty / missing ids.
 */
export function newestEvent<T extends { id: string; ts?: string }>(
    items: readonly T[],
): T | null {
    let best: T | null = null;
    for (const item of items) {
        if (!item.id) {
            continue;
        }
        if (best === null) {
            best = item;
            continue;
        }
        const aTs = typeof item.ts === "string" ? item.ts : "";
        const bTs = typeof best.ts === "string" ? best.ts : "";
        if (aTs > bTs || (aTs === bTs && item.id > best.id)) {
            best = item;
        }
    }
    return best;
}
