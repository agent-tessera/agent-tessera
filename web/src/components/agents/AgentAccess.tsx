import { useQuery } from "@tanstack/react-query";

import {
    fetchAccessFootprint,
    type AccessFootprintPattern,
    type AccessFootprintResource,
} from "@/api/agents";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { cn } from "@/lib/utils";

export type AgentAccessProps = {
    agentId: string;
};

/**
 * Access footprint tab: declared / observed / unused / denied grants.
 */
export function AgentAccess({ agentId }: AgentAccessProps) {
    const query = useQuery({
        queryKey: ["agents", "access-footprint", agentId],
        queryFn: () => fetchAccessFootprint(agentId),
    });

    if (query.isError) {
        return (
            <ErrorState
                title="Could not load access footprint"
                message={
                    query.error instanceof Error
                        ? query.error.message
                        : "Request failed"
                }
                onRetry={() => void query.refetch()}
            />
        );
    }

    if (query.isPending || !query.data) {
        return (
            <p
                className="text-muted-foreground text-sm"
                data-testid="agent-access-loading"
            >
                Loading…
            </p>
        );
    }

    const { declared, observed, unused, denied } = query.data;
    const empty =
        declared.length === 0 &&
        observed.length === 0 &&
        unused.length === 0 &&
        denied.length === 0;

    if (empty) {
        return (
            <EmptyState
                title="No access footprint yet"
                body="Upload a policy and let the agent run. Declared grants are compared with the last 30 days of events."
                className="border-border bg-panel rounded-md border py-8"
            />
        );
    }

    return (
        <div className="grid gap-4 lg:grid-cols-2" data-testid="agent-access">
            <PatternSection
                title="Declared"
                testId="access-declared"
                empty="No declared grants in the effective policy."
                items={declared}
                tone="neutral"
            />
            <ResourceSection
                title="Observed"
                testId="access-observed"
                empty="No allowed resources observed in the last 30 days."
                items={observed}
                tone="ok"
            />
            <PatternSection
                title="Unused"
                testId="access-unused"
                empty="Every declared grant was used."
                items={unused}
                tone="warn"
            />
            <ResourceSection
                title="Denied"
                testId="access-denied"
                empty="No denials recorded in the last 30 days."
                items={denied}
                tone="deny"
            />
        </div>
    );
}

type Tone = "neutral" | "ok" | "warn" | "deny";

const TONE_BADGE: Record<Tone, string> = {
    neutral: "bg-muted text-muted-foreground border-border",
    ok: "bg-status-ok-bg text-status-ok-fg border-status-ok/40",
    warn: "bg-status-running-bg text-status-running-fg border-status-running/40",
    deny: "bg-effect-deny-bg text-effect-deny-fg border-effect-deny/40",
};

function CountBadge({ count, tone }: { count: number; tone: Tone }) {
    return (
        <span
            className={cn(
                "inline-flex min-w-5 items-center justify-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium tabular-nums",
                TONE_BADGE[tone],
            )}
        >
            {count}
        </span>
    );
}

function KindChip({ kind }: { kind: string }) {
    return (
        <span className="border-border bg-muted text-muted-foreground inline-flex rounded-md border px-1.5 py-0.5 text-[10px] tracking-wide uppercase">
            {kind}
        </span>
    );
}

function PatternSection({
    title,
    testId,
    empty,
    items,
    tone,
}: {
    title: string;
    testId: string;
    empty: string;
    items: AccessFootprintPattern[];
    tone: Tone;
}) {
    return (
        <section
            className="border-border bg-panel space-y-3 rounded-md border p-4"
            data-testid={testId}
            aria-labelledby={`${testId}-h`}
        >
            <div className="flex items-center justify-between gap-2">
                <h2
                    id={`${testId}-h`}
                    className="text-foreground text-sm font-semibold"
                >
                    {title}
                </h2>
                <CountBadge count={items.length} tone={tone} />
            </div>
            {items.length === 0 ? (
                <p className="text-muted-foreground text-sm">{empty}</p>
            ) : (
                <ul className="divide-border divide-y">
                    {items.map((item) => (
                        <li
                            key={`${item.kind}:${item.pattern}:${item.rule ?? ""}`}
                            className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0"
                        >
                            <span className="font-mono text-xs">{item.pattern}</span>
                            <span className="flex items-center gap-2">
                                <KindChip kind={item.kind} />
                                {item.rule ? (
                                    <span className="text-muted-foreground max-w-40 truncate text-[11px]">
                                        {item.rule}
                                    </span>
                                ) : null}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}

function ResourceSection({
    title,
    testId,
    empty,
    items,
    tone,
}: {
    title: string;
    testId: string;
    empty: string;
    items: AccessFootprintResource[];
    tone: Tone;
}) {
    return (
        <section
            className="border-border bg-panel space-y-3 rounded-md border p-4"
            data-testid={testId}
            aria-labelledby={`${testId}-h`}
        >
            <div className="flex items-center justify-between gap-2">
                <h2
                    id={`${testId}-h`}
                    className="text-foreground text-sm font-semibold"
                >
                    {title}
                </h2>
                <CountBadge count={items.length} tone={tone} />
            </div>
            {items.length === 0 ? (
                <p className="text-muted-foreground text-sm">{empty}</p>
            ) : (
                <ul className="divide-border divide-y">
                    {items.map((item) => (
                        <li
                            key={`${item.kind}:${item.resource}`}
                            className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0"
                        >
                            <span className="font-mono text-xs">{item.resource}</span>
                            <span className="flex items-center gap-2">
                                <KindChip kind={item.kind} />
                                <span className="text-muted-foreground text-xs tabular-nums">
                                    ×{item.count}
                                </span>
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}
