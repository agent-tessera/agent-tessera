/**
 * Cookie-authenticated SSE client for `GET /v1/events/stream`.
 *
 * Uses `fetch` + `ReadableStream` so we can send `Last-Event-ID` and
 * credentials; standard `EventSource` cannot set custom headers.
 */

import {
    eventsQueryString,
    type EventItem,
    type EventQueryFilters,
} from "@/api/events";
import { TesseraApiError, redirectToLogin, appPathForLoginNext } from "@/api/client";

/** Stream filters (no pagination). */
export type EventStreamFilters = Omit<
    EventQueryFilters,
    "limit" | "cursor" | "buckets"
>;

export type EventStreamHandlers = {
    /** Called for each `data:` event payload. */
    onEvent: (event: EventItem, cursor: string | null) => void;
    /** Called when the server emits `event: lag`. */
    onLag?: () => void;
    /** Called on non-abort stream failures. */
    onError?: (error: unknown) => void;
};

export type OpenEventStreamOptions = EventStreamHandlers & {
    /** Resume cursor (`Last-Event-ID`). */
    lastEventId?: string | null;
    /** Abort to close the stream. */
    signal?: AbortSignal;
};

/**
 * Parse one SSE block (lines joined by `\n`, terminated by a blank line).
 *
 * @param block - Raw SSE event block without the trailing blank line.
 * @returns Parsed fields; empty when the block is only a comment/keepalive.
 */
export function parseSseBlock(block: string): {
    event: string;
    data: string;
    id: string | null;
} | null {
    const lines = block.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];
    let id: string | null = null;
    let sawField = false;
    for (const line of lines) {
        if (!line || line.startsWith(":")) {
            continue;
        }
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        let value = colon === -1 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) {
            value = value.slice(1);
        }
        sawField = true;
        if (field === "event") {
            eventName = value;
        } else if (field === "data") {
            dataLines.push(value);
        } else if (field === "id") {
            id = value;
        }
    }
    if (!sawField) {
        return null;
    }
    return { event: eventName, data: dataLines.join("\n"), id };
}

/**
 * Open an authenticated event stream.
 *
 * @param filters - Same filters as `GET /v1/events`.
 * @param options - Handlers, resume cursor, and abort signal.
 * @returns Promise that settles when the stream ends or aborts.
 */
export async function openEventStream(
    filters: EventStreamFilters,
    options: OpenEventStreamOptions,
): Promise<void> {
    const headers = new Headers({
        Accept: "text/event-stream",
    });
    if (options.lastEventId) {
        headers.set("Last-Event-ID", options.lastEventId);
    }

    let response: Response;
    try {
        response = await fetch(`/v1/events/stream${eventsQueryString(filters)}`, {
            method: "GET",
            credentials: "include",
            headers,
            signal: options.signal,
        });
    } catch (error) {
        if (options.signal?.aborted) {
            return;
        }
        options.onError?.(error);
        return;
    }

    if (response.status === 401) {
        redirectToLogin(appPathForLoginNext());
        options.onError?.(
            new TesseraApiError(401, "unauthorized", "authentication required"),
        );
        return;
    }

    if (!response.ok) {
        options.onError?.(
            new TesseraApiError(
                response.status,
                "http_error",
                `HTTP ${response.status}`,
            ),
        );
        return;
    }

    const body = response.body;
    if (!body) {
        options.onError?.(new Error("event stream has no body"));
        return;
    }

    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
            let sep = buffer.indexOf("\n\n");
            while (sep !== -1) {
                const block = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                const parsed = parseSseBlock(block);
                if (parsed) {
                    dispatchSse(parsed, options);
                }
                sep = buffer.indexOf("\n\n");
            }
        }
    } catch (error) {
        if (options.signal?.aborted) {
            return;
        }
        options.onError?.(error);
    } finally {
        reader.releaseLock();
    }
}

function dispatchSse(
    parsed: { event: string; data: string; id: string | null },
    options: EventStreamHandlers,
): void {
    if (parsed.event === "lag") {
        options.onLag?.();
        return;
    }
    if (!parsed.data) {
        return;
    }
    try {
        const item = JSON.parse(parsed.data) as EventItem;
        if (typeof item.id === "string" && item.id) {
            options.onEvent(item, parsed.id);
        }
    } catch (error) {
        options.onError?.(error);
    }
}
