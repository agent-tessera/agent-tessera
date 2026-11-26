/**
 * Build an external tracing-tool URL from the server template.
 *
 * @param template - `TESSERA_SERVER_TRACE_URL` style string with `{trace_id}`
 *   and `{span_id}` placeholders, or null/undefined when unset.
 * @param traceId - OTel / backend trace id from the span.
 * @param spanId - Governance span id.
 * @returns A concrete URL, or `null` when the link should be hidden.
 */
export function externalTraceUrl(
    template: string | null | undefined,
    traceId: string | null | undefined,
    spanId: string,
): string | null {
    if (!template || !traceId || !spanId) {
        return null;
    }
    return template.replaceAll("{trace_id}", traceId).replaceAll("{span_id}", spanId);
}
