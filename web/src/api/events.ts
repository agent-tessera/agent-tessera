/** Event explorer fetchers (`GET /v1/events`, facets, histogram). */

import { apiRequest } from "./client";
import { queryString, type EventHistogram, type HistogramBucket } from "./overview";

export type { EventHistogram, HistogramBucket };

/** One event row from `GET /v1/events`. */
export type EventItem = {
    id: string;
    type?: string;
    ts?: string;
    agent?: string;
    agent_name?: string;
    agent_id?: string;
    kind?: string | null;
    resource?: string | null;
    effect?: string | null;
    status?: string | null;
    rule?: string | null;
    run?: string | null;
    span_id?: string | null;
    duration_ms?: number | null;
    [key: string]: unknown;
};

/** Paged event list. */
export type EventList = {
    items: EventItem[];
    next_cursor?: string | null;
};

/** One facet value with count. */
export type FacetValue = {
    value: string;
    count: number;
    /** Present on the agent facet (id alongside display name). */
    id?: string;
};

/** Facet document from `GET /v1/events/facets`. */
export type EventFacets = {
    facets: {
        agent?: FacetValue[];
        type?: FacetValue[];
        effect?: FacetValue[];
        status?: FacetValue[];
        kind?: FacetValue[];
    };
};

/** Filters accepted by event list, facet, and histogram endpoints. */
export type EventQueryFilters = {
    agent?: string[];
    type?: string[];
    effect?: string[];
    status?: string;
    kind?: string;
    resource?: string;
    rule?: string;
    run?: string;
    span?: string;
    since?: string;
    until?: string;
    limit?: number;
    cursor?: string;
    buckets?: number;
};

/**
 * Build a query string that repeats multi-value keys for FastAPI lists.
 *
 * @param filters - Event query filters.
 */
export function eventsQueryString(filters: EventQueryFilters): string {
    const params = new URLSearchParams();
    const appendAll = (key: string, values: string[] | undefined) => {
        if (!values) {
            return;
        }
        for (const value of values) {
            if (value) {
                params.append(key, value);
            }
        }
    };
    appendAll("agent", filters.agent);
    appendAll("type", filters.type);
    appendAll("effect", filters.effect);
    const singles = queryString({
        status: filters.status,
        kind: filters.kind,
        resource: filters.resource,
        rule: filters.rule,
        run: filters.run,
        span: filters.span,
        since: filters.since,
        until: filters.until,
        limit: filters.limit,
        cursor: filters.cursor,
        buckets: filters.buckets,
    });
    if (singles) {
        const extra = new URLSearchParams(singles.slice(1));
        for (const [key, value] of extra.entries()) {
            params.set(key, value);
        }
    }
    const text = params.toString();
    return text ? `?${text}` : "";
}

/**
 * List ingested events for the explorer.
 *
 * @param filters - Query filters and pagination.
 */
export async function fetchEvents(filters: EventQueryFilters = {}): Promise<EventList> {
    return apiRequest<EventList>(`/v1/events${eventsQueryString(filters)}`);
}

/**
 * Fetch facet counts for the current explorer filters.
 *
 * @param filters - Same filters as the event list (no cursor/limit).
 */
export async function fetchEventFacets(
    filters: EventQueryFilters = {},
): Promise<EventFacets> {
    const {
        limit: _limit,
        cursor: _cursor,
        buckets: _buckets,
        ...facetFilters
    } = filters;
    void _limit;
    void _cursor;
    void _buckets;
    return apiRequest<EventFacets>(
        `/v1/events/facets${eventsQueryString(facetFilters)}`,
    );
}

/**
 * Fetch a stacked event histogram for the current explorer filters.
 *
 * @param filters - Same filters as the event list plus optional `buckets`.
 */
export async function fetchEventsHistogram(
    filters: EventQueryFilters = {},
): Promise<EventHistogram> {
    const { limit: _limit, cursor: _cursor, ...histogramFilters } = filters;
    void _limit;
    void _cursor;
    return apiRequest<EventHistogram>(
        `/v1/events/histogram${eventsQueryString({
            ...histogramFilters,
            buckets: filters.buckets ?? 60,
        })}`,
    );
}
