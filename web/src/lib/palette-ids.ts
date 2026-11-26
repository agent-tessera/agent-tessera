/** Detect span and run identifiers pasted into the command palette. */

/** 16 lowercase/uppercase hex chars (OpenTelemetry-style span id). */
const SPAN_HEX = /^[0-9a-fA-F]{16}$/;

/**
 * Crockford base32 ULID (26 chars). Run ids from Tessera are often ULIDs;
 * agent ids share the shape, so paste-jump still confirms via search for runs.
 */
const ULID = /^[0-9A-HJKMNP-TV-Z]{26}$/i;

/**
 * Whether `value` is a full 16-hex span id.
 *
 * @param value - Trimmed palette input or clipboard text.
 */
export function isSpanId(value: string): boolean {
    return SPAN_HEX.test(value.trim());
}

/**
 * Whether `value` looks like a Tessera run id (ULID or opaque run slug).
 *
 * @param value - Trimmed palette input or clipboard text.
 */
export function isLikelyRunId(value: string): boolean {
    const text = value.trim();
    if (ULID.test(text)) {
        return true;
    }
    // Opaque run ids used in tests / older SDKs (e.g. `run-search-99`).
    return /^[0-9A-Za-z][0-9A-Za-z_-]{7,63}$/.test(text);
}

/**
 * Strip the `/app` base from a search `href` for TanStack Router navigation.
 *
 * @param href - Absolute console path from `GET /v1/search` (may include query).
 * @returns Pathname and search string without the `/app` prefix.
 */
export function stripAppHref(href: string): { pathname: string; searchStr: string } {
    const raw = href.startsWith("/app") ? href.slice(4) || "/" : href;
    const q = raw.indexOf("?");
    if (q === -1) {
        return { pathname: raw || "/", searchStr: "" };
    }
    return {
        pathname: raw.slice(0, q) || "/",
        searchStr: raw.slice(q + 1),
    };
}

/**
 * Parse a query string into a plain search object for the router.
 *
 * @param searchStr - Raw query without leading `?`.
 */
export function parseSearchString(
    searchStr: string,
): Record<string, string | undefined> {
    if (!searchStr) {
        return {};
    }
    const params = new URLSearchParams(searchStr);
    const out: Record<string, string | undefined> = {};
    for (const [key, value] of params.entries()) {
        out[key] = value;
    }
    return out;
}
