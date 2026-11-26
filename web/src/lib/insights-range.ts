/**
 * Resolve Insights time windows and stacked-chart bucket bounds.
 */

import type { InsightsRange } from "./insights-search";

const MS_MINUTE = 60_000;
const MS_HOUR = 60 * MS_MINUTE;
const MS_DAY = 24 * MS_HOUR;

const RANGE_MS: Record<InsightsRange, number> = {
    "15m": 15 * MS_MINUTE,
    "1h": MS_HOUR,
    "24h": MS_DAY,
    "7d": 7 * MS_DAY,
    "30d": 30 * MS_DAY,
};

/** Inclusive window for one Insights range preset. */
export type InsightsWindow = {
    from: Date;
    to: Date;
    range: InsightsRange;
};

/**
 * Inclusive `[from, to]` window ending at `now` for a range preset.
 *
 * @param range - Preset id.
 * @param now - Clock end (defaults to current time).
 */
export function insightsWindow(
    range: InsightsRange,
    now: Date = new Date(),
): InsightsWindow {
    const span = RANGE_MS[range];
    return {
        range,
        to: now,
        from: new Date(now.getTime() - span),
    };
}

/** One time slice used to build a stacked spend series. */
export type InsightsBucket = {
    from: Date;
    to: Date;
    label: string;
};

/**
 * Split a window into equal buckets for stacked spend-over-time charts.
 *
 * Caps at 24 buckets so Insights stays lean without a dedicated histogram.
 *
 * @param window - Inclusive time window.
 */
export function spendTimeBuckets(window: InsightsWindow): InsightsBucket[] {
    const spanMs = Math.max(1, window.to.getTime() - window.from.getTime());
    const count = bucketCountForRange(window.range);
    const width = Math.max(1, Math.floor(spanMs / count));
    const buckets: InsightsBucket[] = [];
    for (let index = 0; index < count; index += 1) {
        const startMs = window.from.getTime() + index * width;
        const endMs = index === count - 1 ? window.to.getTime() : startMs + width - 1;
        const from = new Date(startMs);
        const to = new Date(Math.max(startMs, endMs));
        buckets.push({
            from,
            to,
            label: formatBucketLabel(from, window.range),
        });
    }
    return buckets;
}

/**
 * Choose how many stacked buckets to fetch for a range.
 *
 * @param range - Preset id.
 */
export function bucketCountForRange(range: InsightsRange): number {
    switch (range) {
        case "15m":
            return 5;
        case "1h":
            return 6;
        case "24h":
            return 8;
        case "7d":
            return 7;
        case "30d":
            return 10;
        default:
            return 8;
    }
}

/**
 * Short axis label for one bucket start.
 *
 * @param moment - Bucket start.
 * @param range - Active preset.
 */
export function formatBucketLabel(moment: Date, range: InsightsRange): string {
    if (range === "7d" || range === "30d") {
        return moment.toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
        });
    }
    return moment.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
    });
}
