import type { TraceUnparentedEvent } from "@/api/runs";
import { EffectBadge, type EffectKind } from "@/components/EffectBadge";
import { Mono } from "@/components/Mono";
import { RelativeTime } from "@/components/RelativeTime";
import { StatusBadge, type StatusKind } from "@/components/StatusBadge";

export type UnparentedListProps = {
    events: TraceUnparentedEvent[];
};

const EFFECTS = new Set<EffectKind>(["allow", "deny", "require_approval"]);
const STATUSES = new Set<StatusKind>(["ok", "error", "revoked", "running"]);

function asEffect(value: string | null | undefined): EffectKind | null {
    if (!value || !EFFECTS.has(value as EffectKind)) {
        return null;
    }
    return value as EffectKind;
}

function asStatus(value: string | null | undefined): StatusKind | null {
    if (!value || !STATUSES.has(value as StatusKind)) {
        return null;
    }
    return value as StatusKind;
}

/**
 * Flat list of pre-0.6 events that lack a span id.
 */
export function UnparentedList({ events }: UnparentedListProps) {
    if (events.length === 0) {
        return null;
    }

    return (
        <section className="space-y-2" data-testid="trace-unparented">
            <div>
                <h2 className="text-sm font-semibold">Unparented events</h2>
                <p className="text-muted-foreground text-xs">
                    Sent by an SDK older than 0.6
                </p>
            </div>
            <ul className="border-border divide-border divide-y rounded-md border">
                {events.map((event) => {
                    const effect = asEffect(event.effect);
                    const status = asStatus(event.status);
                    return (
                        <li
                            key={event.id}
                            className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
                            data-testid="trace-unparented-row"
                        >
                            <RelativeTime value={event.ts} />
                            <span>{event.type}</span>
                            {event.resource ? <Mono>{event.resource}</Mono> : null}
                            {effect ? <EffectBadge effect={effect} /> : null}
                            {status ? <StatusBadge status={status} /> : null}
                            <span className="text-muted-foreground ml-auto">
                                <Mono>{event.id.slice(0, 8)}</Mono>
                            </span>
                        </li>
                    );
                })}
            </ul>
        </section>
    );
}
