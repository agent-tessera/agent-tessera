/**
 * String-preserving URL search codec for the console router.
 *
 * TanStack Router's default JSON search serializer quotes all-digit values
 * (`?span=%22…%22`). This codec keeps scalars as plain query strings so span
 * and run ids round-trip without coerce helpers.
 */

/**
 * Serialize a search object to a `?…` query string.
 *
 * @param search - Route search object.
 */
export function stringifySearch(search: Record<string, unknown>): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(search)) {
        if (value === undefined || value === null || value === "") {
            continue;
        }
        if (
            typeof value === "string" ||
            typeof value === "number" ||
            typeof value === "boolean"
        ) {
            params.set(key, String(value));
            continue;
        }
        params.set(key, JSON.stringify(value));
    }
    const encoded = params.toString();
    return encoded ? `?${encoded}` : "";
}

/**
 * Parse a query string into a search object of strings.
 *
 * @param searchStr - Raw location search (`?a=1` or `a=1`).
 */
export function parseSearch(searchStr: string): Record<string, string> {
    const trimmed = searchStr.startsWith("?") ? searchStr.slice(1) : searchStr;
    const params = new URLSearchParams(trimmed);
    const out: Record<string, string> = {};
    params.forEach((value, key) => {
        out[key] = value;
    });
    return out;
}
