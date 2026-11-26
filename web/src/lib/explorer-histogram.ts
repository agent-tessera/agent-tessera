/** Pure helpers for the explorer stacked histogram and brush zoom. */

/** One bucket from `GET /v1/events/histogram`. */
export type ExplorerHistogramBucket = {
    t: string;
    total: number;
    deny: number;
    require_approval: number;
    error: number;
};

/** Stacked segment counts for one bucket. */
export type StackedBucket = {
    t: string;
    allow: number;
    approval: number;
    deny: number;
    error: number;
    total: number;
};

/**
 * Derive stacked allow / approval / deny / error counts.
 *
 * Allow is the residual after deny and require_approval so the first three
 * layers sum to `total`. Error (a status, not an effect) is stacked on top.
 *
 * @param bucket - Raw histogram bucket.
 */
export function stackBucket(bucket: ExplorerHistogramBucket): StackedBucket {
    const deny = Math.max(0, Number(bucket.deny) || 0);
    const approval = Math.max(0, Number(bucket.require_approval) || 0);
    const error = Math.max(0, Number(bucket.error) || 0);
    const total = Math.max(0, Number(bucket.total) || 0);
    const allow = Math.max(0, total - deny - approval);
    return { t: bucket.t, allow, approval, deny, error, total };
}

/**
 * Format a Date as a UTC ISO string without milliseconds (URL / API form).
 *
 * @param date - Instant to format.
 */
export function toExplorerIso(date: Date): string {
    return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Map a brush pixel range onto histogram time bounds.
 *
 * @param options - Chart geometry and histogram window.
 * @returns `since` / `until` ISO strings, or `null` when the brush is empty.
 */
export function brushToTimeRange(options: {
    x0: number;
    x1: number;
    width: number;
    from: string;
    to: string;
}): { since: string; until: string } | null {
    const width = options.width;
    if (!(width > 0)) {
        return null;
    }
    const fromMs = Date.parse(options.from);
    const toMs = Date.parse(options.to);
    if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) {
        return null;
    }
    const left = Math.min(options.x0, options.x1);
    const right = Math.max(options.x0, options.x1);
    if (right - left < 2) {
        return null;
    }
    const clamp = (x: number) => Math.min(width, Math.max(0, x));
    const startMs = fromMs + (clamp(left) / width) * (toMs - fromMs);
    const endMs = fromMs + (clamp(right) / width) * (toMs - fromMs);
    if (!(endMs > startMs)) {
        return null;
    }
    return {
        since: toExplorerIso(new Date(startMs)),
        until: toExplorerIso(new Date(endMs)),
    };
}
