import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

export type RelativeTimeProps = {
    /** Instant to display; Date or ISO / parseable string. */
    value: Date | string | number;
    className?: string;
};

function toDate(value: Date | string | number): Date {
    return value instanceof Date ? value : new Date(value);
}

function formatUtc(date: Date): string {
    return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function formatRelative(date: Date, now: Date): string {
    const deltaMs = date.getTime() - now.getTime();
    const absSec = Math.round(Math.abs(deltaMs) / 1000);
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    if (absSec < 60) {
        return rtf.format(Math.round(deltaMs / 1000), "second");
    }
    const absMin = Math.round(absSec / 60);
    if (absMin < 60) {
        return rtf.format(Math.sign(deltaMs) * absMin, "minute");
    }
    const absHour = Math.round(absMin / 60);
    if (absHour < 48) {
        return rtf.format(Math.sign(deltaMs) * absHour, "hour");
    }
    const absDay = Math.round(absHour / 24);
    if (absDay < 60) {
        return rtf.format(Math.sign(deltaMs) * absDay, "day");
    }
    const absMonth = Math.round(absDay / 30);
    if (absMonth < 24) {
        return rtf.format(Math.sign(deltaMs) * absMonth, "month");
    }
    return rtf.format(Math.sign(deltaMs) * Math.round(absDay / 365), "year");
}

/** Relative timestamp with absolute UTC available via the native tooltip. */
export function RelativeTime({ value, className }: RelativeTimeProps) {
    const date = toDate(value);
    const [now, setNow] = useState(() => new Date());

    useEffect(() => {
        const id = window.setInterval(() => setNow(new Date()), 30_000);
        return () => window.clearInterval(id);
    }, []);

    const utc = formatUtc(date);
    return (
        <time
            dateTime={date.toISOString()}
            title={utc}
            className={cn("tabular-nums", className)}
        >
            {formatRelative(date, now)}
        </time>
    );
}
