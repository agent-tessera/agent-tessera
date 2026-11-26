import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { fetchEvents, type EventItem } from "@/api/events";
import { eventsKeys } from "@/api/keys";
import { Button } from "@/components/ui/button";
import { Mono } from "@/components/Mono";
import { runIdFromEvent } from "@/lib/welcome";

export type WelcomeDoneStepProps = {
    agentId: string;
    agentName: string;
};

const POLL_MS = 2000;

/**
 * Step 4: poll until the agent's first event appears, then link its trace.
 */
export function WelcomeDoneStep({ agentId, agentName }: WelcomeDoneStepProps) {
    const eventsQuery = useQuery({
        queryKey: eventsKeys.list({ agent: [agentId], limit: 1, welcome: true }),
        queryFn: () => fetchEvents({ agent: [agentId], limit: 1 }),
        refetchInterval: (query) => {
            const items = query.state.data?.items ?? [];
            return items.length > 0 ? false : POLL_MS;
        },
    });

    const first: EventItem | undefined = eventsQuery.data?.items[0];
    const runId = first ? runIdFromEvent(first) : null;
    const spanId =
        first && typeof first.span_id === "string" && first.span_id
            ? first.span_id
            : undefined;

    return (
        <div className="space-y-4" data-testid="welcome-done-step">
            <div className="space-y-1">
                <h2 className="text-sm font-semibold">First event</h2>
                <p className="text-muted-foreground text-sm">
                    Waiting for {agentName} to upload its first governance event.
                </p>
            </div>

            {first && runId ? (
                <div
                    className="border-border bg-panel space-y-3 rounded-md border p-4"
                    data-testid="welcome-first-event"
                    role="status"
                >
                    <p className="text-sm">
                        Events are flowing. Open the first event&apos;s governance
                        trace.
                    </p>
                    <p className="text-muted-foreground text-sm">
                        Event <Mono>{first.id}</Mono>
                        {typeof first.resource === "string" && first.resource
                            ? ` · ${first.resource}`
                            : null}
                    </p>
                    <Button asChild size="sm">
                        <Link
                            to="/traces/$runId"
                            params={{ runId }}
                            search={spanId ? { span: spanId } : undefined}
                            data-testid="welcome-trace-link"
                        >
                            Open trace
                        </Link>
                    </Button>
                </div>
            ) : (
                <p
                    className="text-muted-foreground text-sm"
                    data-testid="welcome-waiting-event"
                    aria-live="polite"
                >
                    {eventsQuery.isPending
                        ? "Checking for events…"
                        : "Waiting for the first uploaded event…"}
                </p>
            )}

            <Button asChild size="sm" variant="outline">
                <Link
                    to="/agents/$agentId"
                    params={{ agentId }}
                    data-testid="welcome-skip-agent"
                >
                    Skip to agent page
                </Link>
            </Button>
        </div>
    );
}
