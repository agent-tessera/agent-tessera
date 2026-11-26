/** Command-palette search against `GET /v1/search`. */

import { apiRequest } from "./client";

/** One hit returned by the console search API. */
export type SearchResult = {
    type: string;
    id: string;
    title: string;
    subtitle: string;
    href: string;
};

export type SearchResponse = {
    results: SearchResult[];
};

/**
 * Search agents, runs, approvals, spans, resources, and rules.
 *
 * @param q - Free-text query (server requires at least 2 characters).
 * @param limit - Max hits per type (1–20).
 */
export async function fetchSearch(
    q: string,
    limit: number = 8,
): Promise<SearchResponse> {
    const params = new URLSearchParams({
        q,
        limit: String(limit),
    });
    return apiRequest<SearchResponse>(`/v1/search?${params.toString()}`);
}
