/** Client-side NDJSON export for explorer rows. */

/**
 * Serialize loaded event rows as NDJSON (one JSON object per line).
 *
 * @param rows - Event documents currently loaded in the table.
 */
export function eventsToNdjson(rows: readonly object[]): string {
    return (
        rows.map((row) => JSON.stringify(row)).join("\n") +
        (rows.length > 0 ? "\n" : "")
    );
}

/**
 * Trigger a browser download of NDJSON content.
 *
 * @param rows - Event documents to export.
 * @param filename - Download filename.
 */
export function downloadEventsNdjson(
    rows: readonly object[],
    filename = "tessera-events.ndjson",
): void {
    const body = eventsToNdjson(rows);
    const blob = new Blob([body], { type: "application/x-ndjson;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
